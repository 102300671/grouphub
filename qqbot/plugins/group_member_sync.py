"""插件 #1：group_member_sync —— 群成员白名单实时同步。

对齐 PRD §6.2：
  - 启动时 + 每 N 小时：全量拉群成员 → batch_upsert + mark_inactive_others
  - 监听进群/退群事件：upsert_one / set_inactive
  - 命令行（管理员）：`/sync_member <group_id|all>` 手动触发一次全量重同步

适配器兼容策略（MVP 阶段对两种主流适配器做事件映射）：
  - OneBotV11（nonebot-adapter-onebot）：
      进群  = notice.group_member_increase
      退群  = notice.group_member_decrease
      名片 = notice.group_card（部分实现）
  - QQ 官方 Bot（nonebot-adapters-qq）：
      进群  = GroupMemberIncreaseEvent
      退群  = GroupMemberDecreaseEvent
    （QQ 官方开放平台事件名以 nonebot-adapters-qq 为准；如没有对应事件，仍可靠全量同步兜底）
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from nonebot import get_driver, logger, on_command, on_notice, require
from nonebot.adapters import Bot, Event
from nonebot.permission import SUPERUSER
from nonebot.rule import to_me

from ._lib.client import backend_client

_ENV_PATH = Path(__file__).resolve().parents[1] / ".env.prod"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH, override=False)


SYNC_GROUPS_RAW = os.getenv("SYNC_GROUPS", "").strip()
SYNC_GROUPS: List[str] = [g.strip() for g in SYNC_GROUPS_RAW.split(",") if g.strip()]
FULL_SYNC_INTERVAL_HOURS = int(os.getenv("FULL_SYNC_INTERVAL_HOURS", "6") or "6")
BOT_API_TOKEN = os.getenv("BOT_API_TOKEN", "")


def _parse_groups_arg(group_ids: Optional[str]) -> List[str]:
    if not group_ids:
        return list(SYNC_GROUPS)
    if group_ids in ("all", "*"):
        return list(SYNC_GROUPS)
    return [g.strip() for g in group_ids.split(",") if g.strip()]


# ------------------ 通用：把「某 adapter 的某条群成员」归一化 ------------------

def _normalize_member(raw: Dict[str, Any]) -> Dict[str, str]:
    """
    接收 adapter 返回的群成员对象（dict），抽取：
      - qq：用户的 ID（OneBotV11 是 user_id，QQ 官方是 tiny_id/member_openid... 这里按场景做兼容）
      - nickname_in_group：群名片 / 昵称
    插件侧和站点都把 qq 当成字符串，避免数字精度问题。
    """
    qq = (
        raw.get("user_id")
        or raw.get("tiny_id")
        or raw.get("member_openid")
        or raw.get("openid")
        or raw.get("id")
        or raw.get("user_openid")
    )
    nickname = (
        raw.get("card")
        or raw.get("nickname")
        or raw.get("nick")
        or raw.get("member_name")
        or raw.get("user_name")
    )
    return {"qq": str(qq) if qq else "", "nickname_in_group": str(nickname) if nickname else None}


# ------------------ 全量同步（对某 bot + 某 group） ------------------

async def _fetch_group_member_list(bot: Bot, group_id: str) -> List[Dict[str, str]]:
    """调用 bot 对应适配器的「获取群成员列表」接口。

    - OneBotV11：get_group_member_list(group_id=group_id)
    - QQ 官方：目前适配器没有统一 list 接口，此处按常见实现试调用 `get_group_members`；
      如果失败则返回空列表，由 batch_upsert 的 mark_inactive_others=false 选项来避免误清。
    """
    adapter_name = type(bot).__name__.lower()
    adapter_module = type(bot).__module__.lower()  # 例如 'nonebot.adapters.onebot.v11.bot'
    is_onebot = "onebot" in adapter_name or "onebot" in adapter_module
    members_raw: Iterable[Any] = []

    try:
        if is_onebot:
            # onebot 的返回是 list[dict]，无分页
            members_raw = await bot.call_api("get_group_member_list", group_id=group_id) or []
        else:
            # 其它适配器（QQ 官方等）：尽力调用；若没暴露 API，退化为空
            try:
                members_raw = await bot.get_group_members(group_id=group_id) or []  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[{adapter_name}] 未暴露 get_group_members: {exc}")
                members_raw = []
    except Exception as exc:  # noqa: BLE001
        logger.error(f"获取群成员列表失败：bot={bot.self_id} group={group_id}: {exc}")
        return []

    normalized: List[Dict[str, str]] = []
    for m in members_raw:
        # 不同适配器返回可能是对象也可能是 dict
        d = m if isinstance(m, dict) else getattr(m, "model_dump", lambda: {})()
        info = _normalize_member(d)
        if info["qq"]:
            normalized.append(info)
    return normalized


async def _sync_one_group(bot: Bot, group_id: str, mark_inactive_others: bool = True) -> Dict[str, Any]:
    """对单个群执行全量同步 → 调 backend /bot/members/batch_upsert。"""
    members = await _fetch_group_member_list(bot, group_id)
    if not members and mark_inactive_others:
        logger.warning(
            f"[group_member_sync] 群 {group_id} 拉到 0 个成员，为避免误清所有群友，"
            f"本次强制 mark_inactive_others=false。检查 bot 连接与权限。"
        )
        mark_inactive_others = False

    payload = {
        "group_id": group_id,
        "members": [
            {"group_id": group_id, "qq": m["qq"], "nickname_in_group": m.get("nickname_in_group")}
            for m in members
        ],
        "mark_inactive_others": mark_inactive_others,
    }
    try:
        resp = await backend_client.post("/bot/members/batch_upsert", json=payload)
        resp.raise_for_status()
        data = resp.json()
        logger.info(
            f"[group_member_sync] 全量同步完成：group={group_id} "
            f"upserted={data.get('details', {}).get('upserted_count')} "
            f"marked_inactive={data.get('details', {}).get('marked_inactive_count')}"
        )
        return {"ok": True, "group_id": group_id, **(data.get("details", {}) or {})}
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[group_member_sync] 全量同步失败：group={group_id} {exc}")
        return {"ok": False, "group_id": group_id, "error": str(exc)}


async def full_sync(groups: Optional[List[str]] = None, mark_inactive_others: bool = True) -> List[Dict[str, Any]]:
    """公共入口：对所有 bot + 目标群做一次全量同步。

    会遍历所有已连接的 bot，每个 bot 尝试同步 groups（默认 SYNC_GROUPS）里的每个群。
    """
    # 懒加载，避免 import 循环
    from nonebot import get_bots

    bots = get_bots()
    if not bots:
        logger.warning("[group_member_sync] 当前没有已连接的 bot，跳过全量同步。")
        return [{"ok": False, "error": "no bot connected"}]

    groups = groups or SYNC_GROUPS
    if not groups:
        logger.warning("[group_member_sync] SYNC_GROUPS 为空，跳过全量同步。请在 .env 中配置。")
        return [{"ok": False, "error": "no groups configured"}]

    results: List[Dict[str, Any]] = []
    for bot in bots.values():
        for gid in groups:
            results.append(
                await _sync_one_group(bot, gid, mark_inactive_others=mark_inactive_others)
            )
    return results


# ------------------ 启动时 + 定时全量同步 ------------------

_driver = get_driver()


@_driver.on_bot_connect
async def _startup_full_sync(_: Bot):
    """机器人连上后延迟 10s 再跑，避免一上线 QQ 端限流 / 还没握手。"""
    await asyncio.sleep(10)
    await full_sync()


# 定时：nonebot_plugin_apscheduler 如果装了就每 N 小时来一次
try:
    scheduler = require("nonebot_plugin_apscheduler").scheduler  # type: ignore[attr-defined]

    @scheduler.scheduled_job("interval", hours=FULL_SYNC_INTERVAL_HOURS, id="group_member_sync.full")
    async def _scheduled_full_sync():
        logger.info(f"[group_member_sync] 定时全量同步启动（每 {FULL_SYNC_INTERVAL_HOURS} 小时）")
        await full_sync()

except Exception as exc:  # noqa: BLE001
    logger.info(
        f"[group_member_sync] 定时任务未启用（通常是未装 nonebot_plugin_apscheduler：{exc}）。"
        f"仅会在 bot 连接时执行一次全量同步；若需周期性同步，请 `pip install nonebot-plugin-apscheduler`。"
    )


# ------------------ 单条 upsert / set_inactive ------------------

async def _upsert_one(group_id: str, qq: str, nickname_in_group: Optional[str]) -> bool:
    try:
        resp = await backend_client.post(
            "/bot/members/upsert_one",
            json={"group_id": group_id, "qq": qq, "nickname_in_group": nickname_in_group},
        )
        resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[group_member_sync] upsert_one 失败：group={group_id} qq={qq} err={exc}")
        return False


async def _set_inactive(group_id: str, qq: str) -> bool:
    try:
        resp = await backend_client.post(
            "/bot/members/set_inactive",
            json={"group_id": group_id, "qq": qq},
        )
        resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[group_member_sync] set_inactive 失败：group={group_id} qq={qq} err={exc}")
        return False


# ------------------ 事件：进群 / 退群 ------------------
# 用 on_notice 通吃；在 handler 内部按 event type/event_name 做分发，避免依赖具体 adapter。

_member_notice = on_notice(block=False)


@_member_notice.handle()
async def _handle_member_change(bot: Bot, event: Event):
    """统一分发：进群 → upsert；退群 → set_inactive。"""
    event_class = type(event).__name__
    event_name = getattr(event, "event_name", None)  # OneBotV11 有 event_name
    notice_type = getattr(event, "notice_type", None)
    sub_type = getattr(event, "sub_type", None) or getattr(event, "change_type", None)
    group_id = (
        getattr(event, "group_id", None)
        or getattr(getattr(event, "group", None), "group_id", None)
    )
    user_id = (
        getattr(event, "user_id", None)
        or getattr(event, "operator_id", None)
        or getattr(getattr(event, "user", None), "tiny_id", None)
        or getattr(getattr(event, "user", None), "member_openid", None)
        or getattr(getattr(event, "user", None), "openid", None)
        or getattr(getattr(event, "user", None), "id", None)
    )
    nickname = (
        getattr(event, "card", None)
        or getattr(getattr(event, "user", None), "nickname", None)
        or getattr(getattr(event, "user", None), "member_name", None)
        or None
    )

    if not group_id or not user_id:
        return  # 不是群成员事件

    qq = str(user_id)
    gid = str(group_id)

    # 判定事件类型
    is_increase = False
    is_decrease = False

    if notice_type == "group_member_increase" or (event_name and "increase" in event_name):
        is_increase = True
    elif notice_type == "group_member_decrease" or (event_name and "decrease" in event_name):
        is_decrease = True
    elif "Increase" in event_class:  # QQ 官方：GroupMemberIncreaseEvent
        is_increase = True
    elif "Decrease" in event_class:
        is_decrease = True

    if not is_increase and not is_decrease:
        return

    # 仅处理 SYNC_GROUPS 白名单内的群
    if SYNC_GROUPS and gid not in SYNC_GROUPS:
        return

    if is_increase:
        await _upsert_one(gid, qq, str(nickname) if nickname else None)
    if is_decrease:
        await _set_inactive(gid, qq)


# ------------------ 命令：管理员手动重同步 ------------------

_sync_cmd = on_command("sync_member", rule=to_me(), permission=SUPERUSER, block=True)


@_sync_cmd.handle()
async def _sync_cmd_handler(bot: Bot, event: Event):
    """命令格式：/sync_member [all|group_id,group_id]

    示例：
      /sync_member                 → 同步 .env 里配置的 SYNC_GROUPS
      /sync_member all             → 同步所有
      /sync_member 123456,789012   → 同步指定两个群
    """
    # OneBotV11 的 command 参数可从 get_args / message / plaintext 拿，兼容写法：
    plain = (
        getattr(event, "message", None)
        and event.get_plaintext()  # type: ignore[union-attr]
    ) or ""
    # 去掉命令前缀
    for prefix in ("/sync_member", "sync_member"):
        if plain.startswith(prefix):
            plain = plain[len(prefix):].strip()
            break
    groups = _parse_groups_arg(plain or None)

    if not groups:
        await bot.send(event, "未配置 SYNC_GROUPS，也没传群号，无法同步。")
        return
    await bot.send(event, f"开始手动重同步，目标群 {groups} …")
    results = await full_sync(groups)
    lines = [
        f"{'OK' if r.get('ok') else 'ERR'} 群 {r.get('group_id','?')} "
        f"up={r.get('upserted_count','-')} off={r.get('marked_inactive_count','-')}"
        + (f" err={r.get('error','')}" if not r.get("ok") else "")
        for r in results
    ]
    await bot.send(event, "同步结果：\n" + "\n".join(lines))


# ------------------ HTTP 接口：管理员后台手动触发（来自 backend /admin/trigger_member_sync） ------------------

def _register_admin_routes() -> bool:
    """注册 POST /bot/admin/trigger_sync，鉴权 X-Bot-Token，由后端管理后台反向调用。

    与 auth_code / avatar_sync 同样要求 fastapi driver；缺少则静默跳过。
    """
    try:
        driver = get_driver()
        if not (hasattr(driver, "asgi") and hasattr(driver, "server_app")):
            logger.warning(
                "[group_member_sync] 当前 driver 不是 fastapi，跳过注册 /bot/admin/trigger_sync。"
            )
            return False
        app = driver.asgi
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[group_member_sync] 注册 /bot/admin/trigger_sync 失败：{exc}")
        return False

    router = APIRouter()

    @router.post("/bot/admin/trigger_sync")
    async def trigger_sync(request: Request) -> Any:
        if not BOT_API_TOKEN or request.headers.get("X-Bot-Token") != BOT_API_TOKEN:
            return JSONResponse(status_code=401, content={"ok": False, "message": "bad bot token"})
        # 允许传 group_ids，缺省为 SYNC_GROUPS
        try:
            data = await request.json()
        except Exception:
            data = {}
        group_ids = data.get("group_ids") if isinstance(data, dict) else None
        if isinstance(group_ids, str):
            groups = _parse_groups_arg(group_ids)
        elif isinstance(group_ids, list):
            groups = [str(g).strip() for g in group_ids if str(g).strip()]
        else:
            # 缺省（空 body / null）→ .env 配置的 SYNC_GROUPS
            groups = list(SYNC_GROUPS)
        if not groups:
            return {"ok": False, "message": "no groups configured"}
        # fire-and-forget：在后台跑，不阻塞 HTTP 响应
        import asyncio

        async def _run():
            results = await full_sync(groups)
            logger.info(f"[group_member_sync] 外部触发同步完成：{results}")

        asyncio.create_task(_run())
        return {"ok": True, "message": "triggered", "groups": groups}

    app.include_router(router)
    return True


_registered = _register_admin_routes()


__all__ = ["full_sync"]
