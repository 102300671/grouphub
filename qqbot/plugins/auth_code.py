"""插件 #2：auth_code —— QQ 私聊验证码下发（站点反向回调）。

流程（对齐 PRD §6.3.2）：
  用户站点填 QQ → POST /auth/send-code
    → 站点生成 code 存 verification_codes
    → 站点反向调本插件 POST /bot/auth/send-code（X-Bot-Token 鉴权）
    → bot 用 send_private_msg 私聊发码
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
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from nonebot import get_bots, get_driver, logger
from nonebot.adapters import Bot

_ENV_PATH = Path(__file__).resolve().parents[1] / ".env.prod"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH, override=False)

BOT_API_TOKEN = os.getenv("BOT_API_TOKEN", "")


def _msg_for_code(qq: str, code: str) -> str:
    return f"[群资源站] 你的登录验证码：{code}（10 分钟内有效，请勿泄露给他人）"


def _find_bot():
    bots = get_bots()
    if not bots:
        return None
    return next(iter(bots.values()))


async def _send_private(bot: Bot, qq: str, message: str) -> None:
    """OneBot v11 发私聊。"""
    await bot.call_api("send_private_msg", user_id=int(qq) if qq.isdigit() else qq, message=message)


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

        bot = _find_bot()
        if bot is None:
            logger.error("[auth_code] 发码失败：当前没有已连接的 bot")
            return {"ok": False, "message": "no bot connected"}

        try:
            await _send_private(bot, qq, _msg_for_code(qq, code))
            logger.info(f"[auth_code] 验证码已私聊下发：qq={qq}")
            return {"ok": True, "message": "sent"}
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[auth_code] 私聊发码失败：qq={qq} err={exc}")
            return {"ok": False, "message": str(exc)}

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
