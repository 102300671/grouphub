"""【临时 · M0 前置核实】QQ 通道渲染测试命令。

**验完即删** —— 连同 `cli_router.py` 里的注册行一起移除。

用途（2026-10-26，M0 前置核实）：实测 QQ 通道对 4 种渲染方式的支持情况，
结论回填 `.ai/STATUS.md` 与 `.ai/FACTS.md`。

第一轮实测结论（2026-10-26 上午，用户截图回报）：

  ① 图片段 URL 直发        → ✅ 正常显示，QQ 服务端能自己拉公网图
  ② markdown 段            → ✅ 群聊可用！粗体/斜体/删除线/行内代码/代码块/表格
                              全部正确渲染成富文本
  ② markdown 嵌图          → ❌ `![alt](url)` 只显示 `[alt]`，图片与 URL 都没出现
                              —— 解析器认得图片语法（否则会原样显示 `![alt](url)`），
                                 但图片渲染不出来

所以第二轮加一组**嵌图对照组**：同一个 markdown 图片语法，只换图片直链，
用来区分「QQ 根本不支持 markdown 嵌图」和「QQ 拉不了这个直链」。

  A 裸 IP + http（第一轮失败的对照组）
  B 域名 + http
  C 域名 + https（自签证书，信号偏弱 —— 失败可能只是 TLS 被拒）
  D 外部公网 https（与本机无关的对照组）

用法：任何人 @机器人 发 `/test`，机器人把测试内容**回发给发命令的那个人**——
谁测谁看结果，多人可同时测，互不干扰。

图片直链必须在公网可达（QQ 服务端要自己去拉）。当前用云服务器 59.110.163.88
上 nginx 提供的 `/m0test/`，可用环境变量 `M0_TEST_IMAGE` 覆盖。
"""
from __future__ import annotations

import os

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
    brief="",
    usage="",
    examples=("/test",),
    handler="commands.test:test",
)

COMMANDS = (TEST_COMMAND,)


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


def _md_block(url: str) -> str:
    """② 的 markdown 源码（QQ 官方 markdown 段专用）。"""
    return "\n".join(
        [
            "### ② markdown 段（QQ 官方适配器独有，msg_type=2）",
            "",
            "**粗体** · *斜体* · ~~删除线~~ · `行内代码`",
            "",
            "嵌图语法：",
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

    图片若渲染不出来，标签仍在，结果不会歧义。
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


# ------------------ 命令主体 ------------------


async def test(bot: Bot, event: Event, result: ParseResult) -> None:
    """把测试内容回发给发命令的那个人。"""
    who = await _who(event)

    await bot.send(
        event,
        "\n".join(
            [
                "🔬 M0 通道渲染测试（第 2 轮）",
                f"发起人：{who}",
                f"图片直链：{IMAGE_URL}",
                "",
                "请依次看下面几组内容，记录 QQ 客户端里的**实际显示效果**。",
                "没显示 / 裂图 / 报错，也都是结论 —— 截图回报最直观。",
            ]
        ),
    )

    # ① 图片段：URL 直发（第一轮已验证 ✅，保留作对照）
    await bot.send(event, "① 图片段 · URL 直发（QQ 服务端自己去拉这张图）")
    seg = _image_segment(bot, IMAGE_URL)
    if seg is None:
        await bot.send(event, "（当前通道不支持图片段，本组跳过）")
    else:
        await bot.send(event, seg)

    # ② markdown 段：第一轮已验证富文本可用，这里保持原样作稳定对照
    await bot.send(event, "② markdown 段（QQ 官方适配器独有）")
    if _is_qq_official(bot):
        from nonebot.adapters.qq import MessageSegment as QQSegment

        await bot.send(event, QQSegment.markdown(_md_block(IMAGE_URL)))
    else:
        await bot.send(event, "（当前通道是 OneBot v11，没有 markdown 段，本组跳过）")

    # ②b 嵌图对照组：只换直链，定位"为什么嵌图不出来"
    if _is_qq_official(bot):
        from nonebot.adapters.qq import MessageSegment as QQSegment

        await bot.send(
            event,
            "②b 嵌图对照组 · 同一段 markdown，只换图片直链。"
            "A 是第一轮失败的那个，B/C 换了域名和 https，D 是与本机无关的外部图。",
        )
        for label, url in _EMBED_VARIANTS:
            await bot.send(event, QQSegment.markdown(_embed_variant_md(label, url)))
    else:
        await bot.send(event, "②b 嵌图对照组：（当前通道没有 markdown 段，本组跳过）")

    # ③ 纯文本：markdown 源码原样发出
    await bot.send(event, _text_source(IMAGE_URL))

    # ④ 观察清单
    await bot.send(
        event,
        "\n".join(
            [
                "④ 观察清单（把结果填进去回报）",
                "",
                "① 图片段：显示 / 不显示 / 裂图",
                "② markdown 段：渲染成富文本 / 显示源码 / 报错 / 不显示",
                "②b-A 裸IP http：出图 / 只显示 [t] / 什么都没有",
                "②b-B 域名 http：出图 / 只显示 [t] / 什么都没有",
                "②b-C 域名 https（自签）：出图 / 只显示 [t] / 什么都没有",
                "②b-D 外部公网图：出图 / 只显示 [t] / 什么都没有",
                "③ 文本里 ![](url)：变图片 / 原样文字",
                "③ 代码块：等宽带底色 / 原样反引号",
                "③ 表格：网格线 / 竖线对齐 / 原样竖线",
                "",
                "⚠ 临时命令，验完会删。",
            ]
        ),
    )
