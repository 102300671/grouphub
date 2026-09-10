"""命令分发入口 —— 群机器人 GNU 命令体系的唯一 on_message 路由。

设计要点（见 docs/机器人命令规范.md §6.3）：
  - nonebot 的 block 是静态的、handler 内无法动态取消，因此「是不是命令」必须在
    rule 阶段判定：命中 → 响应并 block；未命中 → rule 返回 False 完全放行，
    不影响群友正常聊天与其他插件（如内置 echo）。
  - rule 阶段的匹配结果存入 state，handler 直接取用，避免重复解析。
  - 两个 matcher：/命令（priority=10）与裸验证码兜底（priority=11），均要求 to_me。

本模块加载时构建命令注册表（build_registry），必须在处理首条消息前完成。
"""
from __future__ import annotations

from nonebot import logger, on_message
from nonebot.adapters import Bot, Event
from nonebot.permission import SUPERUSER
from nonebot.rule import to_me
from nonebot.typing import T_State

from ._lib.cli import (
    CommandError,
    MessageMatch,
    build_registry,
    is_bare_code,
    load_handler,
    match_message,
    parse,
    render_command_help,
)
from .commands import auth as auth_cmd
from .commands import help as help_cmd
from .commands import sync as sync_cmd
from .commands import work as work_cmd

# ------------------ 启动时构建命令注册表 ------------------

_ALL_COMMANDS = (
    work_cmd.COMMANDS + auth_cmd.COMMANDS + sync_cmd.COMMANDS + help_cmd.COMMANDS
)
build_registry(_ALL_COMMANDS)
logger.info(f"[cli_router] 命令注册表已构建：{len(_ALL_COMMANDS)} 条命令")

_TO_ME = to_me()


def _plaintext(event: Event) -> str:
    try:
        return event.get_plaintext()
    except Exception:  # noqa: BLE001
        return ""


# ------------------ matcher 1：/命令 ------------------

async def _is_command(bot: Bot, event: Event, state: T_State) -> bool:
    """rule：是否为已注册命令（或命中命名空间的错误命令）。"""
    if event.get_type() != "message":
        return False
    if not await _TO_ME(bot, event, state):
        return False
    match = match_message(_plaintext(event))
    if match.kind == "none":
        return False
    state["_cli_match"] = match
    return True


_cli = on_message(rule=_is_command, priority=10, block=True)


@_cli.handle()
async def _dispatch(bot: Bot, event: Event, state: T_State) -> None:
    match: MessageMatch = state.get("_cli_match")
    if match is None:
        return

    # 命中命名空间但子命令未知 / 引号错误等
    if match.kind == "error":
        await bot.send(event, match.message)
        return

    command = match.command
    try:
        result = parse(command, match.args)
    except CommandError as exc:
        await bot.send(event, exc.message)
        return

    # -h/--help 优先
    if result.help_requested:
        await bot.send(event, render_command_help(command))
        return

    # 管理员命令权限校验（与原 SUPERUSER 行为一致：QQ 官方通道 openid 不在 superusers，
    # 故管理员命令实际经 OneBot 通道触发）
    if command.admin_only and not await SUPERUSER(bot, event):
        await bot.send(event, f"⚠️ {command.display} 仅管理员可用")
        return

    try:
        handler = load_handler(command.handler)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[cli_router] 加载 handler 失败：{command.handler} -> {exc}")
        await bot.send(event, "⚠️ 命令内部错误，请联系管理员。")
        return

    try:
        await handler(bot, event, result)
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"[cli_router] 命令执行异常：{command.canonical}")
        await bot.send(event, f"⚠️ 执行出错：{exc}")


# ------------------ matcher 2：裸验证码兜底 ------------------

async def _is_bare_code(bot: Bot, event: Event, state: T_State) -> bool:
    """rule：@机器人 直接发一串 4~8 位数字（注册绑定兜底通道）。"""
    if event.get_type() != "message":
        return False
    if not await _TO_ME(bot, event, state):
        return False
    code = is_bare_code(_plaintext(event))
    if code is None:
        return False
    state["_bare_code"] = code
    return True


_bare = on_message(rule=_is_bare_code, priority=11, block=True)


@_bare.handle()
async def _bare_handler(bot: Bot, event: Event, state: T_State) -> None:
    code = state.get("_bare_code")
    if not code:
        return
    try:
        await auth_cmd.bind_bare_code(bot, event, code)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[cli_router] 裸验证码核销异常")
        await bot.send(event, f"⚠️ 验证失败：{exc}")
