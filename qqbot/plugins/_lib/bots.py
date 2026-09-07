"""适配器选择公共库 —— QQ 官方适配器为主用，OneBot v11 为备用。

QQ 官方开放平台的能力边界（决定各能力的降级路径）：
  - 群消息只有 @机器人 触发（c2c_group_at_messages intent），回复必须带 msg_id（被动回复）
  - 拿不到真实 QQ 号，只有 openid（member_openid / user_openid）
    → openid ↔ 真实 QQ 的映射由注册绑定流程建立（后端 qq_openid_bindings 表），
      本库提供 resolve_openid_qq() 查询（带 TTL 缓存）
  - 没有群成员列表 / 群成员信息 API → 成员同步、头像校验做不了
  - 单聊主动消息受平台限制（多数机器人只能被动回复）→ 私聊发码优先走 OneBot
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from nonebot import get_bots, logger
from nonebot.adapters import Bot

from .client import backend_client


def is_qq_official(bot: Bot) -> bool:
    """判断 bot 是否来自 QQ 官方适配器（主用）。"""
    return type(bot).__module__.startswith("nonebot.adapters.qq")


def is_onebot_v11(bot: Bot) -> bool:
    """判断 bot 是否来自 OneBot v11 适配器（备用）。"""
    return type(bot).__module__.startswith("nonebot.adapters.onebot")


def all_bots() -> List[Bot]:
    return list(get_bots().values())


def qq_official_bots() -> List[Bot]:
    """已连接的 QQ 官方适配器 bot（主用）。"""
    return [b for b in all_bots() if is_qq_official(b)]


def onebot_bots() -> List[Bot]:
    """已连接的 OneBot v11 bot（备用）。"""
    return [b for b in all_bots() if is_onebot_v11(b)]


async def send_private(qq: str, message: str) -> Dict[str, Any]:
    """私聊发消息（按真实 QQ 号寻址）。

    顺序：所有 OneBot bot（唯一能按真实 QQ 号发私聊的通道）→
    QQ 官方 bot 兜底（send_to_c2c 主动消息，多数账号会被平台拒绝）。

    返回 {"ok": bool, "via": "onebot"|"qq"|"none", "message": str}。
    """
    last_err = "no bot connected"
    for bot in onebot_bots():
        try:
            await bot.call_api(
                "send_private_msg", user_id=int(qq) if qq.isdigit() else qq, message=message
            )
            return {"ok": True, "via": "onebot", "message": "sent"}
        except Exception as exc:  # noqa: BLE001
            last_err = f"onebot({bot.self_id}): {exc}"
            logger.warning(f"[bots] OneBot 私聊失败，尝试下一个通道：qq={qq} err={exc}")

    for bot in qq_official_bots():
        try:
            # 官方平台按 openid 寻址，真实 QQ 号直接当 openid 传基本必失败，
            # 这里仅作为「只连了官方适配器」时的兜底尝试并给出明确错误。
            await bot.send_to_c2c(qq, message)
            return {"ok": True, "via": "qq", "message": "sent"}
        except Exception as exc:  # noqa: BLE001
            last_err = f"qq({bot.self_id}): {exc}"
            logger.warning(f"[bots] QQ 官方私聊失败：qq={qq} err={exc}")

    logger.error(f"[bots] 私聊发送失败（无可用通道）：qq={qq} last_err={last_err}")
    return {"ok": False, "via": "none", "message": last_err}


# ------------------ openid → 真实 QQ 解析（带 TTL 缓存） ------------------

_openid_cache: Dict[Tuple[str, str], Tuple[float, Optional[str]]] = {}
_OPENID_CACHE_TTL_SECONDS = 600.0


async def resolve_openid_qq(openid: str, openid_type: str = "group") -> Optional[str]:
    """把 QQ 官方平台的 openid 解析为真实 QQ 号（注册绑定时建立映射）。

    查询后端 GET /bot/auth/resolve-openid，结果（含未绑定）缓存 10 分钟。
    解析失败/未绑定返回 None。
    """
    key = (openid, openid_type)
    now = time.monotonic()
    cached = _openid_cache.get(key)
    if cached is not None and now - cached[0] < _OPENID_CACHE_TTL_SECONDS:
        return cached[1]
    qq: Optional[str] = None
    try:
        resp = await backend_client.get(
            "/bot/auth/resolve-openid",
            params={"openid": openid, "openid_type": openid_type},
        )
        resp.raise_for_status()
        qq = resp.json().get("qq") or None
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[bots] openid 解析失败：type={openid_type} err={exc}")
    _openid_cache[key] = (now, qq)
    return qq


# ------------------ QQ 官方通道：群 openid → 群名称 ------------------

_group_name_cache: Dict[str, Tuple[float, Optional[str]]] = {}


async def get_group_name_by_openid(bot: Bot, group_openid: str) -> Optional[str]:
    """QQ 官方通道查群名称（openapi GET /v2/groups/{group_openid}/info）。

    适配器（nonebot-adapter-qq 1.7.x）未封装该 API，call_api 会抛 ApiNotAvailable，
    因此用 bot.get_access_token() + httpx 直调 openapi（与官方文档一致，
    需机器人有接口权限；失败返回 None 并记 debug 日志）。结果缓存 1 小时。
    """
    now = time.monotonic()
    cached = _group_name_cache.get(group_openid)
    if cached is not None and now - cached[0] < 3600.0:
        return cached[1]

    name: Optional[str] = None
    # 先试适配器封装（未来版本支持时自动生效）
    try:
        info = await bot.call_api("get_group_info", group_openid=group_openid)
        name = getattr(info, "group_name", None) or (
            info.get("group_name") if isinstance(info, dict) else None
        )
    except Exception:  # noqa: BLE001（当前版本必抛 ApiNotAvailable，忽略）
        pass

    # 直调 openapi
    if not name:
        try:
            import httpx

            token = await bot.get_access_token()
            api_base = str(bot.adapter.get_api_base()).rstrip("/")
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{api_base}/v2/groups/{group_openid}/info",
                    headers={"Authorization": f"QQBot {token}"},
                )
                resp.raise_for_status()
                name = resp.json().get("group_name") or None
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[bots] 官方通道查群名称失败：openid={group_openid} err={exc}")

    name = str(name) if name else None
    _group_name_cache[group_openid] = (now, name)
    return name


__all__ = [
    "is_qq_official",
    "is_onebot_v11",
    "all_bots",
    "qq_official_bots",
    "onebot_bots",
    "send_private",
    "resolve_openid_qq",
    "get_group_name_by_openid",
]
