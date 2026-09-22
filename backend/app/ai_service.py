"""AI 配置/会话的领域逻辑：用户侧 API 与机器人侧 API 共用。"""
from __future__ import annotations

import json
from typing import Any, List, Optional

import httpx
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
    """内置主默认配置：name=默认配置 优先，否则 id 最小（向后兼容）。"""
    row = (
        db.query(models.AIConfig)
        .filter(models.AIConfig.owner_id.is_(None), models.AIConfig.name == "默认配置")
        .first()
    )
    if row is not None:
        return row
    return (
        db.query(models.AIConfig)
        .filter(models.AIConfig.owner_id.is_(None))
        .order_by(models.AIConfig.id)
        .first()
    )


def _active_selection(db: Session, user_id: int) -> Optional[models.AIUserActiveConfig]:
    return (
        db.query(models.AIUserActiveConfig)
        .filter(models.AIUserActiveConfig.user_id == user_id)
        .first()
    )


def _config_visible_to_user(cfg: Optional[models.AIConfig], user: models.User) -> bool:
    """内置配置或该用户自己的配置才可见/可用。"""
    return cfg is not None and (cfg.owner_id is None or cfg.owner_id == user.id)


def get_effective_config(
    db: Session, user: models.User
) -> tuple[int, Optional[models.AIConfig]]:
    """返回 (config_id, config)：用户选中项（含内置配置）> 内置主默认；无则 (0, None)。"""
    sel = _active_selection(db, user.id)
    if sel is not None and sel.config_id != 0:
        cfg = db.query(models.AIConfig).filter(models.AIConfig.id == sel.config_id).first()
        if _config_visible_to_user(cfg, user):
            if cfg.owner_id is None and cfg.name == "默认配置":
                return 0, cfg  # 内置主默认行归一化为 0
            return cfg.id, cfg
    return 0, get_builtin_config(db)


def config_to_out(
    cfg: models.AIConfig, *, is_builtin: bool = False, active_id: int = 0
) -> "dict":
    """内置配置也输出真实 id（前端可直接点选激活）；active_id=0 表示内置主默认。"""
    if is_builtin:
        active = (
            cfg.name == "默认配置" and active_id == 0
        ) or (cfg.name != "默认配置" and cfg.id == active_id)
    else:
        active = cfg.id == active_id
    return {
        "id": cfg.id,
        "name": cfg.name,
        "kind": cfg.kind,
        "api_base": cfg.api_base,
        "api_key": mask_key(cfg.api_key),
        "model": cfg.model,
        "system_prompt": cfg.system_prompt,
        "searxng_url": cfg.searxng_url,
        "is_active": active,
        "is_builtin": is_builtin,
    }


def list_configs(db: Session, user: models.User) -> dict:
    """用户视角的配置列表（全部内置配置在前）+ 当前生效 id。"""
    items: List[dict] = []
    sel = _active_selection(db, user.id)
    active_id = 0
    if sel is not None:
        active_id = sel.config_id
        if active_id != 0:
            cfg = db.query(models.AIConfig).filter(models.AIConfig.id == active_id).first()
            if not _config_visible_to_user(cfg, user):
                active_id = 0  # 指向的配置已删除/失效 → 回退主默认
            elif cfg.owner_id is None and cfg.name == "默认配置":
                active_id = 0  # 内置主默认行归一化
    builtin_rows = (
        db.query(models.AIConfig)
        .filter(models.AIConfig.owner_id.is_(None))
        .order_by(models.AIConfig.id)
        .all()
    )
    for row in builtin_rows:
        items.append(config_to_out(row, is_builtin=True, active_id=active_id))
    own_rows = (
        db.query(models.AIConfig)
        .filter(models.AIConfig.owner_id == user.id)
        .order_by(models.AIConfig.id)
        .all()
    )
    for row in own_rows:
        items.append(config_to_out(row, active_id=active_id))
    return {"ok": True, "items": items, "active_id": active_id}


def set_active(db: Session, user: models.User, config_id: int) -> None:
    """切换用户生效配置：0 = 内置主默认；>0 指向内置配置或用户自己的配置。"""
    if config_id != 0:
        cfg = db.query(models.AIConfig).filter(models.AIConfig.id == config_id).first()
        if not _config_visible_to_user(cfg, user):
            raise ValueError("配置不存在")
        if cfg.owner_id is None and cfg.name == "默认配置":
            config_id = 0  # 内置主默认行归一化为 0
    sel = _active_selection(db, user.id)
    if sel is None:
        db.add(models.AIUserActiveConfig(user_id=user.id, config_id=config_id))
    else:
        sel.config_id = config_id


def upsert_builtin_configs(db: Session, configs: List[dict]) -> List[str]:
    """按 name upsert 内置配置（owner_id IS NULL）；name=默认配置 为主默认。

    返回本次生效的内置配置名列表。
    """
    names: List[str] = []
    for c in configs:
        name = (c.get("name") or "默认配置").strip() or "默认配置"
        row = (
            db.query(models.AIConfig)
            .filter(models.AIConfig.owner_id.is_(None), models.AIConfig.name == name)
            .first()
        )
        if row is None:
            row = models.AIConfig(owner_id=None, name=name)
            db.add(row)
        row.kind = "remote"
        row.api_base = (c.get("api_base") or "").rstrip("/") or None
        row.api_key = (c.get("api_key") or "").strip() or None
        row.model = (c.get("model") or "").strip() or None
        row.system_prompt = c.get("system_prompt")
        row.searxng_url = c.get("searxng_url")
        names.append(name)
    db.commit()
    return names


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
        "ai_group_id": conv.ai_group_id,
        "folder_id": conv.folder_id,
        "is_default": bool(conv.is_default),
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


def try_ai_title(
    db: Session,
    conv: models.AIConversation,
    question: str,
    answer: str,
    cfg_row: Optional[models.AIConfig] = None,
) -> None:
    """首轮对话完成后，用 AI 为会话生成简短标题并覆盖占位标题。

    无有效远程配置（缺密钥等）或调用失败时静默保留现有标题（截首句兜底）。
    """
    if cfg_row is None:
        _, cfg_row = get_effective_config(db, conv.owner)
    if cfg_row is None or cfg_row.kind == "local":
        return
    base = (cfg_row.api_base or "").rstrip("/")
    key = cfg_row.api_key or ""
    model = cfg_row.model
    if not base or not key or not model:
        return
    url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
    prompt = (
        "根据这段对话为用户会话起一个简短标题，只输出标题本身，20 字以内，不要引号、不要解释：\n"
        f"用户：{question[:200]}\n助手：{answer[:300]}"
    )
    try:
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 40,
            },
            timeout=20,
        )
        resp.raise_for_status()
        title = resp.json()["choices"][0]["message"]["content"]
        title = (title or "").strip().strip('"\u201c\u201d\u300c\u300d').replace("\n", " ")[:60]
        if title:
            conv.title = title
    except Exception:
        pass


def recent_text_messages(
    conv: models.AIConversation, limit: int = 20
) -> List[dict]:
    """取最近若干条 user/assistant 消息（按时间正序返回）。

    直接查库而不用 conv.messages 关系：messages 为 selectin 预加载，配合
    Session(expire_on_commit=False) 时，同一会话内刚插入的消息不会出现在
    已加载的集合缓存中（标量 FK 插入不会回填集合），会导致请求漏带最新消息。
    """
    from sqlalchemy.orm import object_session

    db = object_session(conv)
    query = (
        db.query(models.AIMessage)
        .filter(models.AIMessage.conversation_id == conv.id)
        .order_by(models.AIMessage.id)
    )
    msgs = query.all()
    if limit:
        msgs = msgs[-limit:]
    return [{"role": m.role, "content": m.content} for m in msgs]


# ------------------ 大组 / 分组（会话层级） ------------------


def get_qq_group(db: Session, user: models.User) -> Optional[models.AIGroup]:
    """该用户的 QQ 大组（按 QQ 号归并所有 openid 的会话）。"""
    return (
        db.query(models.AIGroup)
        .filter(
            models.AIGroup.owner_id == user.id,
            models.AIGroup.kind == "qq",
            models.AIGroup.qq == user.qq,
        )
        .first()
    )


def get_or_create_qq_group(db: Session, user: models.User) -> models.AIGroup:
    g = get_qq_group(db, user)
    if g is None:
        g = models.AIGroup(
            owner_id=user.id, kind="qq", qq=user.qq, name=f"QQ {user.qq}"
        )
        db.add(g)
        db.flush()
    return g


def get_web_group(db: Session, user: models.User) -> Optional[models.AIGroup]:
    """用户的前端大组（每用户一个，自动创建）。"""
    return (
        db.query(models.AIGroup)
        .filter(models.AIGroup.owner_id == user.id, models.AIGroup.kind == "web")
        .first()
    )


def get_or_create_web_group(db: Session, user: models.User) -> models.AIGroup:
    g = get_web_group(db, user)
    if g is None:
        g = models.AIGroup(owner_id=user.id, kind="web", name="我的空间")
        db.add(g)
        db.flush()
    return g


def get_folder_by_openid(
    db: Session, group: models.AIGroup, openid: str
) -> Optional[models.AIFolder]:
    return (
        db.query(models.AIFolder)
        .filter(
            models.AIFolder.group_id == group.id,
            models.AIFolder.openid == openid,
        )
        .first()
    )


def _binding_group_name(db: Session, openid: str) -> str:
    """按组定位键查绑定群名：群聊键=群 openid；私聊键=c2c:xxx（无群名）；OneBot=gb:xxx（无群名）。"""
    if not openid or openid.startswith(("c2c:", "gb:")):
        return ""
    b = (
        db.query(models.QQOpenidBinding)
        .filter(
            models.QQOpenidBinding.openid_type == "group",
            models.QQOpenidBinding.group_openid == openid,
        )
        .order_by(models.QQOpenidBinding.updated_at.desc())
        .first()
    )
    return (b.group_name or "").strip() if b is not None else ""
""


def get_or_create_openid_folder(
    db: Session, group: models.AIGroup, openid: str, name: Optional[str]
) -> models.AIFolder:
    """QQ 场景：openid 组。组名=群名/机器人名，随最新值更新。

    优先用传入的群名/机器人名；官方通道取不到群名（回退“群 xxx”或空）时，
    用绑定表记录的群名兜底，保证组名始终是真实群名。
    """
    bind_name = _binding_group_name(db, openid)
    f = get_folder_by_openid(db, group, openid)
    if f is None:
        eff = (name or "").strip()[:50]
        if bind_name and (not eff or eff.startswith("群 ")):
            eff = bind_name
        f = models.AIFolder(
            group_id=group.id,
            owner_id=group.owner_id,
            name=eff or "会话",
            openid=openid,
        )
        db.add(f)
        db.flush()
    else:
        new_name = (name or "").strip()[:50]
        if bind_name and (not new_name or new_name.startswith("群 ")):
            new_name = bind_name
        if new_name and f.name != new_name:
            f.name = new_name
    return f


def get_owned_group(db: Session, user: models.User, group_id: int) -> Optional[models.AIGroup]:
    return (
        db.query(models.AIGroup)
        .filter(models.AIGroup.id == group_id, models.AIGroup.owner_id == user.id)
        .first()
    )


def get_owned_folder(db: Session, user: models.User, folder_id: int) -> Optional[models.AIFolder]:
    return (
        db.query(models.AIFolder)
        .filter(models.AIFolder.id == folder_id, models.AIFolder.owner_id == user.id)
        .first()
    )


def get_default_conversation(
    db: Session, user: models.User, group: models.AIGroup, folder: Optional[models.AIFolder]
) -> Optional[models.AIConversation]:
    """组内默认（当前）会话：未归档且 is_default。"""
    q = (
        db.query(models.AIConversation)
        .filter(
            models.AIConversation.owner_id == user.id,
            models.AIConversation.ai_group_id == group.id,
            models.AIConversation.folder_id == (folder.id if folder else None),
            models.AIConversation.is_default.is_(True),
            models.AIConversation.archived_at.is_(None),
        )
        .first()
    )
    return q


def get_or_create_default_conversation(
    db: Session,
    user: models.User,
    group: models.AIGroup,
    folder: Optional[models.AIFolder],
    *,
    title: Optional[str] = None,
    source: Optional[str] = None,
) -> models.AIConversation:
    """取组内默认会话；没有则创建并设为默认。"""
    conv = get_default_conversation(db, user, group, folder)
    if conv is None:
        conv = models.AIConversation(
            owner_id=user.id,
            ai_group_id=group.id,
            folder_id=folder.id if folder else None,
            source=source or ("group" if group.kind == "qq" else "web"),
            title=(title or "").strip()[:255] or None,
            is_default=True,
        )
        db.add(conv)
        db.flush()
    return conv


def set_default_conversation(db: Session, user: models.User, conv: models.AIConversation) -> None:
    """把 conv 设为所在组（大组+组维度）的默认会话，清掉同组其它默认标记。"""
    db.query(models.AIConversation).filter(
        models.AIConversation.owner_id == user.id,
        models.AIConversation.ai_group_id == conv.ai_group_id,
        models.AIConversation.folder_id == conv.folder_id,
        models.AIConversation.is_default.is_(True),
        models.AIConversation.id != conv.id,
    ).update({"is_default": False}, synchronize_session=False)
    conv.is_default = True


def list_group_tree(db: Session, user: models.User) -> dict:
    """大组树：groups[{id, kind, name, qq, folders:[{id,name,openid,conversations}], conversations(未分组)}]。"""
    # 懒创建前端大组，保证网页端始终有会话空间（GET 内写库必须 commit，否则被会话关闭回滚）
    get_or_create_web_group(db, user)
    db.commit()
    groups = (
        db.query(models.AIGroup)
        .filter(models.AIGroup.owner_id == user.id)
        .order_by(models.AIGroup.id)
        .all()
    )
    convs = (
        db.query(models.AIConversation)
        .filter(models.AIConversation.owner_id == user.id)
        .order_by(models.AIConversation.updated_at.desc())
        .all()
    )
    out = []
    for g in groups:
        folders = (
            db.query(models.AIFolder)
            .filter(models.AIFolder.group_id == g.id)
            .order_by(models.AIFolder.id)
            .all()
        )
        g_convs = [c for c in convs if c.ai_group_id == g.id]
        folder_items = []
        for f in folders:
            folder_items.append(
                {
                    "id": f.id,
                    "name": f.name,
                    "openid": f.openid,
                    "conversations": [
                        conversation_to_out(c) for c in g_convs if c.folder_id == f.id
                    ],
                }
            )
        out.append(
            {
                "id": g.id,
                "kind": g.kind,
                "name": g.name,
                "qq": g.qq,
                "folders": folder_items,
                "conversations": [
                    conversation_to_out(c) for c in g_convs if c.folder_id is None
                ],
            }
        )
    return {"ok": True, "groups": out}


# ------------------ 思维链 / 工具链轨迹（agent_steps） ------------------
# 结构（与前端 AgentStep 对齐）：
# [{"reasoning": str, "calls": [
#     {"id": int, "kind": "activate"|"tool", "name": str,
#      "args": {...}, "raw": str,
#      "result": {"ok": bool, "summary": str, "content": str}}]}]
# 仅 assistant 消息携带；user 消息恒为 None。

def normalize_agent_steps(steps: Any) -> Optional[List[dict]]:
    """把任意入参（API 传入/内部收集）归一化为可落库的步骤列表；非法返回 None。"""
    if not isinstance(steps, list):
        return None
    out: List[dict] = []
    for raw_step in steps:
        if not isinstance(raw_step, dict):
            continue
        reasoning = str(raw_step.get("reasoning") or "")
        calls_out: List[dict] = []
        for raw_call in raw_step.get("calls") or []:
            if not isinstance(raw_call, dict):
                continue
            kind = raw_call.get("kind")
            if kind not in ("activate", "tool"):
                continue
            call: dict = {
                "id": int(raw_call.get("id") or 0),
                "kind": kind,
                "name": str(raw_call.get("name") or ""),
                "args": raw_call.get("args")
                if isinstance(raw_call.get("args"), dict)
                else {},
                "raw": str(raw_call.get("raw") or ""),
            }
            result = raw_call.get("result")
            if isinstance(result, dict):
                call["result"] = {
                    "ok": bool(result.get("ok")),
                    "summary": str(result.get("summary") or ""),
                    "content": str(result.get("content") or ""),
                }
            calls_out.append(call)
        # 空步骤（既无思考也无调用）不保留
        if not reasoning.strip() and not calls_out:
            continue
        out.append({"reasoning": reasoning, "calls": calls_out})
    return out or None


def dump_agent_steps(steps: Any) -> Optional[str]:
    """归一化后序列化为 DB 文本；无有效步骤返回 None。"""
    normalized = normalize_agent_steps(steps)
    if normalized is None:
        return None
    return json.dumps(normalized, ensure_ascii=False)


def load_agent_steps(raw: Any) -> Optional[List[dict]]:
    """读取 DB 文本并解析；空/损坏返回 None。"""
    if not raw or not isinstance(raw, str):
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return normalize_agent_steps(data)
