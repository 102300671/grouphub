"""命令实现：/渲染 —— 文本 / Markdown / HTML 渲染成图片，网页截图。

命令（内容即位置参数；内容以 - 开头时用 -- 分隔，如 `/渲染 md -- - 列表项`）：
  /渲染 <内容>          文本转图片（默认）
  /渲染 md <内容>       Markdown 转图片
  /渲染 html <内容>     HTML 转图片
  /渲染 网页 <url>      网页整页截图

AI 会话用的同名工具（web:text / web:md / web:html / web:screenshot）已搬到
仓库根 shared/aitools/，与 Web 端共用一份实现；渲染宽度与下面的命令共用
RENDER_WIDTHS 一处真源。
"""
from __future__ import annotations

from nonebot import logger
from nonebot.adapters import Bot, Event

from shared.aitools.tools.web import RENDER_WIDTHS

from .._lib.cli import Command, CommandError, Option, ParseResult
from .._lib.renderer import (
    render_html_img,
    render_md_img,
    render_text_img,
    screenshot_url,
    send_image,
)

# ------------------ 命令定义 ------------------

_COMMON_OPTIONS = (
    Option(
        long="width", short="w", value_name="<像素>", kind="int",
        default=0, min_value=200, max_value=1920,
        help="图片宽度像素，默认按类型自动",
    ),
)

COMMANDS = (
    Command(
        ns_en="render", ns_zh="渲染",
        quick=("渲染", "render"),
        summary="文本渲染成图片",
        brief="<内容>",
        usage="<内容> [-w <像素>]",
        examples=("/渲染 欢迎加入本群", "/渲染 这是一段很长的文本…"),
        options=_COMMON_OPTIONS,
        handler="commands.render:text",
        allow_positional=True,
    ),
    Command(
        ns_en="render", ns_zh="渲染", sub_en="md", sub_zh="md",
        quick=("渲染md", "md渲染"),
        summary="Markdown 渲染成图片",
        brief="<内容>",
        usage="<Markdown 内容> [-w <像素>]",
        examples=("/渲染 md # 标题\n- 列表项", "/渲染md **加粗** 文本"),
        options=_COMMON_OPTIONS,
        handler="commands.render:md",
        allow_positional=True,
    ),
    Command(
        ns_en="render", ns_zh="渲染", sub_en="html", sub_zh="html",
        quick=("渲染html",),
        summary="HTML 渲染成图片",
        brief="<内容>",
        usage="<HTML 内容> [-w <像素>]",
        examples=("/渲染 html <h1>标题</h1><p>正文</p>", "/渲染html <b>加粗</b>"),
        options=_COMMON_OPTIONS,
        handler="commands.render:html",
        allow_positional=True,
    ),
    Command(
        ns_en="render", ns_zh="渲染", sub_en="网页", sub_zh="网页",
        quick=("截图", "shot", "网页截图"),
        summary="网页整页截图",
        brief="<url>",
        usage="<网页地址>",
        examples=("/渲染 网页 https://example.com", "/截图 https://example.com"),
        options=(),
        handler="commands.render:screenshot",
        allow_positional=True,
    ),
)


# ------------------ 命令 handlers ------------------


def _content(result: ParseResult, what: str = "内容") -> str:
    content = " ".join(result.positional).strip()
    if not content:
        raise CommandError(f"请把要渲染的{what}跟在命令后面。\n例：/渲染 {what} …")
    return content


async def text(bot: Bot, event: Event, result: ParseResult) -> None:
    content = _content(result)
    width = result.get("width") or 0
    try:
        img = await render_text_img(content, width=width or RENDER_WIDTHS["text"])
    except Exception as exc:  # noqa: BLE001
        logger.exception("[render] 文本渲染失败")
        raise CommandError(f"文本渲染失败：{exc}") from exc
    await send_image(bot, event, img, "渲染文本.png")


async def md(bot: Bot, event: Event, result: ParseResult) -> None:
    content = _content(result, "Markdown 内容")
    width = result.get("width") or 0
    try:
        img = await render_md_img(content, width=width or RENDER_WIDTHS["md"])
    except Exception as exc:  # noqa: BLE001
        logger.exception("[render] Markdown 渲染失败")
        raise CommandError(f"Markdown 渲染失败：{exc}") from exc
    await send_image(bot, event, img, "渲染Markdown.png")


async def html(bot: Bot, event: Event, result: ParseResult) -> None:
    content = _content(result, "HTML 内容")
    width = result.get("width") or 0
    try:
        img = await render_html_img(content, width=width or RENDER_WIDTHS["html"])
    except Exception as exc:  # noqa: BLE001
        logger.exception("[render] HTML 渲染失败")
        raise CommandError(f"HTML 渲染失败：{exc}") from exc
    await send_image(bot, event, img, "渲染HTML.png")


async def screenshot(bot: Bot, event: Event, result: ParseResult) -> None:
    url = " ".join(result.positional).strip()
    if not url:
        raise CommandError("请把要截图的网页地址跟在命令后面。\n例：/渲染 网页 https://example.com")
    try:
        img = await screenshot_url(url)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[render] 网页截图失败")
        raise CommandError(f"网页截图失败：{exc}") from exc
    await send_image(bot, event, img, "网页截图.png")
