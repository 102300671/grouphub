"""命令实现：作品库（/work hot|search|add）。

对应原 plugins/works.py 的三条命令，迁移到 GNU 选项解析。
后端接口未变（hot/search/submit），仅 add 打通了 BotWorkSubmitIn 早已支持、
但旧插件从未传过的 author/type/summary/tags/links/source_work_id 字段。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote_plus

from dotenv import load_dotenv
from nonebot import logger
from nonebot.adapters import Bot, Event
from nonebot.permission import SUPERUSER

from .._lib import aitools
from .._lib.bots import resolve_openid_qq, resolve_sender_qq
from .._lib.cli import Command, Option, ParseResult
from .._lib.client import backend_client

_ENV_PATH = Path(__file__).resolve().parents[2] / ".env.prod"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH, override=False)

SITE_BASE_URL = os.getenv("SITE_BASE_URL", "http://127.0.0.1:5173").rstrip("/")

# 作品类型：规范值 → 可接受的中文别名（真源 backend/app/models.py:WorkType）
WORK_TYPES = {
    "novel": ("小说",),
    "anime": ("番剧", "动漫"),
    "movie": ("电影", "影片"),
    "gallery": ("图集", "图", "写真"),
    "fanwork": ("同人", "同人文"),
    "other": ("其他",),
}

_TYPE_OPTION = Option(
    long="type",
    value_name="<类型>",
    choices_map=WORK_TYPES,
    help="类型：novel/anime/movie/gallery/fanwork/other（或中文）",
)


# ------------------ 身份识别（双适配器） ------------------

async def _sender_qq(event: Event) -> Optional[str]:
    """取发送者真实 QQ；无法识别返回 None。（委托 bots.resolve_sender_qq）"""
    return await resolve_sender_qq(event)


def _fmt_works(items: list, with_score: bool = False) -> str:
    """bot 命令用的精简格式：标题+作者+简介(截断)+详情链接。

    与 _fmt_works_for_ai 的区别：AI 版给完整字段供模型推理；
    本函数给群友看，每条只带简介一行 + 详情页链接，不返回正文/书评等详细内容。
    """
    if not items:
        return "暂无结果。"
    lines = []
    for i, w in enumerate(items, 1):
        head = f"{i}. {w.get('title', '')}"
        author = w.get("author")
        if author:
            head += f"（{author}）"
        if with_score and w.get("score") is not None:
            head += f" · 热度 {w['score']}"
        lines.append(head)
        summary = (w.get("summary") or "").strip()
        if summary:
            if len(summary) > 80:
                summary = summary[:79] + "…"
            lines.append(f"   简介：{summary}")
        wid = w.get("id")
        if wid is not None:
            lines.append(f"   {SITE_BASE_URL}/works/{wid}")
    return "\n".join(lines)


def _parse_links(raw_links: List[str]) -> List[dict]:
    """把 --link 名=URL 解析为 [{"site_name","url"}]。无 = 时站点名留空。"""
    out = []
    for item in raw_links:
        if "=" in item:
            name, url = item.split("=", 1)
            out.append({"site_name": name.strip() or None, "url": url.strip()})
        elif item.strip():
            out.append({"site_name": None, "url": item.strip()})
    return out


# ------------------ 命令定义 ------------------

COMMANDS = (
    Command(
        ns_en="work", ns_zh="作品", sub_en="hot", sub_zh="热门",
        quick=("热门", "hot"),
        summary="热门作品榜",
        brief="",
        usage="[选项]",
        examples=("/热门", "/hot -n 10", "/作品 热门 --limit 3"),
        options=(
            Option(long="limit", short="n", value_name="<数>", kind="int",
                   default=5, min_value=1, max_value=20,
                   help="返回条数，默认 5，上限 20"),
        ),
        handler="commands.work:hot",
    ),
    Command(
        ns_en="work", ns_zh="作品", sub_en="search", sub_zh="搜索",
        quick=("搜索", "search"),
        summary="关键词搜作品",
        brief="-k <词>",
        usage="--keyword <词> [选项]",
        examples=("/搜索 -k 诡秘之主", "/search --keyword 百合 --type novel -n 20"),
        options=(
            Option(long="keyword", short="k", value_name="<词>", required=True,
                   help="搜索关键词（必填）"),
            _TYPE_OPTION,
            Option(long="limit", short="n", value_name="<数>", kind="int",
                   default=10, min_value=1, max_value=50,
                   help="返回条数，默认 10，上限 50"),
        ),
        handler="commands.work:search",
    ),
    Command(
        ns_en="work", ns_zh="作品", sub_en="add", sub_zh="安利",
        quick=("安利", "recommend"),
        summary="提交作品入库",
        brief="-t <名>",
        usage="--title <作品名> [选项]",
        examples=(
            "/安利 -t 诡秘之主",
            "/recommend -t 诡秘之主 -a 爱潜水的乌贼 --type novel",
            "/安利 -t 某同人 --source 12 --tags 百合,连载中 -s 短篇甜文",
            "/安利 -t 诡秘之主 --uploader 12345  # 管理员：指定 12345 为上传者",
        ),
        options=(
            Option(long="title", short="t", value_name="<名>", required=True,
                   help="作品标题（必填）"),
            Option(long="author", short="a", value_name="<名>", help="作者"),
            _TYPE_OPTION,
            Option(long="summary", short="s", value_name="<简介>", help="一句话简介"),
            Option(long="tags", value_name="<标签>", kind="csv", repeatable=True,
                   help="标签，逗号分隔或重复给出"),
            Option(long="link", short="l", value_name="<名=URL>", repeatable=True,
                   help="外链，可重复，如 -l 起点=https://..."),
            Option(long="source", value_name="<作品ID>", kind="int",
                   help="绑定原作 ID（同人文用）"),
            Option(long="uploader", value_name="<QQ>", help="特权参数：指定上传者 QQ（仅管理员可用）"),
        ),
        handler="commands.work:add",
        require_registered=True,
        notes=(
            "提交者即上传者；同名作品已存在时返回提示，不重复入库。",
            "--uploader 仅管理员可用，需在 .env ADMIN_QQS 配置中；指定后该 QQ 必须已在站点注册。",
        ),
    ),
)


# ------------------ handlers ------------------

async def hot(bot: Bot, event: Event, result: ParseResult) -> None:
    limit = result.get("limit", 5)
    try:
        resp = await backend_client.get("/bot/works/hot", params={"limit": limit})
        resp.raise_for_status()
        items = resp.json().get("items", [])
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[work hot] 热门查询失败：{exc}")
        await bot.send(event, f"⚠️ 查询失败：{exc}")
        return
    body = _fmt_works(items, with_score=True)
    if items:
        body += f"\n详情见站点：{SITE_BASE_URL}/works"
    await bot.send(event, f"📚 热门作品 Top{len(items)}：\n{body}")


async def search(bot: Bot, event: Event, result: ParseResult) -> None:
    keyword = result.get("keyword")
    limit = result.get("limit", 10)
    wtype = result.get("type")
    params = {"keyword": keyword, "limit": limit}
    if wtype:
        params["type"] = wtype
    try:
        resp = await backend_client.get("/bot/works/search", params=params)
        resp.raise_for_status()
        items = resp.json().get("items", [])
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[work search] 搜索失败：{exc}")
        await bot.send(event, f"⚠️ 搜索失败：{exc}")
        return
    body = _fmt_works(items)
    if items:
        body += f"\n详情见站点：{SITE_BASE_URL}/works?keyword={quote_plus(keyword)}"
    await bot.send(event, f"🔍 「{keyword}」搜索结果：\n{body}")


async def add(bot: Bot, event: Event, result: ParseResult) -> None:
    title = result.get("title")
    qq = await _sender_qq(event)
    if not qq:
        await bot.send(
            event,
            "⚠️ 无法识别你的真实 QQ：请先在站点完成注册绑定"
            "（注册页会给你一个验证码，发给机器人即可），再使用安利。",
        )
        return

    # 特权参数 --uploader：仅管理员可用，指定一个已注册的 QQ 作为上传者
    uploader_qq = (result.get("uploader") or "").strip()
    if uploader_qq:
        if not await SUPERUSER(bot, event):
            await bot.send(event, "⚠️ --uploader 仅管理员可用")
            return
        if not uploader_qq.isdigit():
            await bot.send(event, "⚠️ --uploader 必须是 QQ 号（纯数字）")
            return

    payload = {"qq": qq, "title": title}
    if uploader_qq:
        payload["uploader_qq"] = uploader_qq
    author = result.get("author")
    if author:
        payload["author"] = author
    wtype = result.get("type")
    if wtype:
        payload["type"] = wtype
    summary = result.get("summary")
    if summary:
        payload["summary"] = summary
    tags = result.get("tags")
    if tags:
        payload["tags"] = tags
    links = _parse_links(result.get("link") or [])
    if links:
        payload["links"] = links
    source = result.get("source")
    if source:
        payload["source_work_id"] = source

    try:
        resp = await backend_client.post("/bot/works/submit", json=payload)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[work add] 提交失败：{exc}")
        await bot.send(event, f"⚠️ 提交失败：{exc}")
        return

    if not data.get("ok"):
        if data.get("duplicate"):
            w = data.get("work", {})
            await bot.send(
                event,
                f"📖 已存在同名作品：{w.get('title')}（ID={w.get('id')}）\n"
                f"可到站点标记支持：{SITE_BASE_URL}/works/{w.get('id')}",
            )
        else:
            await bot.send(event, f"⚠️ {data.get('message', '提交失败')}")
        return

    w = data.get("work", {})
    lines = [f"📖 已安利入库：{w.get('title')}（ID={w.get('id')}）"]
    if data.get("designated_uploader"):
        lines.append(f"（已指定上传者 QQ {uploader_qq}）")
    elif data.get("created_user"):
        lines.append("（你还没有站点账号，已按群名片自动创建，首次登录用验证码即可）")
    lines.append(f"查看详情：{SITE_BASE_URL}/works/{w.get('id')}")
    await bot.send(event, "\n".join(lines))


# ------------------ AI 工具包：site（站内作品库） ------------------
# 把 /热门 /搜索 命令背后的 backend 调用封装成 aitools，让 AI 能自主检索站内作品库。
# 与 web 包并列，AI 需要时 pkg:activate name=site 激活后调用 site:hot / site:search。


def _normalize_type(raw: str) -> str:
    """把中文类型别名映射到规范值；已是规范值则原样返回；未知值原样透传给后端校验。"""
    raw = raw.strip().lower()
    if raw in WORK_TYPES:
        return raw
    for canon, aliases in WORK_TYPES.items():
        if raw in [a.lower() for a in aliases]:
            return canon
    return raw


def _fmt_works_for_ai(items: list, with_score: bool = False) -> str:
    """给 AI 看的作品列表：标题+作者+类型+简介+链接，比 _fmt_works 更详细。"""
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
            parts.append(f"链接：{SITE_BASE_URL}/works/{wid}")
        blocks.append("\n    ".join(parts))
    return (
        "以下是站内作品库的相关作品（请优先依据这些信息回答用户；"
        "回答末尾可附上作品链接供用户查看）：\n" + "\n".join(blocks)
    )


async def _site_hot_tool(args: Dict[str, str]) -> str:
    """site:hot 工具入口：返回站内热门作品榜。"""
    try:
        limit = max(1, min(20, int((args.get("limit") or "5").strip())))
    except ValueError:
        limit = 5
    try:
        resp = await backend_client.get("/bot/works/hot", params={"limit": limit})
        resp.raise_for_status()
        items = resp.json().get("items", [])
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[ai site:hot] 查询失败：{exc}")
        return f"错误：查询站内热门作品失败：{exc}。请如实告知用户暂时无法获取。"
    if not items:
        return "站内当前没有热门作品。"
    return _fmt_works_for_ai(items, with_score=True)


async def _site_search_tool(args: Dict[str, str]) -> str:
    """site:search 工具入口：按关键词搜索站内作品库。"""
    keyword = (args.get("keyword") or args.get("query") or "").strip()
    if not keyword:
        return "错误：缺少必填参数 keyword（搜索关键词）。"
    try:
        limit = max(1, min(50, int((args.get("limit") or "10").strip())))
    except ValueError:
        limit = 10
    wtype = (args.get("type") or "").strip()
    if wtype:
        wtype = _normalize_type(wtype)
    params: Dict[str, object] = {"keyword": keyword, "limit": limit}
    if wtype:
        params["type"] = wtype
    try:
        resp = await backend_client.get("/bot/works/search", params=params)
        resp.raise_for_status()
        items = resp.json().get("items", [])
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[ai site:search] 搜索失败：{exc}")
        return f"错误：搜索站内作品失败：{exc}。请如实告知用户暂时无法获取。"
    if not items:
        return f"站内没有匹配「{keyword}」的作品。可以建议用户用 /安利 命令提交入库。"
    return _fmt_works_for_ai(items)


aitools.register_package(
    aitools.Package(
        name="site",
        description="站内作品库：检索/上传本群资源站已收录的作品（小说/番剧/电影/图集/同人等）。"
        "用户问「站点有没有xxx」「推荐xxx」「群友在看什么」时检索；"
        "用户让AI「帮忙上传/安利某作品」时用 site:add（流程：AI 联网搜信息→站外有多个同名时让用户选→"
        "站内同名自动驳回不重复上传→上传）。",
    )
)
aitools.register_tool(
    aitools.Tool(
        namespace="site",
        name="hot",
        description="获取站内热门作品榜。用户问群友在看什么/推荐什么/热门作品时调用。",
        handler=_site_hot_tool,
        params={"limit": "返回条数（可选，默认 5，最大 20）"},
    )
)
aitools.register_tool(
    aitools.Tool(
        namespace="site",
        name="search",
        description="按关键词搜索站内作品库。用户问站点有没有某作品/某作者/某类型时调用。",
        handler=_site_search_tool,
        params={
            "keyword": "搜索关键词（必填，作品名/作者名/标签等）",
            "type": "作品类型（可选）：novel/anime/movie/gallery/fanwork/other 或中文",
            "limit": "返回条数（可选，默认 10，最大 50）",
        },
    )
)


async def _site_add_tool(args: Dict[str, str]) -> str:
    """site:add 工具入口：把作品上传到站内作品库。

    调用者 QQ 从 aitools 上下文拿（由 /ai 命令 ask handler 注入），
    不让模型在参数里传，避免 AI 给别人上传作品。
    流程：
    1. 校验调用者 QQ 在站点有账号 → 没有则驳回
    2. 后端 submit 内部检查同名作品 → 已有则返回 duplicate 提示
    3. 提交入库，uploader_id = 调用者
    """
    caller_qq = aitools.get_current_user_qq()
    if not caller_qq:
        return "错误：无法识别当前调用者身份（QQ 上下文未注入），请让用户改用 /安利 命令手动提交。"

    title = (args.get("title") or "").strip()
    if not title:
        return "错误：缺少必填参数 title（作品标题）。"

    payload: Dict[str, object] = {
        "qq": caller_qq,
        "uploader_qq": caller_qq,  # 特权模式：调用者即上传者，后端会校验其在站点有账号
        "title": title,
    }
    author = (args.get("author") or "").strip()
    if author:
        payload["author"] = author
    wtype = (args.get("type") or "").strip()
    if wtype:
        payload["type"] = _normalize_type(wtype)
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
        resp = await backend_client.post("/bot/works/submit", json=payload)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[ai site:add] 提交失败：{exc}")
        return f"错误：上传作品失败：{exc}。请如实告知用户暂时无法上传。"

    if not data.get("ok"):
        if data.get("duplicate"):
            w = data.get("work", {})
            return (
                f"站内已存在同名作品《{w.get('title')}》（ID={w.get('id')}），未重复入库。"
                f"请告知用户可到 {SITE_BASE_URL}/works/{w.get('id')} 标记支持。"
            )
        return f"上传失败：{data.get('message', '未知原因')}。请把这句话如实转告用户。"

    w = data.get("work", {})
    parts = [f"已上传：《{w.get('title')}》（ID={w.get('id')}）"]
    if w.get("author"):
        parts.append(f"作者：{w['author']}")
    if w.get("type"):
        parts.append(f"类型：{w['type']}")
    if w.get("summary"):
        parts.append(f"简介：{w['summary']}")
    parts.append(f"详情：{SITE_BASE_URL}/works/{w.get('id')}")
    return " · ".join(parts)


aitools.register_tool(
    aitools.Tool(
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
        handler=_site_add_tool,
        params={
            "title": "作品标题（必填）",
            "author": "作者（可选，建议先 web:search 查到再填）",
            "type": "类型（可选）：novel/anime/movie/gallery/fanwork/other 或中文",
            "summary": "一句话简介（可选，建议先 web:search 查到再填）",
            "tags": "标签（可选，逗号分隔，如：百合,连载中）",
            "links": '外链（可选，格式 "站点名=URL,站点名=URL"，如：起点=https://...）',
        },
    )
)
logger.info(
    f"[work] 已注册 site 工具包：{', '.join(t.fullname for t in aitools.registered_tools() if t.namespace == 'site')}"
)
