"""插件 #3 + #4：works_recommend / works_submit —— 群内「热门 / 搜索 / 安利」命令。

命令（@机器人 或 /前缀，视 COMMAND_START 配置）：
  /热门           → 热门作品 Top5（后端 /bot/works/hot）
  /搜索 <关键词>  → 关键词搜作品（后端 /bot/works/search）
  /安利 <作品名>  → 把作品入库，提交者自动挂 recommender（后端 /bot/works/submit）
                    提交者 QQ 无站点账号时后端自动建号（随机密码 + 群名片昵称）

适配器：QQ 官方为主用（群 @ 消息触发，bot.send 被动回复），
OneBot v11 为备用（@ 或 / 前缀触发）。
身份：OneBot 通道自带真实 QQ；官方通道只有 openid，会查注册绑定时建立的
openid ↔ QQ 映射（未绑定则提示先去站点完成注册绑定）。

配置：.env 的 SITE_BASE_URL 用于消息里的详情跳转链接。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from nonebot import logger, on_command
from nonebot.adapters import Bot, Event
from nonebot.rule import to_me

from ._lib.bots import resolve_openid_qq
from ._lib.client import backend_client
_ENV_PATH = Path(__file__).resolve().parents[1] / ".env.prod"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH, override=False)

SITE_BASE_URL = os.getenv("SITE_BASE_URL", "http://127.0.0.1:5173").rstrip("/")


def _cmd_args(event: Event, prefixes: List[str]) -> str:
    """从事件里取命令后的参数文本。"""
    plain = (getattr(event, "message", None) and event.get_plaintext()) or ""  # type: ignore[union-attr]
    for prefix in prefixes:
        if plain.startswith(prefix):
            return plain[len(prefix):].strip()
    return plain.strip()


async def _sender_qq(event: Event) -> Optional[str]:
    """取发送者真实 QQ（兼容双适配器）；无法识别返回 None。

    OneBot v11：event.get_user_id() → 真实 QQ 号。
    QQ 官方：event.get_user_id() → openid，查注册绑定映射换成真实 QQ。
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


# ------------------ /热门 ------------------

_hot = on_command("热门", aliases={"hot"}, rule=to_me(), block=True)


@_hot.handle()
async def _hot_handler(bot: Bot, event: Event):
    try:
        resp = await backend_client.get("/bot/works/hot", params={"limit": 5})
        resp.raise_for_status()
        items = resp.json().get("items", [])
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[works] 热门查询失败：{exc}")
        await bot.send(event, f"⚠️ 查询失败：{exc}")
        return
    body = _fmt_works(items, with_score=True)
    if items:
        body += f"\n详情见站点：{SITE_BASE_URL}/works"
    await bot.send(event, f"📚 热门作品 Top{len(items)}：\n{body}")


# ------------------ /搜索 <关键词> ------------------

_search = on_command("搜索", aliases={"search"}, rule=to_me(), block=True)


@_search.handle()
async def _search_handler(bot: Bot, event: Event):
    keyword = _cmd_args(event, ["/搜索", "搜索"])
    if not keyword:
        await bot.send(event, "用法：/搜索 <关键词>\n例：/搜索 诡秘之主")
        return
    try:
        resp = await backend_client.get("/bot/works/search", params={"keyword": keyword, "limit": 10})
        resp.raise_for_status()
        items = resp.json().get("items", [])
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[works] 搜索失败：{exc}")
        await bot.send(event, f"⚠️ 搜索失败：{exc}")
        return
    body = _fmt_works(items)
    if items:
        body += f"\n详情见站点：{SITE_BASE_URL}/works?keyword={keyword}"
    await bot.send(event, f"🔍 「{keyword}」搜索结果：\n{body}")


# ------------------ /安利 <作品名> ------------------

_submit = on_command("安利", aliases={"recommend", "安利作品"}, rule=to_me(), block=True)


@_submit.handle()
async def _submit_handler(bot: Bot, event: Event):
    title = _cmd_args(event, ["/安利", "安利"])
    if not title:
        await bot.send(event, "用法：/安利 <作品名>\n例：/安利 诡秘之主\n提交后会自动入库并记你为推荐人。")
        return
    qq = await _sender_qq(event)
    if not qq:
        await bot.send(event, "⚠️ 无法识别你的真实 QQ：请先在站点完成注册绑定（注册页会给你一个验证码，发给机器人即可），再使用安利。")
        return
    try:
        resp = await backend_client.post("/bot/works/submit", json={"qq": qq, "title": title})
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[works] 提交失败：{exc}")
        await bot.send(event, f"⚠️ 提交失败：{exc}")
        return

    if not data.get("ok"):
        if data.get("duplicate"):
            w = data.get("work", {})
            await bot.send(event, f"📖 已存在同名作品：{w.get('title')}（ID={w.get('id')}）\n可到站点标记支持：{SITE_BASE_URL}/works/{w.get('id')}")
        else:
            await bot.send(event, f"⚠️ {data.get('message', '提交失败')}")
        return

    w = data.get("work", {})
    lines = [f"📖 已安利入库：{w.get('title')}（ID={w.get('id')}）"]
    if data.get("created_user"):
        lines.append("（你还没有站点账号，已按群名片自动创建，首次登录用验证码即可）")
    lines.append(f"查看详情：{SITE_BASE_URL}/works/{w.get('id')}")
    await bot.send(event, "\n".join(lines))


__all__ = []
