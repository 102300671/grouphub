"""QQ 端对共享包（shared/aitools）的宿主能力实现。

共享包（仓库根 shared/aitools）不认识 nonebot / QQ 适配器 / backend HTTP 客户端；
这里把 QQ 侧的能力实现成 ports 里的协议，注入给 build_agent()。

对应关系：
  搜索    → shared.aitools.tools.web.SearXNGSearch（SearXNG 协议本身也在共享包里）
  渲染    → nonebot-plugin-htmlrender + playwright（_lib/renderer.py）
  作品库  → backend 的 /bot/works/* HTTP 接口（_lib/client.py）
  发图    → QQ 适配器（_lib/renderer.py 的 send_image）
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from nonebot import logger
from nonebot.adapters import Bot, Event

# 仓库根加入 sys.path：shared/aitools 放在仓库根，两端共用（不做 pip 安装，
# 部署时云端只需 git pull + 重启）。bot.py 里也做同样的事，两处都幂等。
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.aitools import Agent, build_agent  # noqa: E402
from shared.aitools.ports import SearchBackend  # noqa: E402
from shared.aitools.registry import (  # noqa: E402
    Context,
    ImageArtifact,
    SubmitResult,
)

from .client import backend_client  # noqa: E402
from .renderer import (  # noqa: E402
    render_html_img,
    render_md_img,
    render_text_img,
    screenshot_url,
    send_image,
)

# .env.prod（各命令模块也各自 load 一次，override=False 不会互相覆盖）
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env.prod"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH, override=False)

# 站点地址：作品详情链接前缀（AI 工具给绝对链接，Web 端给相对链接）
SITE_BASE_URL = os.getenv("SITE_BASE_URL", "http://127.0.0.1:5173").rstrip("/")


# ------------------ 日志桥接 ------------------
# 共享包只用标准库 logging（不能依赖 nonebot），这里转发到 loguru，
# 让共享包的日志与其它模块的格式、级别一致。

class _LoguruBridge(logging.Handler):
    """把标准库 logging 的记录转发到 nonebot 的 loguru。"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:  # pragma: no cover - 极端级别名
            level = record.levelname
        logger.opt(exception=record.exc_info).log(level, record.getMessage())


def _bridge_shared_logger() -> None:
    shared_logger = logging.getLogger("shared.aitools")
    if getattr(shared_logger, "_gh_bridged", False):
        return
    shared_logger.setLevel(logging.INFO)
    shared_logger.addHandler(_LoguruBridge())
    shared_logger._gh_bridged = True  # type: ignore[attr-defined]


_bridge_shared_logger()


# ------------------ 端口实现 ------------------

class QQRenderer:
    """渲染端口：转 nonebot-plugin-htmlrender（网页截图走 playwright 直连）。"""

    async def render_text(self, content: str, *, width: int) -> bytes:
        return await render_text_img(content, width=width)

    async def render_markdown(self, content: str, *, width: int) -> bytes:
        return await render_md_img(content, width=width)

    async def render_html(self, content: str, *, width: int) -> bytes:
        return await render_html_img(content, width=width)

    async def screenshot(self, url: str) -> bytes:
        return await screenshot_url(url)


class QQSiteDAO:
    """站内作品库端口：走 backend 的 /bot/works/* HTTP 接口。"""

    async def hot(self, limit: int) -> List[Dict[str, Any]]:
        resp = await backend_client.get("/bot/works/hot", params={"limit": limit})
        resp.raise_for_status()
        return resp.json().get("items", [])

    async def search(
        self, keyword: str, limit: int, type: Optional[str]
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"keyword": keyword, "limit": limit}
        if type:
            params["type"] = type
        resp = await backend_client.get("/bot/works/search", params=params)
        resp.raise_for_status()
        return resp.json().get("items", [])

    async def submit(self, payload: Dict[str, Any], ctx: Context) -> SubmitResult:
        qq = ctx.user_qq or ""
        body = dict(payload)
        # 特权模式：调用者即上传者，后端会校验其在站点有账号
        body["qq"] = qq
        body["uploader_qq"] = qq
        resp = await backend_client.post("/bot/works/submit", json=body)
        resp.raise_for_status()
        data = resp.json()
        return SubmitResult(
            ok=bool(data.get("ok")),
            duplicate=bool(data.get("duplicate")),
            work=data.get("work") or {},
            message=data.get("message"),
        )


class QQImageSink:
    """图片发送出口（per-request，随 Context 传下去）。

    失败不抛异常：回一条中文提示，模型如实告知用户即可。
    """

    def __init__(self, bot: Bot, event: Event) -> None:
        self._bot = bot
        self._event = event

    async def send(self, artifact: ImageArtifact) -> str:
        try:
            return await send_image(
                self._bot, self._event, artifact.bytes_, artifact.filename
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[ai render] 图片发送失败：{exc}")
            return "渲染成功但图片发送失败（详见日志），请改用文本回复用户。"


def build_qq_agent(*, search: SearchBackend, site_base_url: str = "") -> Agent:
    """装配 QQ 端的 Agent（共享包 + QQ 侧端口实现）。"""
    return build_agent(
        search=search,
        renderer=QQRenderer(),
        site=QQSiteDAO(),
        site_base_url=site_base_url,
    )
