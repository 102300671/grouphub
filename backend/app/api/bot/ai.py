"""/bot/ai/* —— nonebot2 机器人 AI 联动的内部 API（X-Bot-Token 鉴权）。

职责：
  - 接收机器人同步的内置默认远程配置（.env.prod 解析值）；
  - 按 QQ 解析用户当前生效配置（用户自建 BYOK 或内置默认，含真实密钥）；
  - 群内会话的获取/创建、历史消息读取、消息批量追加、重置归档。
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import ai_service, models, schemas
from app.db import get_db
from app.models import utcnow
from app.security import BotAuthenticated, get_authenticated_bot

router = APIRouter(dependencies=[Depends(get_authenticated_bot)])

# 群会话回灌给机器人的最近消息条数
_RECENT_LIMIT = 12


def _user_by_qq(db: Session, qq: str) -> models.User:
    user = db.query(models.User).filter(models.User.qq == qq).first()
    if user is None:
        raise HTTPException(status_code=404, detail="该 QQ 尚未注册")
    return user


# =================== 内置默认配置同步 ===================

@router.post("/default", response_model=schemas.SimpleMessageOut)
def upsert_default(
    payload: schemas.AIBuiltinSyncIn,
    db: Session = Depends(get_db),
    _: BotAuthenticated = Depends(),
):
    """机器人启动 / 管理员改配置后：upsert 内置默认配置（owner_id NULL）。"""
    cfg = ai_service.get_builtin_config(db)
    if cfg is None:
        cfg = models.AIConfig(owner_id=None, name="默认配置")
        db.add(cfg)
    cfg.kind = "remote"
    cfg.api_base = payload.api_base.rstrip("/")
    cfg.api_key = (payload.api_key or "").strip() or None
    cfg.model = payload.model
    cfg.system_prompt = payload.system_prompt
    cfg.searxng_url = payload.searxng_url
    db.commit()
    return schemas.SimpleMessageOut(
        message="ok", details={"config_id": cfg.id, "model": cfg.model}
    )


# =================== 用户配置解析 ===================

@router.get("/active", response_model=schemas.AIActiveConfigOut)
def get_active(
    qq: str,
    db: Session = Depends(get_db),
    _: BotAuthenticated = Depends(),
):
    """返回该 QQ 的生效配置（含真实密钥）：用户选中项 > 内置默认。"""
    user = _user_by_qq(db, qq)
    cfg_id, cfg = ai_service.get_effective_config(db, user)
    if cfg is None:
        # 内置默认未同步（机器人理论上启动即同步，此处兜底）
        return schemas.AIActiveConfigOut(config_id=0, kind="remote")
    return schemas.AIActiveConfigOut(
        config_id=cfg_id,
        kind=cfg.kind,
        api_base=cfg.api_base,
        api_key=cfg.api_key,
        model=cfg.model,
        system_prompt=cfg.system_prompt,
        searxng_url=cfg.searxng_url,
    )


@router.get("/configs", response_model=schemas.AIConfigListOut)
def list_configs(
    qq: str,
    db: Session = Depends(get_db),
    _: BotAuthenticated = Depends(),
):
    """列出该 QQ 可见配置（供群内 /ai 我的配置）。"""
    user = _user_by_qq(db, qq)
    return ai_service.list_configs(db, user)


@router.post("/activate", response_model=schemas.SimpleMessageOut)
def activate(
    payload: schemas.AIActivateIn,
    db: Session = Depends(get_db),
    _: BotAuthenticated = Depends(),
):
    """群内切换配置：config_id=0 切回内置默认。"""
    user = _user_by_qq(db, payload.qq)
    try:
        ai_service.set_active(db, user, payload.config_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="配置不存在")
    db.commit()
    return schemas.SimpleMessageOut(message="ok")


# =================== 群会话 ===================

@router.post("/conversation", response_model=schemas.SimpleMessageOut)
def get_or_create_conversation(
    payload: schemas.AIBotConversationIn,
    db: Session = Depends(get_db),
    _: BotAuthenticated = Depends(),
):
    """获取（或创建）该用户在某群的当前 AI 会话，附带最近消息。"""
    user = _user_by_qq(db, payload.qq)
    conv = ai_service.get_active_group_conversation(db, user, payload.group_id)
    if conv is None:
        conv = models.AIConversation(
            owner_id=user.id,
            source="group",
            group_id=payload.group_id,
            title=payload.title or f"群聊 · {payload.group_id}",
        )
        db.add(conv)
        db.commit()
        db.refresh(conv)
    elif payload.title and conv.title == f"群聊 · {payload.group_id}":
        conv.title = payload.title
        db.commit()
    return schemas.SimpleMessageOut(
        message="ok",
        details={
            "conversation_id": conv.id,
            "title": conv.title,
            "messages": ai_service.recent_text_messages(conv, _RECENT_LIMIT),
        },
    )


@router.post("/conversation/{conversation_id}/messages", response_model=schemas.SimpleMessageOut)
def append_messages(
    conversation_id: int,
    payload: schemas.AIBotMessagesIn,
    db: Session = Depends(get_db),
    _: BotAuthenticated = Depends(),
):
    """批量追加一轮（或多轮）消息，并校验会话归属。"""
    conv = ai_service.get_owned_conversation(
        db, _user_by_qq(db, payload.qq), conversation_id
    )
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    for item in payload.messages:
        db.add(
            models.AIMessage(
                conversation_id=conv.id, role=item.role, content=item.content
            )
        )
    ai_service.touch(conv)
    db.commit()
    return schemas.SimpleMessageOut(message="ok")


@router.post("/conversation/reset", response_model=schemas.SimpleMessageOut)
def reset_conversation(
    payload: schemas.AIBotResetIn,
    db: Session = Depends(get_db),
    _: BotAuthenticated = Depends(),
):
    """/ai 重置：归档当前群会话（旧会话保留，下次提问开新会话）。"""
    user = _user_by_qq(db, payload.qq)
    conv = ai_service.get_active_group_conversation(db, user, payload.group_id)
    if conv is None:
        return schemas.SimpleMessageOut(message="no-op: 无当前会话")
    conv.archived_at = utcnow()
    db.commit()
    return schemas.SimpleMessageOut(message="ok", details={"conversation_id": conv.id})
