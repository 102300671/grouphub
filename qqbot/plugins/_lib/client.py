"""插件通用：封装对 backend 内部 API（/bot/*）的 HTTP 调用。

- 自动注入 X-Bot-Token 请求头。
- 统一超时、错误日志、重试（2 次）。
- 每个插件通过 ``from plugins._lib.client import client`` 拿到单例 httpx.AsyncClient。

示例::

    from plugins._lib.client import backend_client
    resp = await backend_client.post("/bot/members/upsert_one", json={...})
    resp.raise_for_status()
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import httpx
from dotenv import load_dotenv
from nonebot import logger

# 插件运行时和 CLI 直接跑都能读到配置
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env.prod"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH, override=False)


BACKEND_API_BASE = os.getenv("BACKEND_API_BASE", "http://127.0.0.1:8003").rstrip("/")
BOT_API_TOKEN = os.getenv("BOT_API_TOKEN", "")

if not BOT_API_TOKEN:
    logger.warning(
        "[plugins/_lib/client] 未配置 BOT_API_TOKEN。请在 qqbot/.env.prod 中填写，且与 backend/.env 的 BOT_API_TOKEN 一致。"
    )


class BackendClient(httpx.AsyncClient):
    """包装 httpx.AsyncClient，统一 X-Bot-Token 鉴权和错误日志。"""

    def __init__(self) -> None:
        headers = {
            "X-Bot-Token": BOT_API_TOKEN,
            "User-Agent": "library-qqbot-bot/0.1-mvp",
        }
        super().__init__(
            base_url=BACKEND_API_BASE,
            headers=headers,
            timeout=httpx.Timeout(15.0, connect=5.0),
        )

    async def request(self, method: str, url: httpx.URLTypes, **kwargs: Any) -> httpx.Response:  # type: ignore[override]
        last_err: Optional[BaseException] = None
        for attempt in range(1, 4):
            try:
                resp = await super().request(method, url, **kwargs)
                if resp.status_code >= 500 and attempt < 3:
                    logger.warning(
                        f"[backend 调用 5xx 重试 {attempt}/2] {method} {url} -> {resp.status_code}"
                    )
                    continue
                return resp
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_err = exc
                if attempt < 3:
                    logger.warning(f"[backend 调用重试 {attempt}/2] {method} {url}: {exc}")
                    continue
                raise
        # 理论上不会走到这里
        if last_err:
            raise last_err
        raise RuntimeError("backend request failed without exception")


# 全局单例（插件共享；连接池复用）
backend_client = BackendClient()


__all__ = ["backend_client", "BACKEND_API_BASE", "BOT_API_TOKEN"]
