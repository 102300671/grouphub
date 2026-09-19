"""/ai/* —— 用户侧 AI 配置、会话与对话 API（JWT 鉴权）。

- 远程配置（含内置默认）：对话经本后端代理转发，密钥不出服务端；SSE 流式返回。
- 本地配置：由前端浏览器直连用户本地模型端点（走用户本地网络），消息通过
  /ai/conversations/{id}/messages 单独持久化。
"""
from __future__ import annotations

import json
from typing import AsyncGenerator, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import ai_service, models, schemas
from app.db import SessionLocal, get_db
from app.models import utcnow
from app.security import get_current_user

router = APIRouter()

# 网页端携带的最近消息条数；上游请求超时
_HISTORY_LIMIT = 20
_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


# =================== 配置 ===================

@router.get("/configs", response_model=schemas.AIConfigListOut)
def list_configs(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    return ai_service.list_configs(db, user)


@router.post("/configs", response_model=schemas.AIConfigOut)
def create_config(
    payload: schemas.AIConfigIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    cfg = models.AIConfig(
        owner_id=user.id,
        name=payload.name.strip(),
        kind=payload.kind,
        api_base=(payload.api_base or "").strip() or None,
        api_key=(payload.api_key or "").strip() or None,
        model=(payload.model or "").strip() or None,
        system_prompt=payload.system_prompt,
        is_active=False,
    )
    db.add(cfg)
    db.commit()
    db.refresh(cfg)
    return ai_service.config_to_out(cfg)


@router.patch("/configs/{config_id}", response_model=schemas.AIConfigOut)
def patch_config(
    config_id: int,
    payload: schemas.AIConfigPatchIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    cfg = _get_owned_config(db, user, config_id)
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        if key in ("name", "api_base", "api_key", "model"):
            value = (value or "").strip() or None
            if key == "name" and not value:
                raise HTTPException(status_code=400, detail="名称不能为空")
        setattr(cfg, key, value)
    db.commit()
    db.refresh(cfg)
    return ai_service.config_to_out(cfg)


@router.delete("/configs/{config_id}", response_model=schemas.SimpleMessageOut)
def delete_config(
    config_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    cfg = _get_owned_config(db, user, config_id)
    was_active = cfg.is_active
    db.delete(cfg)
    db.commit()
    return schemas.SimpleMessageOut(
        message="已删除", details={"was_active": was_active}
    )


@router.post("/configs/{config_id}/activate", response_model=schemas.AIConfigListOut)
def activate_config(
    config_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    try:
        ai_service.set_active(db, user, config_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="配置不存在")
    db.commit()
    return ai_service.list_configs(db, user)


@router.post("/configs/test", response_model=schemas.SimpleMessageOut)
async def test_config(
    payload: schemas.AIConfigTestIn,
    user: models.User = Depends(get_current_user),
):
    """测试远程端点连通性：GET {api_base}/models。"""
    base = payload.api_base.strip().rstrip("/")
    url = base if base.endswith("/models") else f"{base}/models"
    headers = {"Authorization": f"Bearer {payload.api_key}"} if payload.api_key else {}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise HTTPException(status_code=400, detail=f"连接失败：{exc}")
    if resp.status_code != 200:
        raise HTTPException(
            status_code=400,
            detail=f"接口返回 {resp.status_code}：{resp.text[:200]}",
        )
    models_list: List[str] = []
    try:
        models_list = [str(m.get("id")) for m in resp.json().get("data", []) if m.get("id")]
    except ValueError:
        pass
    return schemas.SimpleMessageOut(
        message="连接成功", details={"models": models_list[:20]}
    )


def _get_owned_config(
    db: Session, user: models.User, config_id: int
) -> models.AIConfig:
    cfg = (
        db.query(models.AIConfig)
        .filter(models.AIConfig.id == config_id, models.AIConfig.owner_id == user.id)
        .first()
    )
    if cfg is None:
        raise HTTPException(status_code=404, detail="配置不存在")
    return cfg


# =================== 会话 ===================

@router.get("/conversations", response_model=schemas.AIConversationListOut)
def list_conversations(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    return {"ok": True, "items": ai_service.list_conversations(db, user)}


@router.post("/conversations", response_model=schemas.AIConversationDetailOut)
def create_conversation(
    payload: schemas.AIConversationIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    conv = models.AIConversation(
        owner_id=user.id, source="web", title=payload.title
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return {
        "ok": True,
        "conversation": ai_service.conversation_to_out(conv),
        "messages": [],
    }


@router.get("/conversations/{conversation_id}", response_model=schemas.AIConversationDetailOut)
def get_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    conv = ai_service.get_owned_conversation(db, user, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    messages = [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "created_at": m.created_at,
        }
        for m in conv.messages
    ]
    return {
        "ok": True,
        "conversation": ai_service.conversation_to_out(conv),
        "messages": messages,
    }


@router.delete("/conversations/{conversation_id}", response_model=schemas.SimpleMessageOut)
def delete_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    conv = ai_service.get_owned_conversation(db, user, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if conv.source == "group":
        raise HTTPException(status_code=400, detail="群聊同步会话不可删除（可用群内 /ai 重置 归档）")
    db.delete(conv)
    db.commit()
    return schemas.SimpleMessageOut(message="已删除")


@router.post("/conversations/{conversation_id}/messages", response_model=schemas.SimpleMessageOut)
def append_message(
    conversation_id: int,
    payload: schemas.AIMessageIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """本地配置浏览器直连后，用它持久化单条消息。"""
    conv = ai_service.get_owned_conversation(db, user, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    db.add(
        models.AIMessage(
            conversation_id=conv.id, role=payload.role, content=payload.content
        )
    )
    _maybe_title(conv, payload)
    ai_service.touch(conv)
    db.commit()
    return schemas.SimpleMessageOut(message="ok")


# =================== 远程对话（SSE） ===================

@router.post("/chat")
def chat(
    payload: schemas.AIChatIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    conv = ai_service.get_owned_conversation(db, user, payload.conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if conv.source == "group":
        raise HTTPException(status_code=400, detail="群聊会话请在群内与机器人对话")

    # 解析配置：显式指定 > 用户选中 > 内置默认
    if payload.config_id:
        cfg_row = _get_owned_config(db, user, payload.config_id)
        cfg_id = cfg_row.id
    else:
        cfg_id, cfg_row = ai_service.get_effective_config(db, user)
    if cfg_row is None:
        raise HTTPException(
            status_code=503,
            detail="默认 AI 配置尚未就绪（等待机器人启动同步），请先新建自己的配置。",
        )
    if cfg_row.kind == "local":
        raise HTTPException(
            status_code=400,
            detail="本地配置走你的浏览器直连，请使用本地对话通道（不要走服务器代理）。",
        )
    base = (cfg_row.api_base or "").rstrip("/")
    key = cfg_row.api_key
    model = cfg_row.model
    if not base or not key or not model:
        raise HTTPException(
            status_code=400, detail="该配置不完整（端点 / 密钥 / 模型缺失），请补全后再试。"
        )

    # 先落用户消息
    db.add(
        models.AIMessage(conversation_id=conv.id, role="user", content=payload.content)
    )
    _maybe_title(conv, type("M", (), {"role": "user", "content": payload.content}))
    ai_service.touch(conv)
    db.commit()

    # 系统提示词：用户配置为空时回退内置默认的提示词
    system_prompt = cfg_row.system_prompt
    if not system_prompt:
        builtin = ai_service.get_builtin_config(db)
        system_prompt = builtin.system_prompt if builtin else None

    url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
    history = ai_service.recent_text_messages(conv, _HISTORY_LIMIT)
    request_messages: List[Dict[str, str]] = []
    if system_prompt:
        request_messages.append({"role": "system", "content": system_prompt})
    request_messages.extend(history)

    return_stream = _sse_response(
        url, key, model, request_messages, conv.id
    )
    from fastapi.responses import StreamingResponse

    return StreamingResponse(return_stream, media_type="text/event-stream")


async def _sse_response(
    url: str,
    key: str,
    model: str,
    request_messages: List[Dict[str, str]],
    conversation_id: int,
) -> AsyncGenerator[bytes, None]:
    """转发上游 SSE，转成前端统一事件格式，并在结束后落库 assistant 消息。"""

    def sse(event: dict) -> bytes:
        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")

    body = {"model": model, "messages": request_messages, "stream": True}
    headers = {"Authorization": f"Bearer {key}"}
    collected = ""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            async with client.stream(
                "POST", url, json=body, headers=headers
            ) as resp:
                if resp.status_code != 200:
                    text = (await resp.aread()).decode("utf-8", "ignore")[:300]
                    yield sse({"type": "error", "message": f"上游返回 {resp.status_code}：{text}"})
                    return
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        delta = chunk["choices"][0].get("delta", {})
                        piece = delta.get("content") or ""
                    except (ValueError, KeyError, IndexError):
                        continue
                    if piece:
                        collected += piece
                        yield sse({"type": "delta", "text": piece})
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        yield sse({"type": "error", "message": f"连接上游失败：{exc}"})
        return

    if not collected.strip():
        yield sse({"type": "error", "message": "模型返回了空内容。"})
        return

    # 用独立会话落库 assistant
    with SessionLocal() as db:
        conv = db.get(models.AIConversation, conversation_id)
        if conv is not None:
            db.add(
                models.AIMessage(
                    conversation_id=conv.id, role="assistant", content=collected
                )
            )
            ai_service.touch(conv)
            db.commit()
    yield sse({"type": "done"})


def _maybe_title(conv: models.AIConversation, message) -> None:
    """首条用户消息自动生成会话标题。"""
    if conv.title or message.role != "user":
        return
    title = message.content.strip().splitlines()[0][:24]
    conv.title = title or "新对话"
