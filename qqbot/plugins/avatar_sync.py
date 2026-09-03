"""插件 #1b：avatar_sync —— 群成员头像同步（配合后端 /bot/members/sync_avatar）。

策略（事件驱动，启动不做全量拉取）：
  - 群友在站点注册：站点反向调 POST /bot/avatars/fetch {qq}
    → 本插件在 SYNC_GROUPS 中用 get_group_member_info 查该 QQ 头像
    → 推给后端 /bot/members/sync_avatar
  - 进群事件：顺手拉单个新成员头像并推送
  - SUPERUSER /sync_avatar：手动触发全量同步（补历史数据用）
  后端负责：下载头像 → 转存 zfile /avatars/ → 写 group_members / users

适配器兼容：
  - OneBotV11：get_group_member_info(group_id, user_id) → {"avatar": "https://q.qlogo.cn/..."}
  - QQ 官方：尽力调用同名字段；拿不到则跳过（不阻断其他成员）。
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from nonebot import get_driver, logger, on_command, on_notice
from nonebot.adapters import Bot, Event
from nonebot.permission import SUPERUSER
from nonebot.rule import to_me

from ._lib.client import backend_client
from .group_member_sync import SYNC_GROUPS, _fetch_group_member_list

_ENV_PATH = Path(__file__).resolve().parents[1] / ".env.prod"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH, override=False)

# 每次最多同步多少个头像（防限流，群大时先保量；剩余下次全量补）
AVATAR_SYNC_BATCH_LIMIT = int(os.getenv("AVATAR_SYNC_BATCH_LIMIT", "100") or "100")
# 逐个拉 member_info 的间隔，QQ 接口有限流
AVATAR_FETCH_INTERVAL = float(os.getenv("AVATAR_FETCH_INTERVAL", "0.3") or "0.3")


# ------------------ 核心 ------------------

async def _fetch_avatar_url(bot: Bot, group_id: str, qq: str) -> Optional[str]:
    """调 adapter 拉单个群成员头像 URL；失败返回 None（不抛异常）。"""
    is_onebot = "onebot" in type(bot).__name__.lower() or "onebot" in type(bot).__module__.lower()
    try:
        if is_onebot:
            info = await bot.call_api("get_group_member_info", group_id=int(group_id), user_id=int(qq))
        else:
            info = await bot.call_api("get_group_member_info", group_id=group_id, user_id=qq)
    except TypeError:
        # 部分 adapter 不接受 int
        try:
            info = await bot.call_api("get_group_member_info", group_id=group_id, user_id=qq)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[avatar_sync] get_group_member_info 失败：group={group_id} qq={qq} {exc}")
            return None
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[avatar_sync] get_group_member_info 失败：group={group_id} qq={qq} {exc}")
        return None

    if not info:
        return None
    d = info if isinstance(info, dict) else getattr(info, "model_dump", lambda: {})()
    avatar = d.get("avatar") or d.get("avatar_url") or d.get("face_url")
    return str(avatar) if avatar else None


async def sync_avatars(bot: Bot, group_ids: Optional[List[str]] = None, limit: int = AVATAR_SYNC_BATCH_LIMIT) -> Dict[str, Any]:
    """对目标群成员拉头像并批量推给后端。返回 {"ok", "fetched", "results"}。"""
    groups = group_ids or SYNC_GROUPS
    items: List[Dict[str, str]] = []
    seen: set[str] = set()

    for gid in groups:
        members = await _fetch_group_member_list(bot, gid)
        for m in members:
            qq = m["qq"]
            if not qq or qq in seen:
                continue
            seen.add(qq)
            avatar_url = await _fetch_avatar_url(bot, gid, qq)
            if avatar_url:
                items.append({"qq": qq, "avatar_url": avatar_url})
            if len(items) >= limit:
                break
        if len(items) >= limit:
            break

    if not items:
        logger.info(f"[avatar_sync] bot={bot.self_id} 未拉到任何头像，跳过。")
        return {"ok": True, "fetched": 0, "results": []}

    try:
        await asyncio.sleep(AVATAR_FETCH_INTERVAL)
        resp = await backend_client.post("/bot/members/sync_avatar/batch", json={"items": items})
        resp.raise_for_status()
        data = resp.json()
        details = data.get("details", {}) or {}
        logger.info(
            f"[avatar_sync] bot={bot.self_id} 头像同步完成：fetched={len(items)} "
            f"ok={details.get('ok')} failed={details.get('failed')}"
        )
        return {"ok": True, "fetched": len(items), "results": details.get("results", [])}
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[avatar_sync] 批量推送失败：bot={bot.self_id} err={exc}")
        return {"ok": False, "fetched": len(items), "results": [], "error": str(exc)}


async def full_avatar_sync() -> List[Dict[str, Any]]:
    """公共入口：对每个已连接 bot 执行一次头像同步。"""
    from nonebot import get_bots

    bots = get_bots()
    if not bots:
        logger.warning("[avatar_sync] 当前没有已连接的 bot，跳过头像同步。")
        return []
    results = []
    for bot in bots.values():
        results.append(await sync_avatars(bot))
    return results


# ------------------ 按需拉取（站点注册流程反向调用） ------------------

BOT_API_TOKEN = os.getenv("BOT_API_TOKEN", "")


async def fetch_avatar_for_qq(qq: str) -> Dict[str, Any]:
    """在 SYNC_GROUPS 中按需拉单个 QQ 的头像并推给后端。返回 {"ok", "avatar_url"?}。
    由站点注册流程反向调 POST /bot/avatars/fetch 触发。"""
    from nonebot import get_bots

    bots = get_bots()
    if not bots:
        logger.warning(f"[avatar_sync] 当前没有已连接的 bot，无法拉取头像：qq={qq}")
        return {"ok": False, "message": "no bot connected"}

    bot = next(iter(bots.values()))
    avatar_url: Optional[str] = None
    for gid in SYNC_GROUPS:
        avatar_url = await _fetch_avatar_url(bot, gid, qq)
        if avatar_url:
            break
    if not avatar_url:
        logger.warning(f"[avatar_sync] 未拉到头像：qq={qq}（不在目标群或接口受限）")
        return {"ok": False, "message": "avatar not found"}

    try:
        resp = await backend_client.post("/bot/members/sync_avatar", json={"qq": qq, "avatar_url": avatar_url})
        resp.raise_for_status()
        logger.info(f"[avatar_sync] 注册触发头像同步完成：qq={qq}")
        return {"ok": True, "avatar_url": avatar_url}
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[avatar_sync] 注册触发头像推送失败：qq={qq} err={exc}")
        return {"ok": False, "message": str(exc)}


def _register_fetch_route() -> bool:
    """在 fastapi driver 的 app 上注册 POST /bot/avatars/fetch（失败静默降级，与 auth_code 一致）。"""
    try:
        driver = get_driver()
        if not (hasattr(driver, "asgi") and hasattr(driver, "server_app")):
            logger.warning(
                "[avatar_sync] 当前 driver 不是 fastapi（缺少 asgi/server_app），跳过注册 /bot/avatars/fetch。"
                "请确认 .env 的 DRIVER 包含 ~fastapi。"
            )
            return False
        app = driver.asgi
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[avatar_sync] 注册路由失败（{exc}），跳过注册 /bot/avatars/fetch。")
        return False

    router = APIRouter()

    @router.post("/bot/avatars/fetch")
    async def avatars_fetch(request: Request) -> Any:
        if not BOT_API_TOKEN or request.headers.get("X-Bot-Token") != BOT_API_TOKEN:
            return JSONResponse(status_code=401, content={"ok": False, "message": "bad bot token"})
        data = await request.json()
        qq = str(data.get("qq") or "").strip()
        if not qq:
            return {"ok": False, "message": "qq required"}
        return await fetch_avatar_for_qq(qq)

    app.include_router(router)
    return True


_registered = _register_fetch_route()


# ------------------ 进群事件触发 ------------------

# 进群事件：顺便把新成员头像同步了（fire-and-forget）
_member_notice = on_notice(block=False)


@_member_notice.handle()
async def _handle_increase_for_avatar(bot: Bot, event: Event):
    notice_type = getattr(event, "notice_type", None)
    event_name = getattr(event, "event_name", None)
    is_increase = (
        notice_type == "group_member_increase"
        or (event_name and "increase" in event_name)
        or "Increase" in type(event).__name__
    )
    if not is_increase:
        return
    group_id = getattr(event, "group_id", None) or getattr(getattr(event, "group", None), "group_id", None)
    user_id = getattr(event, "user_id", None) or getattr(getattr(event, "user", None), "id", None)
    if not group_id or not user_id:
        return
    gid, qq = str(group_id), str(user_id)
    if SYNC_GROUPS and gid not in SYNC_GROUPS:
        return
    try:
        avatar_url = await _fetch_avatar_url(bot, gid, qq)
        if avatar_url:
            resp = await backend_client.post(
                "/bot/members/sync_avatar", json={"qq": qq, "avatar_url": avatar_url}
            )
            resp.raise_for_status()
            logger.info(f"[avatar_sync] 新成员 {qq} 头像已同步")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[avatar_sync] 新成员头像同步失败（不影响成员入库）：qq={qq} err={exc}")


# ------------------ 命令：管理员手动触发 ------------------

_avatar_cmd = on_command("sync_avatar", rule=to_me(), permission=SUPERUSER, block=True)


@_avatar_cmd.handle()
async def _avatar_cmd_handler(bot: Bot, event: Event):
    await bot.send(event, "开始同步群成员头像 …")
    results = await full_avatar_sync()
    lines = []
    for r in results:
        ok = r.get("ok")
        fetched = r.get("fetched", 0)
        failed = len([x for x in r.get("results", []) if not x.get("ok")])
        lines.append(f"{'OK' if ok else 'ERR'} fetched={fetched} failed={failed}")
    await bot.send(event, "头像同步结果：\n" + "\n".join(lines) if lines else "没有已连接的 bot。")


__all__ = ["sync_avatars", "full_avatar_sync", "fetch_avatar_for_qq"]
