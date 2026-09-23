"""/ai/* —— 用户侧 AI 配置、会话与对话 API（JWT 鉴权）。

- 远程配置（含内置默认）：对话经本后端代理转发，密钥不出服务端；SSE 流式返回。
- 本地配置：由前端浏览器直连用户本地模型端点（走用户本地网络），消息通过
  /ai/conversations/{id}/messages 单独持久化。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, AsyncGenerator, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import ai_service, models, schemas
from app.api.bot.works import _hot_scores
from app.config import get_settings
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

# 文本协议工具采用与 qqbot 一致的「两阶段」包机制：system 只给包目录，
# 模型先 pkg:activate 激活包（系统回该包工具手册），再调用具体工具。
# 目录/手册按本次请求可用的包动态生成（见 _build_tool_table）。


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


# =================== 工具包（两阶段协议，与 qqbot 对齐） ===================

@dataclass(frozen=True)
class _ToolSpec:
    """包内一个工具的说明（手册用）；执行器在 _build_tool_table 中绑定。"""

    namespace: str
    name: str
    description: str
    params: Dict[str, str]

    @property
    def fullname(self) -> str:
        return f"{self.namespace}:{self.name}"


@dataclass(frozen=True)
class _PackageSpec:
    name: str
    description: str


_TOOL_OPEN_LITERAL = "<tool_call>"
_TOOL_CLOSE_LITERAL = "<" + "/tool_call>"


def _package_catalog(packages: List[_PackageSpec]) -> str:
    """生成模型侧包目录（仅包名+简介），拼进 system prompt。"""
    blocks: List[str] = [
        "# 工具调用协议",
        "当你仅凭自身知识无法可靠回答时（最新资讯、实时数据、近期版本/数值、站内作品、"
        "你不确定的事实），必须先调用工具获取信息，禁止编造；闲聊、观点、创作、常识问题不要调用。",
        "",
        "## 两阶段调用",
        "1. 先激活包：输出 pkg:activate 块，系统返回该包内的工具列表；",
        "2. 再调用工具：按返回的工具列表选择具体工具，输出 包名:工具名 块。",
        "",
        "调用块格式（块外不要写解释，不要用代码围栏包裹；一次只输出一个块）：",
        _TOOL_OPEN_LITERAL,
        "包名:工具名   （激活包时第二行固定写 pkg:activate）",
        "参数名=参数值",
        _TOOL_CLOSE_LITERAL,
        '系统执行后以 <tool_result name="..."> 回传结果；拿到结果后，'
        "要么直接输出最终回答（不含工具块），要么继续调用。",
        "",
        "判定规则（按顺序）：",
        "1. 用户明确要求搜索/联网/查一下/最新/最近，或问题涉及新闻、近期事件、"
        "软件/游戏新版本与改动、实时数据 → 立即激活 web 包并调用，不要反问；",
        "2. 用户问站内有没有某作品、求推荐、群友在看什么 → 激活 site 包检索；",
        "3. 用户让你帮忙上传/安利某作品 → 先 web:search 查作者与简介，"
        "有多个同名候选时先让用户选定，再调 site:add（站内同名会自动驳回）；",
        "4. 闲聊、观点、写作、写代码、常识 → 不要调用，直接回答。",
        "",
        "示例（用户消息：泰拉瑞亚最新版本更新了什么）：",
        "第一步——激活包：",
        _TOOL_OPEN_LITERAL,
        "pkg:activate",
        "name=web",
        _TOOL_CLOSE_LITERAL,
        "第二步——系统返回 web 包工具后，调用搜索：",
        _TOOL_OPEN_LITERAL,
        "web:search",
        "query=泰拉瑞亚 最新版本 更新内容",
        _TOOL_CLOSE_LITERAL,
        "第三步——系统返回搜索结果后，输出最终回答（引用资料按 [序号] 标注）。",
        "",
        "可用工具包：",
    ]
    for pkg in packages:
        blocks.append(f"- {pkg.name}：{pkg.description}")
    blocks.append("")
    blocks.append("最终回答中不要出现工具块。")
    return "\n".join(blocks)


def _package_manual(pkg_name: str, specs: List[_ToolSpec]) -> str:
    """激活包后回给模型的包内工具详情。"""
    lines = [f"包 [{pkg_name}] 已激活，可用工具："]
    for tool in specs:
        lines.append(f"## {tool.fullname}")
        lines.append(f"说明：{tool.description}")
        if tool.params:
            lines.append("参数：")
            for pname, pdesc in tool.params.items():
                lines.append(f"  {pname}={pdesc}")
        lines.append("")
    lines.append("现在选择一个工具调用；不需要时直接回答用户。")
    return "\n".join(lines)


# ------------------ site 包：站内作品库（直接查库，以当前登录用户身份） ------------------

_VALID_WORK_TYPES = {"novel", "anime", "movie", "gallery", "fanwork", "other"}


def _fmt_works_for_ai(
    works: List[models.Work], scores: Optional[Dict[int, int]] = None
) -> str:
    """给模型看的作品列表：标题/作者/类型/简介/相对链接。"""
    blocks = []
    for i, w in enumerate(works, 1):
        head = f"[{i}] {w.title}"
        if w.author:
            head += f"（作者：{w.author}）"
        if scores and scores.get(w.id):
            head += f" · 热度 {scores[w.id]}"
        parts = [head]
        if w.type:
            parts.append(f"类型：{w.type}")
        if w.summary:
            parts.append(f"简介：{w.summary}")
        parts.append(f"链接：/works/{w.id}")
        blocks.append("\n    ".join(parts))
    if not blocks:
        return ""
    return (
        "以下是站内作品库的相关作品（请优先依据这些信息回答用户；"
        "回答末尾可附上作品链接供用户查看）：\n" + "\n".join(blocks)
    )


def _clamp_int(raw: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int((raw or "").strip() or default)))
    except ValueError:
        return default


def _site_hot_sync(args: Dict[str, str], user_id: int) -> str:
    limit = _clamp_int(args.get("limit", ""), 5, 1, 20)
    with SessionLocal() as db:
        works = (
            db.query(models.Work)
            .filter(models.Work.status == models.WorkStatus.PUBLISHED)
            .all()
        )
        scores = _hot_scores(db, [w.id for w in works])
        ordered = sorted(works, key=lambda w: scores.get(w.id, 0), reverse=True)[:limit]
        return _fmt_works_for_ai(ordered, scores) or "站内当前没有热门作品。"


def _site_search_sync(args: Dict[str, str], user_id: int) -> str:
    keyword = (args.get("keyword") or args.get("query") or "").strip()
    if not keyword:
        return "错误：缺少必填参数 keyword（搜索关键词）。"
    limit = _clamp_int(args.get("limit", ""), 10, 1, 50)
    with SessionLocal() as db:
        q = db.query(models.Work).filter(
            models.Work.status == models.WorkStatus.PUBLISHED
        )
        like = f"%{keyword}%"
        q = q.filter(models.Work.title.like(like) | models.Work.author.like(like))
        wtype = (args.get("type") or "").strip().lower()
        if wtype in _VALID_WORK_TYPES:
            q = q.filter(models.Work.type == wtype)
        works = q.order_by(models.Work.updated_at.desc()).limit(limit).all()
        return (
            _fmt_works_for_ai(works)
            or f"站内没有匹配「{keyword}」的作品。可以建议用户在站点上传这部作品。"
        )


def _site_add_sync(args: Dict[str, str], user_id: int) -> str:
    """以当前登录用户身份上传作品（同名已发布作品自动驳回，不重复入库）。"""
    title = (args.get("title") or "").strip()
    if not title:
        return "错误：缺少必填参数 title（作品标题）。"
    with SessionLocal() as db:
        user = db.get(models.User, user_id)
        if user is None:
            return "错误：登录态已失效，请刷新页面后重试。"
        dup = (
            db.query(models.Work)
            .filter(
                models.Work.title == title,
                models.Work.status == models.WorkStatus.PUBLISHED,
            )
            .first()
        )
        if dup is not None:
            return (
                f"站内已存在同名作品《{dup.title}》（ID={dup.id}），未重复入库。"
                f"请告知用户可到 /works/{dup.id} 查看并标记支持。"
            )
        wtype = (args.get("type") or "").strip().lower()
        if wtype not in _VALID_WORK_TYPES:
            wtype = models.WorkType.OTHER
        tags = [
            t.strip()
            for t in (args.get("tags") or "").split(",")
            if t.strip()
        ]
        work = models.Work(
            title=title,
            author=(args.get("author") or "").strip() or None,
            type=wtype,
            summary=(args.get("summary") or "").strip() or None,
            uploader_id=user.id,
            tags_json=tags,
            status=(
                models.WorkStatus.PENDING
                if get_settings().works_require_review
                else models.WorkStatus.PUBLISHED
            ),
        )
        db.add(work)
        db.flush()
        for chunk in (args.get("links") or "").split(","):
            if "=" not in chunk:
                continue
            site_name, url = chunk.split("=", 1)
            site_name, url = site_name.strip(), url.strip()
            if url:
                db.add(
                    models.WorkLink(work_id=work.id, site_name=site_name or None, url=url)
                )
        db.commit()
        db.refresh(work)
        pending = work.status == models.WorkStatus.PENDING
        parts = [
            f"已提交（待管理员审核）：《{work.title}》（ID={work.id}）"
            if pending
            else f"已上传：《{work.title}》（ID={work.id}）"
        ]
        if work.author:
            parts.append(f"作者：{work.author}")
        if work.type:
            parts.append(f"类型：{work.type}")
        if not pending:
            parts.append(f"详情：/works/{work.id}")
        return " · ".join(parts)


def _build_tool_table(
    user_id: int, searxng_url: str
) -> tuple[List[_PackageSpec], List[_ToolSpec], Dict[str, Callable[[Dict[str, str]], Awaitable[str]]]]:
    """按当前请求上下文构造可用包、工具说明与执行器映射。"""
    packages: List[_PackageSpec] = []
    specs: List[_ToolSpec] = []
    handlers: Dict[str, Callable[[Dict[str, str]], Awaitable[str]]] = {}

    if searxng_url:
        packages.append(
            _PackageSpec(
                name="web",
                description="联网搜索：最新资讯、实时数据、近期版本/数值、你不确定的事实。",
            )
        )
        specs.append(
            _ToolSpec(
                namespace="web",
                name="search",
                description="联网搜索公开网页，返回带序号的参考资料。",
                params={"query": "搜索关键词（必填，精炼，不要客套话）"},
            )
        )
        handlers["web:search"] = lambda args: _web_search_backend(
            str(args.get("query") or ""), searxng_url
        )

    packages.append(
        _PackageSpec(
            name="site",
            description="站内作品库：检索/上传本站已收录的小说、番剧、电影、图集、同人等作品。"
            "用户问「站点有没有 xxx」「推荐 xxx」「群友在看什么」时检索；"
            "用户让 AI「帮忙上传/安利某作品」时用 site:add。",
        )
    )
    specs += [
        _ToolSpec(
            namespace="site",
            name="hot",
            description="获取站内热门作品榜。用户问群友在看什么/推荐什么/热门作品时调用。",
            params={"limit": "返回条数（可选，默认 5，最大 20）"},
        ),
        _ToolSpec(
            namespace="site",
            name="search",
            description="按关键词搜索站内作品库。用户问站点有没有某作品/某作者/某类型时调用。",
            params={
                "keyword": "搜索关键词（必填，作品名/作者名等）",
                "type": "作品类型（可选）：novel/anime/movie/gallery/fanwork/other",
                "limit": "返回条数（可选，默认 10，最大 50）",
            },
        ),
        _ToolSpec(
            namespace="site",
            name="add",
            description=(
                "把作品上传到站内作品库，上传者自动记为当前用户。"
                "调用前请先用 web:search 查到作者、简介等元信息；多个同名候选先让用户选定；"
                "站内已有同名已发布作品时本工具会驳回，不要重复提交。"
            ),
            params={
                "title": "作品标题（必填）",
                "author": "作者（可选，建议先 web:search 查到再填）",
                "type": "类型（可选）：novel/anime/movie/gallery/fanwork/other",
                "summary": "一句话简介（可选）",
                "tags": "标签（可选，逗号分隔，如：百合,连载中）",
                "links": '外链（可选，格式 "站点名=URL,站点名=URL"）',
            },
        ),
    ]
    handlers["site:hot"] = lambda args: asyncio.to_thread(_site_hot_sync, args, user_id)
    handlers["site:search"] = lambda args: asyncio.to_thread(_site_search_sync, args, user_id)
    handlers["site:add"] = lambda args: asyncio.to_thread(_site_add_sync, args, user_id)
    return packages, specs, handlers


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
            "agent_steps": ai_service.load_agent_steps(m.agent_steps),
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
            conversation_id=conv.id,
            role=payload.role,
            content=payload.content,
            agent_steps=ai_service.dump_agent_steps(payload.agent_steps)
            if payload.role == "assistant"
            else None,
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
            "agent_steps": ai_service.load_agent_steps(m.agent_steps),
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
    # 动态注入当前日期（人设提示词之后、工具目录之前），校准模型时效认知
    system_content = "\n\n".join(
        part for part in [system_prompt, ai_service.current_date_hint()] if part
    )
    request_messages.append({"role": "system", "content": system_content})
    request_messages.extend(history)

    return_stream = _sse_response(
        url, key, model, request_messages, conv.id, user,
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
    user: models.User,
    question: Optional[str] = None,
    cfg_row: Optional[models.AIConfig] = None,
) -> AsyncGenerator[bytes, None]:
    """转发上游 SSE：思考（reasoning）、激活包/工具调用的请求与响应、正文增量；落库最终正文。

    事件：
    - {"type":"round","index"}：一轮模型请求开始（前端据此把思考与调用归组）；
    - {"type":"reasoning","text"} / {"type":"delta","text"}；
    - {"type":"tool_call","id","kind","name","args","raw"}：
      kind="activate" 为 pkg:activate 激活包，kind="tool" 为普通工具调用；
    - {"type":"tool_result","id","name","ok","summary","content"}：
      summary 为单行截断预览，content 为完整响应（前端点击弹窗查看）；
    - {"type":"error","message"} / {"type":"done"}。
    """

    def sse(event: dict) -> bytes:
        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")

    headers = {"Authorization": f"Bearer {key}"}
    searxng_url = ""
    if cfg_row is not None:
        searxng_url = (getattr(cfg_row, "searxng_url", None) or "").strip()
    packages, tool_specs, handlers = _build_tool_table(user.id, searxng_url)

    # 有可用包时把两阶段协议目录并入 system prompt
    messages: List[Dict[str, str]] = list(request_messages)
    if packages:
        catalog = _package_catalog(packages)
        sys_idx = next(
            (i for i, m in enumerate(messages) if m.get("role") == "system"),
            None,
        )
        if sys_idx is not None:
            messages[sys_idx] = {
                "role": "system",
                "content": messages[sys_idx]["content"] + "\n\n" + catalog,
            }
        else:
            messages.insert(0, {"role": "system", "content": catalog})

    collected_final = ""
    collected = ""
    tool_steps = 0
    total_rounds = 0
    max_rounds = _MAX_TOOL_STEPS * 3 + 3  # 激活包轮不占工具额度，另加安全阀
    call_seq = 0
    # 思维链/工具链轨迹（随 assistant 消息落库，前端刷新/历史可还原）
    trace_steps: List[dict] = []

    while total_rounds < max_rounds:
        total_rounds += 1
        yield sse({"type": "round", "index": total_rounds - 1})
        trace_steps.append({"reasoning": "", "calls": []})
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
                            if trace_steps:
                                trace_steps[-1]["reasoning"] += r_piece
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

        messages.append({"role": "assistant", "content": collected})

        # ---- 激活包（pkg:activate，不计入工具轮数）----
        activates = [c for c in calls if str(c.get("name")) == "pkg:activate"]
        tool_calls = [c for c in calls if str(c.get("name")) != "pkg:activate"]

        for ac in activates:
            call_seq += 1
            cid = call_seq
            args = ac.get("args") or {}
            trace_call = {
                "id": cid,
                "kind": "activate",
                "name": "pkg:activate",
                "args": args,
                "raw": ac.get("raw") or "",
            }
            yield sse({"type": "tool_call", **trace_call})
            trace_steps[-1]["calls"].append(trace_call)
            pkg_name = str(args.get("name") or "").strip()
            pkg_specs = [t for t in tool_specs if t.namespace == pkg_name]
            if not pkg_name or not any(p.name == pkg_name for p in packages):
                available = "、".join(p.name for p in packages) or "（无可用包）"
                result_text = f"错误：包「{pkg_name}」不存在或未指定 name。可用包：{available}"
                logger.warning("激活包失败：模型请求了不存在的包 %r", pkg_name)
            else:
                result_text = _package_manual(pkg_name, pkg_specs)
                logger.info("激活工具包：%s", pkg_name)
            trace_call["result"] = {
                "ok": not result_text.startswith("错误："),
                "summary": result_text.replace("\n", " ").strip()[:120],
                "content": result_text,
            }
            yield sse(
                {
                    "type": "tool_result",
                    "id": cid,
                    "name": "pkg:activate",
                    **trace_call["result"],
                }
            )
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f'<tool_result name="pkg:activate">\n{result_text}\n</tool_result>'
                    ),
                }
            )

        # ---- 普通工具调用 ----
        if tool_calls:
            if tool_steps >= _MAX_TOOL_STEPS:
                logger.warning("工具调用轮数达到上限 %s，终止循环", _MAX_TOOL_STEPS)
                collected_final = "抱歉，工具调用次数已达上限，请换个问法或稍后再试。"
                break
            tool_steps += 1
            for call in tool_calls:
                call_seq += 1
                cid = call_seq
                name = str(call.get("name") or "")
                args = call.get("args") or {}
                trace_call = {
                    "id": cid,
                    "kind": "tool",
                    "name": name,
                    "args": args,
                    "raw": call.get("raw") or "",
                }
                yield sse({"type": "tool_call", **trace_call})
                trace_steps[-1]["calls"].append(trace_call)
                handler = handlers.get(name)
                if handler is None:
                    available = "、".join(sorted(handlers)) or "（当前无可用工具）"
                    result_text = (
                        f"错误：工具「{name}」不存在或未激活对应包。可用工具：{available}。"
                        "请先 pkg:activate 激活对应包，或直接回答用户。"
                    )
                    logger.warning("模型调用了未注册工具 %s", name)
                else:
                    logger.info("执行工具：%s 参数=%s", name, args)
                    try:
                        result_text = await handler(args)
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("工具 %s 执行异常：%r", name, exc)
                        result_text = (
                            f"错误：工具 {name} 执行失败：{exc}。"
                            "不要重复调用同一工具，请基于已有信息回答或如实告知用户。"
                        )
                result_text = (result_text or "").strip() or "工具执行成功但没有返回内容。"
                trace_call["result"] = {
                    "ok": not result_text.startswith("错误："),
                    "summary": result_text.replace("\n", " ").strip()[:120],
                    "content": result_text,
                }
                yield sse(
                    {
                        "type": "tool_result",
                        "id": cid,
                        "name": name,
                        **trace_call["result"],
                    }
                )
                messages.append(
                    {
                        "role": "user",
                        "content": f'<tool_result name="{name}">\n{result_text}\n</tool_result>',
                    }
                )
    else:
        if not collected_final:
            collected_final = _strip_tool_calls(collected) or collected.strip()

    if not collected_final.strip():
        yield sse({"type": "error", "message": "模型返回了空内容。"})
        return

    # 用独立会话落库 assistant：正文 + 思维链/工具链轨迹（刷新与历史会话可还原）
    agent_steps_json = ai_service.dump_agent_steps(trace_steps)
    with SessionLocal() as db:
        conv = db.get(models.AIConversation, conversation_id)
        if conv is not None:
            db.add(
                models.AIMessage(
                    conversation_id=conv.id,
                    role="assistant",
                    content=collected_final,
                    agent_steps=agent_steps_json,
                )
            )
            if question:
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
