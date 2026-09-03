"""/bot/auth/* —— nonebot2 插件（qqbot/plugins/auth_code.py）的内部回调端点。

当前 MVP：qqbot 插件侧的 `/bot/auth/send-code` / `/bot/auth/notify-login` 收到请求
就立即 200 返 ack（具体动作在 handler 内完成，例如下发验证码私聊），并没有反向
回调 backend。这两个端点保留下来作为「未来落审计日志 / 异常登录风控」的接缝，
目前只回 200。

所有路径都挂在 /bot/auth 前缀下（见 main.py），需要 X-Bot-Token 鉴权。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import models, schemas
from app.db import get_db
from app.security import BotAuthenticated, get_authenticated_bot

router = APIRouter(dependencies=[Depends(get_authenticated_bot)])


@router.post("/code-sent-ack", response_model=schemas.SimpleMessageOut)
def code_sent_ack(
    payload: schemas.BotSendCodeIn,
    db: Session = Depends(get_db),
    _: BotAuthenticated = Depends(),
) -> schemas.SimpleMessageOut:
    """qqbot 成功下发验证码后可选回调（当前 MVP 仅 ack，未来可写 audit_logs）。"""
    return schemas.SimpleMessageOut(
        ok=True,
        message="ack",
        details={
            "qq": payload.qq,
            "code_prefix": payload.code[:2] + "****",
            "at": datetime.now(timezone.utc).isoformat(),
        },
    )


@router.post("/login-notify-ack", response_model=schemas.SimpleMessageOut)
def login_notify_ack(
    payload: schemas.BotNotifyLoginIn,
    db: Session = Depends(get_db),
    _: BotAuthenticated = Depends(),
) -> schemas.SimpleMessageOut:
    """qqbot 收到「用户已登录」通知后的可选回调（MVP 占位）。"""
    return schemas.SimpleMessageOut(
        ok=True,
        message="ack",
        details={"qq": payload.qq, "login_at": payload.login_at.isoformat()},
    )
