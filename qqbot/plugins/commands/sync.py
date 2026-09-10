"""命令实现：同步（/sync member|avatar）。仅超级用户。

复用 group_member_sync.full_sync 与 avatar_sync.full_avatar_sync，
本模块只负责 GNU 选项解析与结果回显。--force 接到 full_sync 的
mark_inactive_others（拉到 0 个成员时是否仍标记其余人退群）。
"""
from __future__ import annotations

from typing import List, Optional

from nonebot.adapters import Bot, Event

from .._lib.cli import Command, Option, ParseResult
from ..avatar_sync import full_avatar_sync
from ..group_member_sync import SYNC_GROUPS, _onebot_enabled, full_sync

_GROUP_OPTION = Option(
    long="group", short="g", value_name="<群号>", repeatable=True,
    help="目标群，可重复；缺省为 .env 的 SYNC_GROUPS",
)
_ALL_OPTION = Option(
    long="all", value_name=None,
    help="同步 SYNC_GROUPS 配置的全部群",
)

COMMANDS = (
    Command(
        ns_en="sync", ns_zh="同步", sub_en="member", sub_zh="成员",
        summary="重同步群成员白名单",
        brief="--all",
        usage="[选项]",
        examples=("/同步 成员 --all", "/sync member -g 123456 -g 789012", "/sync member --all --force"),
        options=(
            _GROUP_OPTION,
            _ALL_OPTION,
            Option(long="force", value_name=None,
                   help="拉到 0 个成员时仍标记其余人退群（危险，默认关闭）"),
        ),
        handler="commands.sync:member",
        admin_only=True,
    ),
    Command(
        ns_en="sync", ns_zh="同步", sub_en="avatar", sub_zh="头像",
        summary="同步群成员头像",
        brief="--all",
        usage="[选项]",
        examples=("/同步 头像 --all", "/sync avatar -g 123456 -n 50"),
        options=(
            _GROUP_OPTION,
            _ALL_OPTION,
            Option(long="limit", short="n", value_name="<数>", kind="int",
                   min_value=1, help="本次最多同步多少个，默认取 AVATAR_SYNC_BATCH_LIMIT"),
        ),
        handler="commands.sync:avatar",
        admin_only=True,
    ),
)


def _resolve_groups(result: ParseResult) -> Optional[List[str]]:
    """-g 可重复 / --all / 缺省 三种来源合并为目标群列表。

    返回 None 表示「用 .env 的 SYNC_GROUPS」（交给底层函数默认）。
    """
    groups: List[str] = list(result.get("group") or [])
    if result.get("all"):
        groups.extend(SYNC_GROUPS)
    # 去重保序
    seen = set()
    uniq = [g for g in groups if not (g in seen or seen.add(g))]
    return uniq or None


async def member(bot: Bot, event: Event, result: ParseResult) -> None:
    groups = _resolve_groups(result)
    if not groups and not SYNC_GROUPS:
        await bot.send(event, "未配置 SYNC_GROUPS，也没传群号，无法同步。")
        return
    if not _onebot_enabled():
        await bot.send(
            event,
            "OneBot v11 未启用（QQ 官方适配器无群成员列表 API），无法同步群成员。\n"
            "请配置 NapCat 等 OneBot 实现后重试。",
        )
        return
    force = bool(result.get("force"))
    target = groups or SYNC_GROUPS
    await bot.send(event, f"开始手动重同步，目标群 {target} …")
    # force=True：即使某群拉到 0 个成员也照标记其余人退群（绕过底层防误清保护）
    results = await full_sync(target, mark_inactive_others=True, force=force)
    lines = [
        f"{'OK' if r.get('ok') else 'ERR'} 群 {r.get('group_id', '?')} "
        f"up={r.get('upserted_count', '-')} off={r.get('marked_inactive_count', '-')}"
        + (f" err={r.get('error', '')}" if not r.get("ok") else "")
        for r in results
    ]
    await bot.send(event, "同步结果：\n" + "\n".join(lines))


async def avatar(bot: Bot, event: Event, result: ParseResult) -> None:
    groups = _resolve_groups(result)
    limit = result.get("limit")
    await bot.send(event, "开始同步群成员头像 …")
    if groups or limit:
        # full_avatar_sync 不接受参数，这里走底层 sync_avatars 以支持 -g/-n
        from .._lib.bots import onebot_bots
        from ..avatar_sync import sync_avatars
        bots = onebot_bots()
        if not bots:
            await bot.send(event, "没有已连接的 OneBot v11 bot，无法同步头像。")
            return
        results = []
        for b in bots:
            kwargs = {}
            if groups:
                kwargs["group_ids"] = groups
            if limit:
                kwargs["limit"] = limit
            results.append(await sync_avatars(b, **kwargs))
    else:
        results = await full_avatar_sync()

    lines = []
    for r in results:
        ok = r.get("ok")
        fetched = r.get("fetched", 0)
        failed = len([x for x in r.get("results", []) if not x.get("ok")])
        lines.append(f"{'OK' if ok else 'ERR'} fetched={fetched} failed={failed}")
    await bot.send(event, "头像同步结果：\n" + "\n".join(lines) if lines else "没有已连接的 bot。")
