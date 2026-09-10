"""命令实现：作品库（/work hot|search|add）。

对应原 plugins/works.py 的三条命令，迁移到 GNU 选项解析。
后端接口未变（hot/search/submit），仅 add 打通了 BotWorkSubmitIn 早已支持、
但旧插件从未传过的 author/type/summary/tags/links/source_work_id 字段。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote_plus

from dotenv import load_dotenv
from nonebot import logger
from nonebot.adapters import Bot, Event

from .._lib.bots import resolve_openid_qq
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
    """取发送者真实 QQ；无法识别返回 None。

    OneBot v11：get_user_id() 即真实 QQ。
    QQ 官方：get_user_id() 是 openid，查注册绑定映射换回真实 QQ。
    """
    try:
        uid = event.get_user_id()
    except Exception:  # noqa: BLE001
        uid = None
    if not uid:
        uid = getattr(event, "user_id", None) or getattr(getattr(event, "user", None), "id", None)
    if not uid:
        return None
    uid = str(uid)
    if uid.isdigit():
        return uid
    openid_type = "group" if getattr(event, "group_openid", None) else "c2c"
    return await resolve_openid_qq(uid, openid_type)


def _fmt_works(items: list, with_score: bool = False) -> str:
    if not items:
        return "暂无结果。"
    lines = []
    for i, w in enumerate(items, 1):
        author = f"（{w['author']}）" if w.get("author") else ""
        score = f" · 热度 {w['score']}" if with_score and w.get("score") is not None else ""
        lines.append(f"{i}. {w['title']}{author}{score}")
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
        ),
        handler="commands.work:add",
        notes=("提交者自动记为推荐人；同名作品已存在时返回提示，不重复入库。",),
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

    payload = {"qq": qq, "title": title}
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
    if data.get("created_user"):
        lines.append("（你还没有站点账号，已按群名片自动创建，首次登录用验证码即可）")
    lines.append(f"查看详情：{SITE_BASE_URL}/works/{w.get('id')}")
    await bot.send(event, "\n".join(lines))
