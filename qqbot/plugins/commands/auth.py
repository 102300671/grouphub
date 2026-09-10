"""命令实现：账号（/auth bind）。

注册绑定码核销。逻辑迁自原 plugins/auth_code.py 的 on_command("绑定")，
改为 GNU 选项解析（-c/--code），并保留裸数字兜底通道。

HTTP 路由（/bot/auth/send-code、/bot/auth/notify-login）仍在 auth_code.py，
本模块只负责命令交互。
"""
from __future__ import annotations

from typing import Optional

from nonebot import logger
from nonebot.adapters import Bot, Event

from .._lib.bots import get_group_name_by_openid, is_onebot_v11
from .._lib.cli import Command, Option, ParseResult
from .._lib.client import backend_client
from ..group_member_sync import SYNC_GROUPS

COMMANDS = (
    Command(
        ns_en="auth", ns_zh="账号", sub_en="bind", sub_zh="绑定",
        quick=("绑定", "bind"),
        summary="注册绑定码核验",
        brief="-c <码>",
        usage="--code <验证码> [选项]",
        examples=("/绑定 -c 123456", "/bind --code 123456"),
        options=(
            Option(long="code", short="c", value_name="<码>", required=True,
                   help="4~8 位验证码（必填）"),
        ),
        handler="commands.auth:bind",
        notes=("也可 @机器人 直接发一串验证码（不带命令），机器人同样会核销。",),
    ),
)


async def bind(bot: Bot, event: Event, result: ParseResult) -> None:
    """命令：/绑定 -c <验证码>。"""
    code = result.get("code")
    await _verify_code(bot, event, code)


async def bind_bare_code(bot: Bot, event: Event, code: str) -> None:
    """裸数字兜底：群友直接发一串 4~8 位数字。"""
    await _verify_code(bot, event, code)


async def _verify_code(bot: Bot, event: Event, code: str) -> None:
    if not code:
        return

    # 身份识别（双适配器）
    qq: Optional[str] = None
    openid: Optional[str] = None
    openid_type: Optional[str] = None
    group_id: Optional[str] = str(getattr(event, "group_id", None) or "") or None
    group_openid: Optional[str] = None
    group_name: Optional[str] = None
    nickname_in_group = None
    if is_onebot_v11(bot):
        uid = event.get_user_id()
        qq = uid if uid else None
        sender = getattr(event, "sender", None)
        nickname_in_group = getattr(sender, "card", None) or getattr(sender, "nickname", None)
        if group_id:
            try:
                info = await bot.call_api("get_group_info", group_id=int(group_id))
                group_name = getattr(info, "group_name", None) or (
                    info.get("group_name") if isinstance(info, dict) else None
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[auth bind] 查群名称失败（OneBot）：{exc}")
    else:  # QQ 官方：只有 openid，群号用 SYNC_GROUPS 兜底
        openid = event.get_user_id() or None
        group_openid = str(getattr(event, "group_openid", None) or "") or None
        openid_type = "group" if group_openid else "c2c"
        group_id = group_id or (SYNC_GROUPS[0] if SYNC_GROUPS else None)
        if group_openid:
            group_name = await get_group_name_by_openid(bot, group_openid)
        nickname_in_group = getattr(getattr(event, "author", None), "username", None)

    # 群消息 best-effort 撤回验证码，避免被群友看到冒用（仅 OneBot 支持）
    if group_id and is_onebot_v11(bot):
        message_id = getattr(event, "message_id", None)
        if message_id is not None:
            try:
                await bot.call_api("delete_msg", message_id=message_id)
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[auth bind] 撤回验证码消息失败（忽略）：{exc}")

    payload = {"code": code}
    if qq:
        payload["qq"] = qq
    if openid:
        payload.update({"openid": openid, "openid_type": openid_type})
    if group_openid:
        payload["group_openid"] = group_openid
    if group_id:
        payload["group_id"] = group_id
    if group_name:
        payload["group_name"] = str(group_name)
    if nickname_in_group:
        payload["nickname_in_group"] = str(nickname_in_group)

    try:
        resp = await backend_client.post("/bot/auth/verify-register", json=payload)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[auth bind] 注册绑定核验失败：{exc}")
        detail = ""
        if hasattr(exc, "response") and getattr(exc.response, "content", None):
            try:
                detail = exc.response.json().get("detail", "")
            except Exception:  # noqa: BLE001
                detail = ""
        await bot.send(event, f"⚠️ 验证失败：{detail or exc}")
        return

    if not data.get("ok"):
        await bot.send(event, f"⚠️ 验证失败：{data.get('message', '未知错误')}")
        return

    logger.info(f"[auth bind] 绑定码核销成功：qq={data.get('qq')} bound_openid={data.get('bound_openid')}")
    if data.get("account_existed"):
        await bot.send(event, f"✅ 绑定成功！QQ {data.get('qq')} 已关联机器人官方通道，回到站点即可生效。")
    else:
        await bot.send(
            event,
            f"✅ 验证通过！QQ {data.get('qq')} 已加入白名单"
            + ("，并完成 openid 绑定" if data.get("bound_openid") else "")
            + "。\n现在可以回站点用 QQ 号 + 密码登录了。",
        )
