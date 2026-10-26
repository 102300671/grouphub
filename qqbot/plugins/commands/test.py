"""【临时 · M0 前置核实】QQ 通道渲染测试命令。

**验完即删** —— 连同 `cli_router.py` 里的注册行一起移除。

## 为什么分组成子命令（重要）

QQ 开放平台官方口径（发送群聊消息 / 消息收发概述）：

    被动消息  群聊  有效期 5 分钟  **每条消息最多回复 5 次**
    错误码 40034128「被动回复时间或次数超限」

所以**一次 @ 最多只能被动回 5 条**。第一轮 `/test` 一次发 7 条、第 6 条就撞上
`40034128`；第二轮 12 条更早挂。适配器的 `event._reply_seq` 递增是对的
（`msg_seq` 照官方要求递增），**问题纯粹是次数上限**。

于是拆成 4 组，每组 ≤ 5 条：

    /test      ① 图片段 URL 直发（+ 其它组提示）
    /test 2    ② markdown 段（富文本渲染能力）
    /test 3    ②b 嵌图对照（4 个直链变体，带转存校验）
    /test 4    ③ 纯文本 markdown 源码 + ④ 观察清单

## ②b 为什么用 force_verify_image_resource

官方 `MessageMarkdown` 有一个字段：

    force_verify_image_resource  是否校验图片转存结果，为 true 时若图片转存失败
                                 则返回错误、消息不会发送；默认 false

默认 false 时，**图片转存失败是静默的** —— 消息照发，图片位置只留 alt 文本。
这正是第一轮看到的现象（只显示 `[blocks]`）。

所以 ②b 走**原始 HTTP API**（适配器没暴露这个字段），把 `force_verify` 打开：
转存成功 → 图出来了；转存失败 → 平台直接返回错误码，我们把错误码报出来。
**失败的那条不占回复额度**（消息根本没发出去），所以 4 个变体最多占 4 次。

用法：任何人 @机器人 发 `/test [组号]`，机器人把测试内容**回发给发命令的那个人**。
"""
from __future__ import annotations

import os

import httpx
from nonebot.adapters import Bot, Event

from .._lib.cli import Command, ParseResult

# 公网可达的测试图（云服务器 nginx → /var/www/html/m0test/）。
# 必须是公网直链：手机 proot 在 NAT 后面，QQ 服务端拉不到。
IMAGE_URL = os.getenv("M0_TEST_IMAGE", "http://59.110.163.88/m0test/blocks.png")

# 嵌图对照组：同一个 markdown 图片语法，只换直链
_EMBED_VARIANTS = (
    ("A 裸 IP + http（第一轮失败的对照组）", IMAGE_URL),
    ("B 域名 + http", "http://jianying.run.place/m0test/blocks.png"),
    ("C 域名 + https（自签证书，信号偏弱）", "https://jianying.run.place:8443/m0test/blocks.png"),
    ("D 外部公网 https（与本机无关的对照组）", "https://www.python.org/static/img/python-logo.png"),
)

TEST_COMMAND = Command(
    ns_en="test",
    ns_zh="测试",
    quick=("test", "测试"),
    summary="【临时】M0 通道渲染测试",
    brief="[组号]",
    usage="[组号]（1 图片段 / 2 markdown段 / 3 嵌图对照 / 4 纯文本）",
    examples=("/test", "/test 2", "/test 3", "/test 4"),
    handler="commands.test:test",
    allow_positional=True,
)

COMMANDS = (TEST_COMMAND,)

_GROUPS_HINT = "其余组：/test 2（markdown段）· /test 3（嵌图对照）· /test 4（纯文本）"


# ------------------ 适配器差异 ------------------


def _raw_bot(bot: Bot) -> Bot:
    """AtSenderBot 包装 → 内部真实适配器 bot（类型判定用）。"""
    return getattr(bot, "raw_bot", bot)


def _module(bot: Bot) -> str:
    return type(_raw_bot(bot)).__module__


def _is_qq_official(bot: Bot) -> bool:
    return _module(bot).startswith("nonebot.adapters.qq")


def _image_segment(bot: Bot, url: str):
    """按适配器构造图片段。

    QQ 官方：`image(url)` → msg_type=7 + `{"file_type":1,"url":...}`，
    QQ 服务端自己去拉这张图（**不经过机器人**，所以直链必须公网可达）。
    OneBot v11：同理由 OneBot 侧拉取。
    """
    module = _module(bot)
    if module.startswith("nonebot.adapters.qq"):
        from nonebot.adapters.qq import MessageSegment as QQSegment

        return QQSegment.image(url)
    if module.startswith("nonebot.adapters.onebot"):
        from nonebot.adapters.onebot.v11 import MessageSegment as OneBotSegment

        return OneBotSegment.image(url)
    return None


# ------------------ 原始 API 发 markdown（带转存校验） ------------------


async def _send_md_force_verify(bot: Bot, event: Event, content: str) -> tuple[bool, str]:
    """用 `force_verify_image_resource=true` 直接调原始 API 发 markdown。

    适配器的 `post_group_messages` 没暴露这个字段，所以走原始 HTTP。
    返回 (是否发出, 说明)。**失败时消息根本没发出去，不占被动回复额度。**
    """
    raw = _raw_bot(bot)
    if not _is_qq_official(bot):
        return False, "当前通道不是 QQ 官方，跳过"
    group_openid = getattr(event, "group_openid", None)
    msg_id = getattr(event, "id", None)
    if not group_openid or not msg_id:
        return False, "拿不到 group_openid / msg_id，跳过"

    # msg_seq 与适配器共用同一个计数器，避免 msg_id+msg_seq 重复被去重
    seq = int(getattr(event, "_reply_seq", 0)) + 1
    try:
        event._reply_seq = seq
    except Exception:  # noqa: BLE001
        pass

    try:
        token = await raw.get_access_token()
        url = raw.adapter.get_api_base() / "v2" / "groups" / group_openid / "messages"
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                url,
                json={
                    "msg_type": 2,
                    "markdown": {"content": content},
                    "msg_id": msg_id,
                    "msg_seq": seq,
                    "force_verify_image_resource": True,
                },
                headers={"Authorization": f"QQBot {token}"},
            )
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        return False, f"请求异常：{exc}"

    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code} {data.get('message', '')}"
    if data.get("code"):
        return False, f"平台报错 code={data['code']} {data.get('message', '')}"
    return True, "已发出（图片转存成功，应该能看到图）"


# ------------------ 内容 ------------------


def _md_block(url: str) -> str:
    """② 的 markdown 源码（QQ 官方 markdown 段专用）。"""
    return "\n".join(
        [
            "### ② markdown 段（msg_type=2）",
            "",
            "**粗体** · *斜体* · ~~删除线~~ · `行内代码`",
            "",
            "嵌图语法（第一轮这里只出了 `[blocks]`，见 /test 3）：",
            "",
            f"![blocks]({url})",
            "",
            "代码块：",
            "",
            "```python",
            "def m0():",
            '    return "hello"',
            "```",
            "",
            "表格：",
            "",
            "| 场景 | 期望效果 |",
            "| --- | --- |",
            "| 嵌图 | 显示为图片 |",
            "| 代码块 | 等宽字体 + 底色 |",
            "| 表格 | 网格线 |",
        ]
    )


def _embed_variant_md(label: str, url: str) -> str:
    """②b 单个嵌图变体：标签与图片放在同一条 markdown 里。

    图片若转存失败，平台会直接拒发（不会只留 alt 文本），
    这样"哪条没出来"就是确定的。
    """
    return "\n".join(
        [
            f"**②b 嵌图对照 · {label}**",
            "",
            f"![t]({url})",
        ]
    )


def _text_source(url: str) -> str:
    """③ 的纯文本源码（markdown 语法原样发出，看 QQ 自己会不会解析）。"""
    return "\n".join(
        [
            "### ③ 纯文本通道（markdown 源码原样发出）",
            "",
            "下面这些是**源码**，看 QQ 客户端会不会自己解析：",
            "",
            f"![blocks]({url})",
            "",
            "```python",
            "def m0():",
            '    return "hello"',
            "```",
            "",
            "| 场景 | 期望效果 |",
            "| --- | --- |",
            "| 嵌图 | 显示为图片 |",
            "| 代码块 | 等宽字体 + 底色 |",
            "| 表格 | 网格线 |",
        ]
    )


async def _who(event: Event) -> str:
    """尽量报出真实 QQ 号；拿不到就退回平台 user_id。"""
    try:
        from .._lib.bots import resolve_sender_info

        info = await resolve_sender_info(event)
        if info and info[0]:
            return str(info[0])
    except Exception:  # noqa: BLE001
        pass
    try:
        return str(event.get_user_id())
    except Exception:  # noqa: BLE001
        return "未知"


# ------------------ 各组 ------------------


async def _group_img(bot: Bot, event: Event) -> None:
    """① 图片段 URL 直发。"""
    await bot.send(
        event,
        f"① 图片段 · URL 直发（QQ 服务端自己去拉这张图）\n直链：{IMAGE_URL}\n{_GROUPS_HINT}",
    )
    seg = _image_segment(bot, IMAGE_URL)
    if seg is None:
        await bot.send(event, "（当前通道不支持图片段，本组跳过）")
    else:
        await bot.send(event, seg)


async def _group_md(bot: Bot, event: Event) -> None:
    """② markdown 段的富文本渲染能力。"""
    await bot.send(event, f"② markdown 段（msg_type=2）\n{_GROUPS_HINT}")
    if _is_qq_official(bot):
        from nonebot.adapters.qq import MessageSegment as QQSegment

        await bot.send(event, QQSegment.markdown(_md_block(IMAGE_URL)))
    else:
        await bot.send(event, "（当前通道是 OneBot v11，没有 markdown 段，本组跳过）")


async def _group_embed(bot: Bot, event: Event) -> None:
    """②b 嵌图对照：4 个直链变体，逐个带转存校验发。

    成功的会出图；失败的平台直接拒发（不占额度），最后一条汇总说明谁失败。
    """
    results = []
    for label, url in _EMBED_VARIANTS:
        ok, note = await _send_md_force_verify(bot, event, _embed_variant_md(label, url))
        results.append((label, ok, note))

    lines = [
        "②b 嵌图对照结果（同一段 markdown，只换直链）：",
        "",
        "· **转存成功 = 出了图**；转存失败时平台直接拒发，所以**没出图的那条就是失败**。",
        "",
    ]
    for label, ok, note in results:
        lines.append(f"· {label}\n  {'✅ ' if ok else '❌ '}{note}")
    lines.append("")
    lines.append(_GROUPS_HINT)
    await bot.send(event, "\n".join(lines))


async def _group_text(bot: Bot, event: Event) -> None:
    """③ 纯文本 markdown 源码 + ④ 观察清单。"""
    await bot.send(event, _text_source(IMAGE_URL))
    await bot.send(
        event,
        "\n".join(
            [
                "④ 观察清单（把结果填进去回报）",
                "",
                "③ 文本里 ![](url)：变图片 / 原样文字",
                "③ 代码块：等宽带底色 / 原样反引号",
                "③ 表格：网格线 / 竖线对齐 / 原样竖线",
                "",
                _GROUPS_HINT,
                "",
                "⚠ 临时命令，验完会删。",
            ]
        ),
    )


# ------------------ 命令主体 ------------------


async def test(bot: Bot, event: Event, result: ParseResult) -> None:
    """按组把测试内容回发给发命令的那个人。"""
    group = ((result.positional or ["1"])[0] or "1").strip().lower()
    if group in ("1", "a", "img", "图片"):
        await _group_img(bot, event)
    elif group in ("2", "b", "md", "markdown"):
        await _group_md(bot, event)
    elif group in ("3", "c", "embed", "嵌图"):
        await _group_embed(bot, event)
    elif group in ("4", "d", "text", "纯文本"):
        await _group_text(bot, event)
    else:
        await bot.send(
            event,
            f"⚠️ 没有第 {group} 组。\n\n"
            "/test（图片段）· /test 2（markdown段）· /test 3（嵌图对照）· /test 4（纯文本）",
        )
