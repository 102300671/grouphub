"""插件 #2：auth_code —— 注册绑定码核销 + 登录验证码通道。

== 注册绑定（主流程，方向反转：用户发给 bot） ==
  用户站点注册：输入 QQ 号 + 密码 → 站点生成绑定码并直接展示在页面
    → 用户在 QQ 里把码发给机器人（群内 @机器人「/绑定 -c <码>」或私聊）
      命令实现见 commands/auth.py；本模块只保留 HTTP 路由
    → 本插件调后端 POST /bot/auth/verify-register（X-Bot-Token 鉴权）：
        验码 → QQ 加入白名单 → 绑定 QQ 官方 openid ↔ 真实 QQ
    → bot 回复验证结果；之后用户即可用 QQ 号 + 密码正常登录
  说明：QQ 官方适配器拿不到真实 QQ 号（只有 openid）也无群成员列表 API，
  注册绑定一步同时解决「官方通道加白名单」和「openid ↔ QQ 映射」两个问题。
  OneBot v11 群消息会 best-effort 撤回用户发的验证码，避免被群友看到。

== 登录验证码（旧通道，依赖 OneBot 私聊） ==
  用户站点填 QQ → POST /auth/send-code
    → 站点生成 code 存 verification_codes
    → 站点反向调本插件 POST /bot/auth/send-code（X-Bot-Token 鉴权）
    → bot 按真实 QQ 号私聊发码（OneBot v11 send_private_msg；
      QQ 官方适配器拿不到 openid 对应关系且单聊主动消息受限，仅作兜底尝试）
  用户回填 code → /auth/confirm-code 成功
    → 站点 best-effort 反向调 POST /bot/auth/notify-login（本插件仅记录日志）

依赖：
  - .env 的 DRIVER 需包含 ~fastapi（如 `~fastapi+~websockets`）
  - 安装 fastapi 与 uvicorn：pip install fastapi uvicorn
  缺少以上依赖时本插件静默降级（仅告警一次），不影响其他插件。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from nonebot import get_driver, logger

from ._lib.bots import send_private

_ENV_PATH = Path(__file__).resolve().parents[1] / ".env.prod"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH, override=False)

BOT_API_TOKEN = os.getenv("BOT_API_TOKEN", "")

def _msg_for_code(qq: str, code: str) -> str:
    return f"[群资源站] 你的登录验证码：{code}（10 分钟内有效，请勿泄露给他人）"


def _register_routes() -> bool:
    """在 fastapi driver 的 app 上注册 /bot/auth/* 路由。失败返回 False。"""
    try:
        driver = get_driver()
        if not (hasattr(driver, "asgi") and hasattr(driver, "server_app")):
            logger.warning(
                "[auth_code] 当前 driver 不是 fastapi（缺少 asgi/server_app），跳过注册 /bot/auth/*。"
                "请确认 .env 的 DRIVER 包含 ~fastapi。"
            )
            return False
        app = driver.asgi
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"[auth_code] 注册路由失败（{exc}）。请确认：1) .env 的 DRIVER 包含 ~fastapi  2) 已 pip install fastapi uvicorn"
        )
        return False

    router = APIRouter()

    def _check_token(request: Request) -> bool:
        # fail-closed：未配置 BOT_API_TOKEN 或 token 不匹配都拒绝
        return bool(BOT_API_TOKEN) and request.headers.get("X-Bot-Token") == BOT_API_TOKEN

    @router.post("/bot/auth/send-code")
    async def send_code(request: Request) -> Any:
        if not _check_token(request):
            return JSONResponse(status_code=401, content={"ok": False, "message": "bad bot token"})
        data = await request.json()
        qq = str(data.get("qq") or "").strip()
        code = str(data.get("code") or "").strip()
        if not qq or not code:
            return {"ok": False, "message": "qq/code required"}

        result = await send_private(qq, _msg_for_code(qq, code))
        if result.get("ok"):
            logger.info(f"[auth_code] 验证码已私聊下发：qq={qq} via={result.get('via')}")
            return {"ok": True, "message": "sent", "via": result.get("via")}
        logger.error(f"[auth_code] 私聊发码失败：qq={qq} err={result.get('message')}")
        return {"ok": False, "message": result.get("message", "send failed")}

    @router.post("/bot/auth/notify-login")
    async def notify_login(request: Request) -> Any:
        if not _check_token(request):
            return JSONResponse(status_code=401, content={"ok": False, "message": "bad bot token"})
        data = await request.json()
        qq = str(data.get("qq") or "").strip()
        login_at = data.get("login_at")
        ip = data.get("ip")
        logger.info(f"[auth_code] 登录通知：qq={qq} at={login_at} ip={ip}")
        # MVP 仅记录；后续可在此做群内播报 / 异常 IP 告警
        return {"ok": True, "message": "acked"}

    app.include_router(router)
    return True


_registered = _register_routes()
