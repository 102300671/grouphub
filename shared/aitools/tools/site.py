"""site 包：站内作品库（热门榜 / 检索 / 上传）。

两端共用同一份实现，差异只在注入的 SiteDAO：
- QQ 端：走 backend 的 /bot/works/* HTTP 接口；
- Web 端：直连数据库。

作品类型映射（WORK_TYPES）真源在 backend/app/models.py:WorkType，
这里放一份供两端共用；QQ 端的 /安利 命令也用它做别名归一。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..logs import log_exception
from ..ports import SiteDAO
from ..registry import (
    Context,
    Package,
    Param,
    Registry,
    SubmitResult,
    Tool,
    ToolHandler,
)

# ------------------ 作品类型：规范值 → 可接受的中文别名 ------------------

WORK_TYPES = {
    "novel": ("小说",),
    "anime": ("番剧", "动漫"),
    "movie": ("电影", "影片"),
    "gallery": ("图集", "图", "写真"),
    "fanwork": ("同人", "同人文"),
    "other": ("其他",),
}


def normalize_type(raw: str) -> str:
    """把中文类型别名映射到规范值；已是规范值则原样返回；未知值原样透传给后端校验。"""
    raw = raw.strip().lower()
    if raw in WORK_TYPES:
        return raw
    for canon, aliases in WORK_TYPES.items():
        if raw in [a.lower() for a in aliases]:
            return canon
    return raw


# ------------------ 工具包 ------------------

SITE_PACKAGE = Package(
    name="site",
    description=(
        "站内作品库：检索/上传本群资源站已收录的作品（小说/番剧/电影/图集/同人等）。"
        "用户问「站点有没有xxx」「推荐xxx」「群友在看什么」时检索；"
        "用户让AI「帮忙上传/安利某作品」时用 site:add（流程：AI 联网搜信息→站外有多个同名时让用户选→"
        "站内同名自动驳回不重复上传→上传）。"
    ),
)

# ------------------ 参数说明 ------------------

_PARAM_LIMIT_HOT = Param("返回条数", required=False, default=5, note="最大 20")
_PARAM_KEYWORD = Param("搜索关键词", required=True, note="作品名/作者名/标签等")
_PARAM_TYPE_SEARCH = Param(
    "作品类型",
    required=False,
    suffix="：novel/anime/movie/gallery/fanwork/other 或中文",
)
_PARAM_TYPE_ADD = Param(
    "类型",
    required=False,
    suffix="：novel/anime/movie/gallery/fanwork/other 或中文",
)
_PARAM_LIMIT_SEARCH = Param("返回条数", required=False, default=10, note="最大 50")
_PARAM_TITLE = Param("作品标题", required=True)
_PARAM_AUTHOR = Param("作者", required=False, note="建议先 web:search 查到再填")
_PARAM_SUMMARY = Param("一句话简介", required=False, note="建议先 web:search 查到再填")
_PARAM_TAGS = Param("标签", required=False, note="逗号分隔，如：百合,连载中")
_PARAM_LINKS = Param(
    "外链",
    required=False,
    note='格式 "站点名=URL,站点名=URL"，如：起点=https://...',
)


# ------------------ 列表格式化 ------------------

def fmt_works_for_ai(
    items: List[Dict[str, Any]],
    *,
    with_score: bool = False,
    site_base_url: str = "",
) -> str:
    """给 AI 看的作品列表：标题+作者+类型+简介+链接。

    site_base_url 为空时给相对链接（/works/{id}），QQ 端给绝对链接。
    """
    blocks = []
    for i, w in enumerate(items, 1):
        head = f"[{i}] {w.get('title', '')}"
        author = w.get("author")
        if author:
            head += f"（作者：{author}）"
        if with_score and w.get("score") is not None:
            head += f" · 热度 {w['score']}"
        parts = [head]
        wtype = w.get("type")
        if wtype:
            parts.append(f"类型：{wtype}")
        summary = w.get("summary")
        if summary:
            parts.append(f"简介：{summary}")
        wid = w.get("id")
        if wid is not None:
            parts.append(f"链接：{site_base_url}/works/{wid}")
        blocks.append("\n    ".join(parts))
    return (
        "以下是站内作品库的相关作品（请优先依据这些信息回答用户；"
        "回答末尾可附上作品链接供用户查看）：\n" + "\n".join(blocks)
    )


# ------------------ 工具实现 ------------------

def _make_hot_tool(site: SiteDAO, site_base_url: str) -> ToolHandler:
    async def handler(args: Dict[str, str], ctx: Context) -> str:
        try:
            limit = max(1, min(20, int((args.get("limit") or "5").strip())))
        except ValueError:
            limit = 5
        try:
            items = await site.hot(limit)
        except Exception as exc:  # noqa: BLE001
            log_exception("[aitools site:hot] 查询失败", exc)
            return f"错误：查询站内热门作品失败：{exc}。请如实告知用户暂时无法获取。"
        if not items:
            return "站内当前没有热门作品。"
        return fmt_works_for_ai(items, with_score=True, site_base_url=site_base_url)

    return handler


def _make_search_tool(site: SiteDAO, site_base_url: str) -> ToolHandler:
    async def handler(args: Dict[str, str], ctx: Context) -> str:
        keyword = (args.get("keyword") or args.get("query") or "").strip()
        if not keyword:
            return "错误：缺少必填参数 keyword（搜索关键词）。"
        try:
            limit = max(1, min(50, int((args.get("limit") or "10").strip())))
        except ValueError:
            limit = 10
        wtype = (args.get("type") or "").strip()
        if wtype:
            wtype = normalize_type(wtype)
        try:
            items = await site.search(keyword, limit, wtype or None)
        except Exception as exc:  # noqa: BLE001
            log_exception("[aitools site:search] 搜索失败", exc)
            return f"错误：搜索站内作品失败：{exc}。请如实告知用户暂时无法获取。"
        if not items:
            return f"站内没有匹配「{keyword}」的作品。可以建议用户用 /安利 命令提交入库。"
        return fmt_works_for_ai(items, site_base_url=site_base_url)

    return handler


def _make_add_tool(site: SiteDAO, site_base_url: str) -> ToolHandler:
    async def handler(args: Dict[str, str], ctx: Context) -> str:
        # 调用者身份来自上下文（不让模型在参数里传，避免 AI 给别人上传作品）
        if not ctx.user_qq and ctx.owner_id is None:
            return "错误：无法识别当前调用者身份（QQ 上下文未注入），请让用户改用 /安利 命令手动提交。"

        title = (args.get("title") or "").strip()
        if not title:
            return "错误：缺少必填参数 title（作品标题）。"

        payload: Dict[str, Any] = {"title": title}
        author = (args.get("author") or "").strip()
        if author:
            payload["author"] = author
        wtype = (args.get("type") or "").strip()
        if wtype:
            payload["type"] = normalize_type(wtype)
        summary = (args.get("summary") or "").strip()
        if summary:
            payload["summary"] = summary
        tags_raw = (args.get("tags") or "").strip()
        if tags_raw:
            payload["tags"] = [t.strip() for t in tags_raw.split(",") if t.strip()]
        links_raw = (args.get("links") or "").strip()
        if links_raw:
            # 简化格式："site_name=url,site_name=url"
            links: List[Dict[str, str]] = []
            for chunk in links_raw.split(","):
                if "=" in chunk:
                    name, url = chunk.split("=", 1)
                    name = name.strip()
                    url = url.strip()
                    if url:
                        links.append({"site_name": name, "url": url})
            if links:
                payload["links"] = links

        try:
            result = await site.submit(payload, ctx)
        except Exception as exc:  # noqa: BLE001
            log_exception("[aitools site:add] 提交失败", exc)
            return f"错误：上传作品失败：{exc}。请如实告知用户暂时无法上传。"

        if not result.ok:
            if result.duplicate:
                w = result.work or {}
                return (
                    f"站内已存在同名作品《{w.get('title')}》（ID={w.get('id')}），未重复入库。"
                    f"请告知用户可到 {site_base_url}/works/{w.get('id')} 标记支持。"
                )
            msg = result.message if result.message is not None else "未知原因"
            return f"上传失败：{msg}。请把这句话如实转告用户。"

        w = result.work or {}
        pending = w.get("status") == "pending"
        parts = [
            f"已提交（待管理员审核）：《{w.get('title')}》（ID={w.get('id')}）"
            if pending
            else f"已上传：《{w.get('title')}》（ID={w.get('id')}）"
        ]
        if w.get("author"):
            parts.append(f"作者：{w['author']}")
        if w.get("type"):
            parts.append(f"类型：{w['type']}")
        if w.get("summary"):
            parts.append(f"简介：{w['summary']}")
        if not pending:
            parts.append(f"详情：{site_base_url}/works/{w.get('id')}")
        return " · ".join(parts)

    return handler


# ------------------ 注册 ------------------

def register_site(registry: Registry, *, site: SiteDAO, site_base_url: str = "") -> None:
    """把 site 包与 3 个工具注册进注册表。

    工具顺序 = 手册里的顺序，改动顺序等于改 prompt，需谨慎。
    """
    registry.register_package(SITE_PACKAGE)
    registry.register_tool(
        Tool(
            namespace="site",
            name="hot",
            description="获取站内热门作品榜。用户问群友在看什么/推荐什么/热门作品时调用。",
            handler=_make_hot_tool(site, site_base_url),
            params={"limit": _PARAM_LIMIT_HOT},
        )
    )
    registry.register_tool(
        Tool(
            namespace="site",
            name="search",
            description="按关键词搜索站内作品库。用户问站点有没有某作品/某作者/某类型时调用。",
            handler=_make_search_tool(site, site_base_url),
            params={
                "keyword": _PARAM_KEYWORD,
                "type": _PARAM_TYPE_SEARCH,
                "limit": _PARAM_LIMIT_SEARCH,
            },
        )
    )
    registry.register_tool(
        Tool(
            namespace="site",
            name="add",
            description=(
                "把作品上传到站内作品库，调用者自动记为上传者。"
                "用户让AI「上传/安利某作品」时调用。"
                "调用前请先用 web:search 联网搜该作品的作者、简介等元信息。"
                "【站外多同名】如果 web:search 搜到多个同名作品（如小说/动漫/电影同名，或不同作者的同名），"
                "把每个候选的标题+作者+简介列给用户，让用户选择是哪一部后再调本工具；"
                "【站外单同名】如果只有一个匹配，把结果展示给用户确认「是这本吗？」后调用本工具；"
                "【站内同名】本工具会自动检查站内同名：已有群友上传过则驳回不重复入库，"
                "请把驳回结果和已有作品详情链接如实告知用户，建议用户去站点标记支持即可。"
            ),
            handler=_make_add_tool(site, site_base_url),
            params={
                "title": _PARAM_TITLE,
                "author": _PARAM_AUTHOR,
                "type": _PARAM_TYPE_ADD,
                "summary": _PARAM_SUMMARY,
                "tags": _PARAM_TAGS,
                "links": _PARAM_LINKS,
            },
        )
    )
