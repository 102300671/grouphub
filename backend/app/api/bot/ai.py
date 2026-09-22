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
        raise HTTPException(
            status_code=404,
            detail="该 QQ 尚未绑定站点账号，请先在网页端注册并绑定 QQ",
        )
    return user


# =================== 内置默认配置同步 ===================

@router.post("/default", response_model=schemas.SimpleMessageOut)
def upsert_defaults(
    payload: schemas.AIBuiltinSyncListIn,
    db: Session = Depends(get_db),
    _: BotAuthenticated = Depends(),
):
    """机器人启动 / 管理员改配置后：按 name upsert 多套内置配置（owner_id NULL）。

    .env.prod 可写多套（AI_* 主默认 + AI_BUILTIN_CONFIGS 数组），全部同步为
    内置配置；name=默认配置 为主默认。所有用户可见，且可被每个用户独立选中。
    """
    configs = [
        {
            "name": c.name,
            "api_base": c.api_base,
            "api_key": c.api_key,
            "model": c.model,
            "system_prompt": c.system_prompt,
            "searxng_url": c.searxng_url,
        }
        for c in payload.configs
    ]
    names = ai_service.upsert_builtin_configs(db, configs)
    return schemas.SimpleMessageOut(message="ok", details={"configs": names})


# =================== 用户配置解析 ===================

@router.get("/active", response_model=schemas.AIActiveConfigOut)
def get_active(
    qq: str,
    db: Session = Depends(get_db),
    _: BotAuthenticated = Depends(),
):
    """返回该 QQ 的生效配置（含真实密钥）：用户选中项（含内置配置）> 内置主默认。"""
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
    """获取（或创建）该用户在 openid 组的当前（默认）AI 会话，附带最近消息。

    openid 缺失（OneBot 通道）时用 gb:<group_id> 作为组定位键；
    组/默认会话不存在会自动创建。
    """
    user = _user_by_qq(db, payload.qq)
    group = ai_service.get_or_create_qq_group(db, user)
    key = (payload.openid or "").strip() or f"gb:{payload.group_id}"
    folder = ai_service.get_or_create_openid_folder(
        db, group, key, payload.folder_name or f"群 {payload.group_id}"
    )
    if payload.force_new:
        cur = ai_service.get_default_conversation(db, user, group, folder)
        if cur is not None:
            cur.archived_at = utcnow()
            db.flush()  # SessionLocal autoflush=False：归档后立即可见
    conv = ai_service.get_or_create_default_conversation(
        db, user, group, folder, title=payload.title, source="group"
    )
    conv.group_id = payload.group_id or conv.group_id
    db.commit()
    db.refresh(conv)
    return schemas.SimpleMessageOut(
        message="ok",
        details={
            "conversation_id": conv.id,
            "title": conv.title,
            "messages": ai_service.recent_text_messages(conv, _RECENT_LIMIT),
        },
    )


@router.get("/conversations", response_model=schemas.SimpleMessageOut)
def list_conversations(
    qq: str,
    scope: str = "current",
    openid: Optional[str] = None,
    db: Session = Depends(get_db),
    _: BotAuthenticated = Depends(),
):
    """列会话。scope=current（默认，当前 openid 组）| all（QQ 大组全部）| web（前端大组）。"""
    user = _user_by_qq(db, qq)

    def _conv_out(c: models.AIConversation, folder_name: Optional[str]) -> dict:
        last = c.messages[-1].content if c.messages else None
        if last and len(last) > 120:
            last = last[:119] + "…"
        return {
            "id": c.id,
            "title": c.title,
            "source": c.source,
            "folder_id": c.folder_id,
            "folder_name": folder_name,
            "is_default": bool(c.is_default),
            "last_message": last,
            "updated_at": c.updated_at,
        }

    items: List[dict] = []
    current_id: Optional[int] = None
    if scope == "web":
        group = ai_service.get_or_create_web_group(db, user)
        db.commit()
        folders = {
            f.id: f.name
            for f in (
                db.query(models.AIFolder)
                .filter(models.AIFolder.group_id == group.id)
                .all()
            )
        }
        rows = (
            db.query(models.AIConversation)
            .filter(
                models.AIConversation.owner_id == user.id,
                models.AIConversation.ai_group_id == group.id,
            )
            .order_by(models.AIConversation.updated_at.desc())
            .all()
        )
        items = [_conv_out(c, folders.get(c.folder_id)) for c in rows]
        current_id = next(
            (c.id for c in rows if c.is_default and c.folder_id is None), None
        )
    elif scope == "all":
        group = ai_service.get_or_create_qq_group(db, user)
        folders = {
            f.id: f.name
            for f in (
                db.query(models.AIFolder)
                .filter(models.AIFolder.group_id == group.id)
                .all()
            )
        }
        rows = (
            db.query(models.AIConversation)
            .filter(
                models.AIConversation.owner_id == user.id,
                models.AIConversation.ai_group_id == group.id,
            )
            .order_by(models.AIConversation.updated_at.desc())
            .all()
        )
        items = [_conv_out(c, folders.get(c.folder_id)) for c in rows]
        current_id = next((c.id for c in rows if c.is_default), None)
    else:  # current
        group = ai_service.get_qq_group(db, user)
        if group is None:
            return schemas.SimpleMessageOut(
                message="ok", details={"items": [], "current_id": None}
            )
        folder = None
        if openid:
            folder = ai_service.get_folder_by_openid(db, group, openid)
        if folder is None:
            return schemas.SimpleMessageOut(
                message="ok", details={"items": [], "current_id": None}
            )
        rows = (
            db.query(models.AIConversation)
            .filter(
                models.AIConversation.owner_id == user.id,
                models.AIConversation.ai_group_id == group.id,
                models.AIConversation.folder_id == folder.id,
            )
            .order_by(models.AIConversation.updated_at.desc())
            .all()
        )
        items = [_conv_out(c, folder.name) for c in rows]
        current_id = next((c.id for c in rows if c.is_default), None)

    return schemas.SimpleMessageOut(
        message="ok",
        details={"items": items, "current_id": current_id},
    )


@router.post("/conversation/switch", response_model=schemas.SimpleMessageOut)
def switch_conversation(
    payload: schemas.AIBotSwitchIn,
    db: Session = Depends(get_db),
    _: BotAuthenticated = Depends(),
):
    """切换当前会话（设为所在组默认）。"""
    user = _user_by_qq(db, payload.qq)
    conv = ai_service.get_owned_conversation(db, user, payload.conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if conv.archived_at is not None:
        conv.archived_at = None  # 切到已归档会话时重新激活
    ai_service.set_default_conversation(db, user, conv)
    db.commit()
    return schemas.SimpleMessageOut(message="ok")


@router.post("/conversation/move", response_model=schemas.SimpleMessageOut)
def move_conversation(
    payload: schemas.AIBotMoveIn,
    db: Session = Depends(get_db),
    _: BotAuthenticated = Depends(),
):
    """把其它范围的会话移动到当前 openid 组并设为默认（继续会话）。"""
    user = _user_by_qq(db, payload.qq)
    conv = ai_service.get_owned_conversation(db, user, payload.conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    group = ai_service.get_or_create_qq_group(db, user)
    key = (payload.openid or "").strip() or f"gb:{conv.group_id or ''}"
    folder = ai_service.get_or_create_openid_folder(
        db, group, key, payload.folder_name or "会话"
    )
    conv.ai_group_id = group.id
    conv.folder_id = folder.id
    ai_service.set_default_conversation(db, user, conv)
    db.commit()
    return schemas.SimpleMessageOut(
        message="ok", details={"conversation_id": conv.id}
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
    first_round = (
        len(conv.messages) == 0
        and len(payload.messages) == 2
        and payload.messages[0].role == "user"
        and payload.messages[1].role == "assistant"
    )
    for item in payload.messages:
        db.add(
            models.AIMessage(
                conversation_id=conv.id,
                role=item.role,
                content=item.content,
                agent_steps=ai_service.dump_agent_steps(item.agent_steps)
                if item.role == "assistant"
                else None,
            )
        )
    if first_round:
        # 首轮完成：用 AI 生成会话标题；无密钥/失败时截首句兜底
        ai_service.try_ai_title(
            db, conv, payload.messages[0].content, payload.messages[1].content
        )
        if not conv.title:
            t = payload.messages[0].content.strip().splitlines()[0][:24]
            conv.title = t or '新对话'
    ai_service.touch(conv)
    db.commit()
    return schemas.SimpleMessageOut(message="ok")


@router.post("/conversation/delete", response_model=schemas.SimpleMessageOut)
def delete_conversation(
    payload: schemas.AIBotSwitchIn,
    db: Session = Depends(get_db),
    _: BotAuthenticated = Depends(),
):
    """删除会话（QQ 内 /ai 会话 删 <序号>；消息级联删除）。"""
    user = _user_by_qq(db, payload.qq)
    conv = ai_service.get_owned_conversation(db, user, payload.conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    db.delete(conv)
    db.commit()
    return schemas.SimpleMessageOut(message="已删除")


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
