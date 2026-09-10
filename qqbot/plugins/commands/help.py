"""命令实现：/帮助 /help —— 命令发现。

无参数 → 分组列出全部命令；带命令名 → 显示该命令完整用法。
help 是元命令，允许位置参数（如 `/帮助 安利`），对齐 GNU 的 `man ls`/`git help commit`。
"""
from __future__ import annotations

from nonebot.adapters import Bot, Event

from .._lib import cli
from .._lib.cli import Command, ParseResult, render_catalog, render_command_help

HELP_COMMAND = Command(
    ns_en="help", ns_zh="帮助",
    quick=("帮助", "help"),
    summary="查看全部命令",
    brief="[命令名]",
    usage="[命令名]",
    examples=("/帮助", "/help 安利", "/help work add", "/help recommend"),
    handler="commands.help:help",
    allow_positional=True,
)

# 供 cli_router 收集；命名与其他模块统一为 COMMANDS
COMMANDS = (HELP_COMMAND,)


def _resolve_target(name: str) -> Command | None:
    """把 `/帮助 <命令名>` 的命令名解析为 Command。

    支持：中文/英文快捷别名（安利 / recommend）、中文路径（作品 安利）、
    英文路径（work add）、单 token 命名空间（作品）。
    """
    name = name.strip().lstrip("/")
    if not name:
        return None
    parts = tuple(name.split())
    # 直接命中触发名（含快捷别名、中英混搭）
    cmd = cli._LOOKUP.get(parts)
    if cmd:
        return cmd
    # 单 token：可能是命名空间或子命令别名，逐个触发名比对
    if len(parts) == 1:
        token = parts[0]
        for cmd in cli.COMMANDS:
            for trig in cmd.trigger_names:
                if trig[-1] == token or trig[0] == token:
                    return cmd
    return None


async def help(bot: Bot, event: Event, result: ParseResult) -> None:
    target = (result.positional or [""])[0] if result.positional else ""
    if not target:
        await bot.send(event, render_catalog(cli.COMMANDS))
        return
    cmd = _resolve_target(target)
    if cmd is None:
        await bot.send(
            event,
            f"⚠️ 没有「{target}」这个命令。\n\n输入 /帮助 查看全部命令。",
        )
        return
    await bot.send(event, render_command_help(cmd))
