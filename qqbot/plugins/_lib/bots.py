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
from nonebot.adapters import Bot, Event

from .client import backend_client


def _unwrap_bot(bot: Bot) -> Bot:
    """AtSenderBot 包装对象 → 取出内部真实适配器 bot；其余原样返回。

    cli_router 会把进入命令处理的 bot 统一包成 AtSenderBot，
    类型判定/底层 openapi 调用必须对内部真实 bot 进行，
    否则 type(bot).__module__ 命中 plugins._lib.bots 造成误判
    （历史 bug：群名接口因此恒抛 TypeError，群名一直回退 openid）。
    """
    return getattr(bot, "raw_bot", bot)


def is_qq_official(bot: Bot) -> bool:
    """判断 bot 是否来自 QQ 官方适配器（主用）。自动解包 AtSenderBot。"""
    return type(_unwrap_bot(bot)).__module__.startswith("nonebot.adapters.qq")


def is_onebot_v11(bot: Bot) -> bool:
    """判断 bot 是否来自 OneBot v11 适配器（备用）。自动解包 AtSenderBot。"""
    return type(_unwrap_bot(bot)).__module__.startswith("nonebot.adapters.onebot")


class AtSenderBot:
    """包装 bot，让所有 send 调用自动 @ 消息发送者。

    OneBot v11：用原生 at_sender=True。
    QQ 官方：群被动回复（send 带 msg_id=event.id）平台会自动 @ 触发者，
             不能再手动插 mention_user，否则群聊出现双 @（@用户名 + <@openid>），
             因此直接透传；私聊本来就没有 @。
    其余适配器：透传，不 @。
    所有非 send 方法/属性透传给原始 bot（call_api、get 等）。
    """

    def __init__(self, bot: Bot, event: Any) -> None:
        self._bot = bot
        self._event = event

    @property
    def raw_bot(self) -> Bot:
        """内部真实适配器 bot（供类型判定与底层 openapi 调用解包）。"""
        return self._bot

    async def send(self, event: Any, message: Any, **kwargs: Any) -> Any:
        if is_onebot_v11(self._bot):
            kwargs.setdefault("at_sender", True)
            return await self._bot.send(event, message, **kwargs)
        # QQ 官方群被动回复已由平台自动 @ 发送者，手动 @ 会导致双 @，故注释：
        # if is_qq_official(self._bot):
        #     message = _prepend_mention_qq(event, message)
        return await self._bot.send(event, message, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._bot, name)


# QQ 官方手动 @ 逻辑已停用：群被动回复平台自带 @，插入 mention_user 反而双 @。
# 如需恢复，取消下面注释并在 AtSenderBot.send 中重新调用即可。
# def _prepend_mention_qq(event: Any, message: Any) -> Any:
#     """QQ 官方适配器：在消息前插入 mention_user 段。"""
#     try:
#         from nonebot.adapters.qq import Message as QQMessage
#         from nonebot.adapters.qq import MessageSegment as QQSegment
#
#         user_id = ""
#         try:
#             user_id = event.get_user_id() or ""
#         except Exception:  # noqa: BLE001
#             pass
#         if not user_id:
#             return message
#         mention = QQSegment.mention_user(user_id)
#         if isinstance(message, QQMessage):
#             return QQMessage(mention) + message
#         if isinstance(message, str):
#             return QQMessage(mention) + QQMessage(QQSegment.text(message))
#         # MessageSegment 或其他
#         return QQMessage(mention) + QQMessage(message)
#     except Exception:  # noqa: BLE001
#         return message


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

# value: (时间戳, (qq, is_active))；qq 为 None=未注册；is_active 为 None=状态未知（放行）
_openid_cache: Dict[Tuple[str, str], Tuple[float, Optional[Tuple[Optional[str], Optional[bool]]]]] = {}
_OPENID_CACHE_TTL_SECONDS = 600.0


async def resolve_sender_info(event: Event) -> Optional[Tuple[Optional[str], Optional[bool]]]:
    """从消息事件解析发送者 (真实QQ, 账号是否启用)；无法识别通道返回 None。

    OneBot v11：get_user_id() 即真实 QQ（纯数字）。
    QQ 官方：get_user_id() 是 openid（非数字），查注册绑定映射换回真实 QQ。
    两种通道都经后端 resolve-openid 取 is_active（被管理员禁用的用户拦截在命令入口）。
    供 cli_router 的 require_registered 统一校验、各 handler 复用。
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
    openid_type = "group" if getattr(event, "group_openid", None) else "c2c"
    return await resolve_openid_info(uid, openid_type)


async def resolve_sender_qq(event: Event) -> Optional[str]:
    """resolve_sender_info 的旧接口：只取真实 QQ。"""
    info = await resolve_sender_info(event)
    return info[0] if info else None


async def resolve_openid_info(
    openid: str, openid_type: str = "group"
) -> Optional[Tuple[Optional[str], Optional[bool]]]:
    """把发送者标识解析为 (真实QQ, is_active)。

    查询后端 GET /bot/auth/resolve-openid，结果（含未绑定）缓存 10 分钟。
    OneBot 通道入参本身是纯数字 QQ，后端会直接按 QQ 查 users.is_active。
    网络失败时不缓存，返回 None（调用方按「无法识别」处理，不锁死命令）。
    """
    key = (openid, openid_type)
    now = time.monotonic()
    cached = _openid_cache.get(key)
    if cached is not None and now - cached[0] < _OPENID_CACHE_TTL_SECONDS:
        return cached[1]
    info: Optional[Tuple[Optional[str], Optional[bool]]] = None
    try:
        resp = await backend_client.get(
            "/bot/auth/resolve-openid",
            params={"openid": openid, "openid_type": openid_type},
        )
        resp.raise_for_status()
        body = resp.json()
        info = (body.get("qq") or None, body.get("is_active"))
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[bots] openid 解析失败：type={openid_type} err={exc}")
        return None
    _openid_cache[key] = (now, info)
    return info


async def resolve_openid_qq(openid: str, openid_type: str = "group") -> Optional[str]:
    """resolve_openid_info 的旧接口：只取真实 QQ 号。"""
    info = await resolve_openid_info(openid, openid_type)
    return info[0] if info else None


# ------------------ QQ 官方 openapi 通用请求 ------------------

async def qq_openapi_request(
    bot: Bot,
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    timeout: float = 10.0,
) -> Any:
    """直调 QQ 官方 openapi（适配器未封装的接口，如 /v2/groups/.../info、/v2/panels）。

    鉴权：bot.get_access_token()（复用适配器的 token 缓存与自动刷新），
    Header `Authorization: QQBot {access_token}`。
    仅支持 QQ 官方适配器 bot；返回解析后的 JSON（无响应体时返回 None）；
    非 2xx 抛 RuntimeError（带官方错误码与描述）。
    """
    bot = _unwrap_bot(bot)
    if not is_qq_official(bot):
        raise TypeError("qq_openapi_request 仅支持 QQ 官方适配器 bot")

    import httpx

    token = await bot.get_access_token()
    api_base = str(bot.adapter.get_api_base()).rstrip("/")
    url = f"{api_base}{path}"
    headers = {"Authorization": f"QQBot {token}"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.request(
            method.upper(), url, params=params, json=json_body, headers=headers
        )
    if resp.status_code >= 400:
        detail = resp.text[:200]
        code = ""
        try:
            body = resp.json()
            detail = body.get("message") or body.get("Message") or detail
            code = str(body.get("code") or body.get("Code") or "")
        except Exception:  # noqa: BLE001
            pass
        prefix = f"[{code}] " if code else ""
        raise RuntimeError(
            f"QQ openapi {method.upper()} {path} 失败：HTTP {resp.status_code} {prefix}{detail}"
        )
    if not resp.content:
        return None
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return None


# ------------------ QQ 官方通道：群 openid → 群名称 ------------------

_group_name_cache: Dict[str, Tuple[float, Optional[str]]] = {}
_GROUP_NAME_CACHE_TTL = 3600.0       # 成功结果缓存 1 小时
_GROUP_NAME_FAIL_TTL = 60.0         # 失败结果只缓存 1 分钟，避免瞬时故障锁死 1 小时


async def get_group_name_by_openid(bot: Bot, group_openid: str) -> Optional[str]:
    """QQ 官方通道查群名称（openapi GET /v2/groups/{group_openid}/info）。

    适配器（nonebot-adapter-qq 1.7.x）未封装该 API，call_api 会抛 ApiNotAvailable，
    因此走 qq_openapi_request 直调（需机器人有接口权限；失败返回 None 并记 warning
    日志）。成功结果缓存 1 小时，失败结果只缓存 1 分钟。
    """
    now = time.monotonic()
    cached = _group_name_cache.get(group_openid)
    if cached is not None:
        ttl = _GROUP_NAME_CACHE_TTL if cached[1] else _GROUP_NAME_FAIL_TTL
        if now - cached[0] < ttl:
            return cached[1]

    name: Optional[str] = None
    raw_bot = _unwrap_bot(bot)
    # 先试适配器封装（未来版本支持时自动生效）
    try:
        info = await raw_bot.call_api("get_group_info", group_openid=group_openid)
        name = getattr(info, "group_name", None) or (
            info.get("group_name") if isinstance(info, dict) else None
        )
    except Exception:  # noqa: BLE001（当前版本必抛 ApiNotAvailable，忽略）
        pass

    # 直调 openapi
    if not name:
        # 直调失败后重试一次（瞬时错误常见：token 刷新 / 网络抖动）
        for _ in range(2):
            try:
                data = await qq_openapi_request(
                    raw_bot, "GET", f"/v2/groups/{group_openid}/info"
                )
                name = (data or {}).get("group_name") or None
                if name:
                    break
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[bots] 官方通道查群名称失败：openid={group_openid} err={exc}")
                name = None

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
    "resolve_openid_info",
    "resolve_sender_info",
    "qq_openapi_request",
    "get_group_name_by_openid",
]
