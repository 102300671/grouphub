"""/ai/* —— 用户侧 AI 配置、会话与对话 API（JWT 鉴权）。

- 远程配置（含内置默认）：对话经本后端代理转发，密钥不出服务端；SSE 流式返回。
- 本地配置：由前端浏览器直连用户本地模型端点（走用户本地网络），消息通过
  /ai/conversations/{id}/messages 单独持久化。
"""
from __future__ import annotations

import json
import logging
import re
from typing import AsyncGenerator, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import ai_service, models, schemas
from app.db import SessionLocal, get_db
from app.models import utcnow
from app.security import get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)

# 网页端携带的最近消息条数；上游请求超时
_HISTORY_LIMIT = 20
_TIMEOUT = httpx.Timeout(120.0, connect=10.0)
_MAX_TOOL_STEPS = 3

# 文本协议工具（与 qqbot aitools 一致的轻量子集：仅联网搜索）
# 实测 agnes-3.0-flash 等模型会发生标签漂移：</tool_call> 误写成 </think>、
# 工具名被包进尖括号、混入其它 XML 闭合标签等。解析器对此统一容错。

_TOOL_OPEN_RE = re.compile(r"<tool_call\b[^>]*>", re.IGNORECASE)
_TOOL_CLOSE_RE = re.compile(
    r"<\s*/\s*(?:tool_call|function_call|function_calls|function|invoke|tool|think)\s*>",
    re.IGNORECASE,
)
# 闭合标签字面量在源码里拼接，避免与文档工具链冲突
_CLOSE_PARAMETER = "<" + "/parameter>"
_PURGE_TAG_RE = re.compile(
    r"<\s*/?\s*(?:parameter(?:\s*=\s*\w+)?|function|invoke|think|tool_call)[^>]*>",
    re.IGNORECASE,
)
_TOOL_RESULT_RE = re.compile(r"<tool_result\b.*?</tool_result>", re.DOTALL | re.IGNORECASE)
_NAME_RE = re.compile(r"([A-Za-z_]\w*\s*:\s*[A-Za-z_]\w*)")
_FUNCTION_TAG_RE = re.compile(r"<\s*function\s*[=\s]\s*([^>]+?)\s*>", re.IGNORECASE)
_PARAM_KV_RE = re.compile(r"^<\s*parameter\s*=\s*(\w+)\s*>\s*(.*)$", re.IGNORECASE)
_TRAILING_CLOSE_RE = re.compile(r"\s*<\s*/\s*[a-z_]+\s*>\s*$", re.IGNORECASE)
_BARE_CLOSING_RE = re.compile(r"^<\s*/\s*[a-z_]+\s*>$", re.IGNORECASE)
_BARE_OPENING_RE = re.compile(r"^<\s*[a-z_]+\s*>$", re.IGNORECASE)

_TOOL_CATALOG = (
    "你有联网搜索工具可用。当问题涉及最新资讯、实时数据、近期版本/数值、攻略资料"
    "或你不确定的事实，且你已有的知识不足以可靠回答时，严格按如下协议调用搜索，"
    "一次只输出一个块，块外不要写解释，不要用代码围栏，也不要输出本协议以外的任何 XML 标签：\n\n"
    "<tool_call>\nweb:search\nquery=搜索关键词\n</tool_call>\n\n"
    "要求：开标签必须是 <tool_call>，闭标签必须是 </tool_call>，二者都不能写错或省略；"
    "第二行固定为 web:search；query 必填，精炼，不要包含客套话。"
    "系统返回 <tool_result> 后，参考其中带 [序号] 的资料回答并标注引用；"
    "若搜索无结果或资料不足，如实说明，不要编造。"
)


def _iter_tool_blocks(text: str):
    """遍历所有工具块 (start, end, body)；容忍错误闭合标签与未闭合块。"""
    for m in _TOOL_OPEN_RE.finditer(text or ""):
        tail = text[m.end():]
        close = _TOOL_CLOSE_RE.search(tail)
        next_open = _TOOL_OPEN_RE.search(tail)
        if next_open and (not close or next_open.start() < close.start()):
            end = m.end() + next_open.start()
            body = tail[: next_open.start()]
        elif close:
            end = m.end() + close.end()
            body = tail[: close.start()]
        else:
            end = len(text)
            body = tail
        yield m.start(), end, body


def _parse_tool_body(body: str) -> Dict[str, object]:
    """解析块体：兼容标准 key=value、整体 JSON 与模型漂移出的杂标签。"""
    text = body.strip()
    name: Optional[str] = None
    name_from_tag = False
    fm = _FUNCTION_TAG_RE.search(text)
    if fm:
        nm = _NAME_RE.search(fm.group(1))
        if nm:
            name = nm.group(1).replace(" ", "")
            name_from_tag = True
            text = (text[: fm.start()] + "\n" + text[fm.end():])

    lines: List[str] = []
    for raw in text.splitlines():
        ln = raw.strip()
        if not ln:
            continue
        if _BARE_CLOSING_RE.match(ln) or _BARE_OPENING_RE.match(ln):
            continue
        lines.append(ln)

    if name is None:
        first = (lines[0] if lines else "").lstrip("<").rstrip(">").strip()
        nm = _NAME_RE.search(first)
        name = nm.group(1).replace(" ", "") if nm else first
        if lines:
            lines[0] = first

    args: Dict[str, str] = {}
    tail = lines if name_from_tag else lines[1:]
    joined = "\n".join(tail).strip()
    if joined[:1] in "{[":
        try:
            parsed = json.loads(joined)
            if isinstance(parsed, dict):
                args = {str(k): ("" if v is None else str(v)) for k, v in parsed.items()}
                tail = []
        except json.JSONDecodeError:
            pass
    for line in tail:
        ln = line.replace(_CLOSE_PARAMETER, "").strip()
        pm = _PARAM_KV_RE.match(ln)
        if pm:
            key, value = pm.group(1), pm.group(2)
        elif "=" in ln:
            key, value = ln.split("=", 1)
        else:
            continue
        value = _TRAILING_CLOSE_RE.sub("", value).rstrip(">").strip()
        args[key.strip().strip("<>")] = value
    return {"name": name or "", "args": args}


_FUNCTION_CLOSE_RE = re.compile(
    r"<\s*/\s*function\s*>", re.IGNORECASE
)


def _iter_function_blocks(text: str):
    """无 tool_call 外壳时的回退：识别 function 标签块（开标签到配对闭标签或文末）。"""
    for m in _FUNCTION_TAG_RE.finditer(text or ""):
        tail = text[m.end():]
        cm = _FUNCTION_CLOSE_RE.search(tail)
        if cm:
            yield m.start(), m.end() + cm.end(), text[m.start(): m.end() + cm.end()]
        else:
            yield m.start(), len(text), text[m.start():]


def _parse_tool_calls(text: str) -> List[Dict[str, object]]:
    """解析全部工具块（容忍标签漂移），返回 name/args/raw/span。"""
    calls: List[Dict[str, object]] = []
    blocks = list(_iter_tool_blocks(text or ""))
    if not blocks:
        blocks = list(_iter_function_blocks(text or ""))
    for start, end, raw_block in blocks:
        body = raw_block
        parsed = _parse_tool_body(body)
        if not parsed["name"]:
            continue
        parsed["raw"] = text[start:end]
        parsed["span"] = (start, end)
        calls.append(parsed)
    return calls


def _strip_tool_calls(text: str) -> str:
    """剥离工具块、残留 result 块与杂标签，压缩空行。"""
    out = text or ""
    spans = [(s, e) for s, e, _ in _iter_tool_blocks(out)]
    spans += [(s, e) for s, e, _ in _iter_function_blocks(out)]
    for start, end in sorted(spans, reverse=True):
        out = out[:start] + out[end:]
    out = _TOOL_RESULT_RE.sub("", out)
    out = _PURGE_TAG_RE.sub("", out)
    out = re.sub(r"[ \t]+\n", "\n", out)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


async def _web_search_backend(
    query: str, searxng_url: str, limit: int = 6
) -> str:
    """经本地 SearXNG JSON API 搜索，返回带序号的参考资料文本。"""
    base = searxng_url.rstrip("/")
    url = base if base.endswith("/search") else f"{base}/search"
    try:
        resp = await httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=8.0)).get(
            url,
            params={"q": query, "format": "json", "language": "zh-CN"},
            headers={"Accept": "application/json"},
        )
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        return f"错误：无法连接搜索服务：{exc}"
    if resp.status_code != 200:
        return f"错误：搜索服务返回 {resp.status_code}：{resp.text[:120]}"
    try:
        data = resp.json()
    except ValueError:
        return "错误：搜索服务未返回 JSON。"
    items = (data.get("results") or [])[:limit]
    if not items:
        return "未搜索到任何结果。可以换一个更精炼的关键词重试一次；若仍无结果就直接回答用户。"
    blocks = []
    for i, item in enumerate(items, 1):
        title = str(item.get("title") or "").strip()
        url_ = str(item.get("url") or "").strip()
        content = str(item.get("content") or "").strip()
        blocks.append(f"[{i}] {title}\n{url_}\n{content}".rstrip())
    return "\n\n".join(blocks)


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
    was_active = False
    sel = (
        db.query(models.AIUserActiveConfig)
        .filter(models.AIUserActiveConfig.user_id == user.id)
        .first()
    )
    if sel is not None and sel.config_id == config_id:
        was_active = True
        sel.config_id = 0  # 删除当前生效配置 → 回退内置主默认
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


# =================== 大组 / 组（会话层级） ===================

@router.get("/groups", response_model=schemas.AIGroupTreeListOut)
def group_tree(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """大组树：大组 -> 组 -> 会话（含未分组会话）。"""
    return ai_service.list_group_tree(db, user)


@router.patch("/groups/{group_id}", response_model=schemas.SimpleMessageOut)
def rename_group(
    group_id: int,
    payload: schemas.AIGroupPatchIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    group = ai_service.get_owned_group(db, user, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="大组不存在")
    group.name = payload.name.strip()[:100]
    db.commit()
    return schemas.SimpleMessageOut(message="ok")


@router.post("/folders", response_model=schemas.SimpleMessageOut)
def create_folder(
    payload: schemas.AIFolderIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    group = ai_service.get_owned_group(db, user, payload.group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="大组不存在")
    folder = models.AIFolder(
        group_id=group.id,
        owner_id=user.id,
        name=payload.name.strip()[:100],
    )
    db.add(folder)
    db.commit()
    db.refresh(folder)
    return schemas.SimpleMessageOut(
        message="ok", details={"id": folder.id, "name": folder.name}
    )


@router.patch("/folders/{folder_id}", response_model=schemas.SimpleMessageOut)
def rename_folder(
    folder_id: int,
    payload: schemas.AIFolderPatchIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    folder = ai_service.get_owned_folder(db, user, folder_id)
    if folder is None:
        raise HTTPException(status_code=404, detail="组不存在")
    folder.name = payload.name.strip()[:100]
    db.commit()
    return schemas.SimpleMessageOut(message="ok")


@router.delete("/folders/{folder_id}", response_model=schemas.SimpleMessageOut)
def delete_folder(
    folder_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    folder = ai_service.get_owned_folder(db, user, folder_id)
    if folder is None:
        raise HTTPException(status_code=404, detail="组不存在")
    # 组内会话保留，取消分组（仍属于当前大组）
    db.query(models.AIConversation).filter(
        models.AIConversation.folder_id == folder.id
    ).update({"folder_id": None}, synchronize_session=False)
    db.delete(folder)
    db.commit()
    return schemas.SimpleMessageOut(message="已删除")


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
    group = ai_service.get_owned_group(db, user, payload.ai_group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="大组不存在")
    folder = None
    if payload.folder_id is not None:
        folder = ai_service.get_owned_folder(db, user, payload.folder_id)
        if folder is None or folder.group_id != group.id:
            raise HTTPException(status_code=400, detail="组不存在或不属于该大组")
    conv = models.AIConversation(
        owner_id=user.id,
        ai_group_id=group.id,
        folder_id=folder.id if folder else None,
        source="web",
        title=(payload.title or "").strip() or None,
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


@router.patch(
    "/conversations/{conversation_id}", response_model=schemas.AIConversationDetailOut
)
def patch_conversation(
    conversation_id: int,
    payload: schemas.AIConversationPatchIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """会话编辑：改名 / 移动大组（清空原分组）/ 分组或取消分组。"""
    conv = ai_service.get_owned_conversation(db, user, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    data = payload.model_dump(exclude_unset=True)
    if "title" in data:
        conv.title = (data["title"] or "").strip()[:255] or None
    if "ai_group_id" in data and data["ai_group_id"] is not None:
        group = ai_service.get_owned_group(db, user, data["ai_group_id"])
        if group is None:
            raise HTTPException(status_code=404, detail="大组不存在")
        conv.ai_group_id = group.id
        conv.folder_id = None
    if "folder_id" in data:
        fid = data["folder_id"]
        if fid is None:
            conv.folder_id = None
        else:
            folder = ai_service.get_owned_folder(db, user, fid)
            if folder is None or folder.group_id != conv.ai_group_id:
                raise HTTPException(status_code=400, detail="组不存在或不属于会话所在大组")
            conv.folder_id = fid
    ai_service.touch(conv)
    db.commit()
    db.refresh(conv)
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


@router.post(
    "/conversations/{conversation_id}/default",
    response_model=schemas.SimpleMessageOut,
)
def set_default_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """把会话设为所在组的默认（当前）会话。"""
    conv = ai_service.get_owned_conversation(db, user, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    ai_service.set_default_conversation(db, user, conv)
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
    # QQ 大组内的会话（群聊/私聊）允许在前端继续对话
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
        url, key, model, request_messages, conv.id,
        question=payload.content, cfg_row=cfg_row,
    )
    from fastapi.responses import StreamingResponse

    return StreamingResponse(
        return_stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 防 nginx/反代缓冲 SSE
        },
    )


async def _open_chat_stream(
    client: httpx.AsyncClient,
    url: str,
    headers: Dict[str, str],
    model: str,
    messages: List[Dict[str, str]],
) -> httpx.Response:
    """开启上游流式响应。

    首选带 enable_thinking（Qwen3/部分中转支持，模型会在 reasoning_content
    独立通道输出思考，且工具调用标签更规范）；上游因不认识该参数返回 400/422
    时自动去掉该参数重试一次，兼容严格遵循 OpenAI 协议的端点。
    """
    thinking_payload = {
        "model": model, "messages": messages, "stream": True, "enable_thinking": True
    }
    resp = await client.send(
        client.build_request("POST", url, json=thinking_payload, headers=headers),
        stream=True,
    )
    if resp.status_code in (400, 422):
        body = (await resp.aread()).decode("utf-8", "ignore")[:200]
        await resp.aclose()
        logger.info("上游不支持 enable_thinking（%s），降级普通请求：%s", resp.status_code, body)
        plain_payload = {"model": model, "messages": messages, "stream": True}
        resp = await client.send(
            client.build_request("POST", url, json=plain_payload, headers=headers),
            stream=True,
        )
    return resp


async def _sse_response(
    url: str,
    key: str,
    model: str,
    request_messages: List[Dict[str, str]],
    conversation_id: int,
    question: Optional[str] = None,
    cfg_row: Optional[models.AIConfig] = None,
) -> AsyncGenerator[bytes, None]:
    """转发上游 SSE：思考内容（reasoning）、工具调用流程、正文增量；结束后落库 assistant。

    事件：{"type":"reasoning","text"} / {"type":"tool_call","name","args"} /
    {"type":"tool_result","name","summary"} / {"type":"delta","text"} /
    {"type":"error","message"} / {"type":"done"}。
    """

    def sse(event: dict) -> bytes:
        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")

    headers = {"Authorization": f"Bearer {key}"}
    # 配置了 SearXNG 才注入联网工具说明；工具循环仅在有搜索地址时启用
    searxng_url = ""
    if cfg_row is not None:
        searxng_url = (getattr(cfg_row, "searxng_url", None) or "").strip()
    messages: List[Dict[str, str]] = []
    if searxng_url:
        sys_idx = next(
            (i for i, m in enumerate(request_messages) if m.get("role") == "system"),
            None,
        )
        if sys_idx is not None:
            merged = request_messages[sys_idx]["content"] + "\n\n" + _TOOL_CATALOG
            messages = list(request_messages)
            messages[sys_idx] = {"role": "system", "content": merged}
        else:
            messages = [{"role": "system", "content": _TOOL_CATALOG}] + list(request_messages)
    else:
        messages = list(request_messages)

    collected_final = ""
    used_tool = False
    for _step in range(_MAX_TOOL_STEPS):
        collected = ""
        flushed = 0  # 已推给前端的正文长度；工具块原文不推送（避免 XML 闪烁）
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await _open_chat_stream(client, url, headers, model, messages)
                try:
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
                        except (ValueError, KeyError, IndexError):
                            continue
                        r_piece = delta.get("reasoning_content") or ""
                        if r_piece:
                            yield sse({"type": "reasoning", "text": r_piece})
                        piece = delta.get("content") or ""
                        if not piece:
                            continue
                        collected += piece
                        # 出现工具块开标签后，其后的正文先暂存：确认为工具调用则丢弃，
                        # 否则（模型只是在讨论协议文本）结束时补发，避免漏字。
                        open_m = _TOOL_OPEN_RE.search(collected)
                        safe_end = open_m.start() if open_m else len(collected)
                        if safe_end > flushed:
                            yield sse({"type": "delta", "text": collected[flushed:safe_end]})
                            flushed = safe_end
                finally:
                    await resp.aclose()
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            yield sse({"type": "error", "message": f"连接上游失败：{exc}"})
            return

        calls = _parse_tool_calls(collected)
        if not calls:
            if len(collected) > flushed:
                yield sse({"type": "delta", "text": collected[flushed:]})
            collected_final = _strip_tool_calls(collected) or collected.strip()
            break

        used_tool = True
        messages.append({"role": "assistant", "content": collected})
        for call in calls:
            name = str(call.get("name") or "")
            args = call.get("args") or {}
            yield sse({"type": "tool_call", "name": name, "args": args})
            if name == "web:search" and searxng_url:
                result = await _web_search_backend(str(args.get("query") or ""), searxng_url)
            else:
                result = f"错误：工具 {name} 不可用或未配置搜索地址。"
            summary = result.replace("\n", " ").strip()[:120]
            yield sse({"type": "tool_result", "name": name, "summary": summary})
            messages.append(
                {"role": "user", "content": f'<tool_result name="{name}">\n{result}\n</tool_result>'}
            )
    else:
        collected_final = _strip_tool_calls(collected) or collected.strip()

    if not collected_final.strip():
        yield sse({"type": "error", "message": "模型返回了空内容。"})
        return

    # 用独立会话落库 assistant（只存最终正文，不含思考与工具过程）
    with SessionLocal() as db:
        conv = db.get(models.AIConversation, conversation_id)
        if conv is not None:
            db.add(
                models.AIMessage(
                    conversation_id=conv.id, role="assistant", content=collected_final
                )
            )
            if question:
                import asyncio

                await asyncio.to_thread(
                    ai_service.try_ai_title, db, conv, question, collected_final, cfg_row
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
