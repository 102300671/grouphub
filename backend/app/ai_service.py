"""AI 配置/会话的领域逻辑：用户侧 API 与机器人侧 API 共用。"""
from __future__ import annotations

from typing import List, Optional

from sqlalchemy.orm import Session

from . import models
from .models import utcnow


# ------------------ 配置 ------------------

def mask_key(key: Optional[str]) -> Optional[str]:
    """密钥打码：列表/详情统一只回打码值。"""
    if not key:
        return None
    if len(key) <= 8:
        return key[:2] + "****"
    return f"{key[:4]}****{key[-4:]}"


def get_builtin_config(db: Session) -> Optional[models.AIConfig]:
    """内置默认配置（owner_id IS NULL，至多一行）。"""
    return db.query(models.AIConfig).filter(models.AIConfig.owner_id.is_(None)).first()


def get_user_active_config(db: Session, user: models.User) -> Optional[models.AIConfig]:
    """用户当前选中的自建配置；没有则 None（调用方回退内置默认）。"""
    return (
        db.query(models.AIConfig)
        .filter(models.AIConfig.owner_id == user.id, models.AIConfig.is_active.is_(True))
        .first()
    )


def get_effective_config(
    db: Session, user: models.User
) -> tuple[int, models.AIConfig]:
    """返回 (config_id, config)：优先用户选中项，否则内置默认。

    内置默认尚未同步时返回 (0, None)。
    """
    active = get_user_active_config(db, user)
    if active is not None:
        return active.id, active
    return 0, get_builtin_config(db)


def config_to_out(cfg: models.AIConfig, *, is_builtin: bool = False) -> "dict":
    return {
        "id": 0 if is_builtin else cfg.id,
        "name": cfg.name,
        "kind": cfg.kind,
        "api_base": cfg.api_base,
        "api_key": mask_key(cfg.api_key),
        "model": cfg.model,
        "system_prompt": cfg.system_prompt,
        "searxng_url": cfg.searxng_url,
        "is_active": False if is_builtin else cfg.is_active,
        "is_builtin": is_builtin,
    }


def list_configs(db: Session, user: models.User) -> dict:
    """用户视角的配置列表（内置默认在前）+ 当前生效 id。"""
    items: List[dict] = []
    builtin = get_builtin_config(db)
    active_id = 0
    if builtin is not None:
        items.append(config_to_out(builtin, is_builtin=True))
    rows = (
        db.query(models.AIConfig)
        .filter(models.AIConfig.owner_id == user.id)
        .order_by(models.AIConfig.id)
        .all()
    )
    for row in rows:
        out = config_to_out(row)
        if row.is_active:
            active_id = row.id
            out["is_active"] = True
        items.append(out)
    return {"ok": True, "items": items, "active_id": active_id}


def set_active(db: Session, user: models.User, config_id: int) -> None:
    """切换用户生效配置：0 = 内置默认（清除所有选中标记）。"""
    rows = db.query(models.AIConfig).filter(models.AIConfig.owner_id == user.id).all()
    target = None
    for row in rows:
        row.is_active = row.id == config_id
        if row.id == config_id:
            target = row
    if config_id != 0 and target is None:
        raise ValueError("配置不存在")


# ------------------ 会话 ------------------

def conversation_to_out(conv: models.AIConversation) -> dict:
    last = conv.messages[-1].content if conv.messages else None
    if last and len(last) > 120:
        last = last[:119] + "…"
    return {
        "id": conv.id,
        "title": conv.title,
        "source": conv.source,
        "group_id": conv.group_id,
        "config_id": conv.config_id,
        "archived": conv.archived_at is not None,
        "created_at": conv.created_at,
        "updated_at": conv.updated_at,
        "last_message": last,
    }


def list_conversations(db: Session, user: models.User) -> List[dict]:
    rows = (
        db.query(models.AIConversation)
        .filter(models.AIConversation.owner_id == user.id)
        .order_by(models.AIConversation.updated_at.desc())
        .all()
    )
    return [conversation_to_out(c) for c in rows]


def get_owned_conversation(
    db: Session, user: models.User, conversation_id: int
) -> Optional[models.AIConversation]:
    return (
        db.query(models.AIConversation)
        .filter(
            models.AIConversation.id == conversation_id,
            models.AIConversation.owner_id == user.id,
        )
        .first()
    )


def get_active_group_conversation(
    db: Session, user: models.User, group_id: str
) -> Optional[models.AIConversation]:
    """该用户在某群的当前（未归档）AI 会话。"""
    return (
        db.query(models.AIConversation)
        .filter(
            models.AIConversation.owner_id == user.id,
            models.AIConversation.source == "group",
            models.AIConversation.group_id == group_id,
            models.AIConversation.archived_at.is_(None),
        )
        .first()
    )


def touch(conv: models.AIConversation) -> None:
    conv.updated_at = utcnow()


def recent_text_messages(
    conv: models.AIConversation, limit: int = 20
) -> List[dict]:
    """取最近若干条 user/assistant 消息（按时间正序返回）。"""
    msgs = conv.messages[-limit:] if limit else conv.messages
    return [{"role": m.role, "content": m.content} for m in msgs]
