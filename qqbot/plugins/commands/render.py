"""命令实现：/渲染 —— 文本 / Markdown / HTML 渲染成图片，网页截图。

命令（内容即位置参数；内容以 - 开头时用 -- 分隔，如 `/渲染 md -- - 列表项`）：
  /渲染 <内容>          文本转图片（默认）
  /渲染 md <内容>       Markdown 转图片
  /渲染 html <内容>     HTML 转图片
  /渲染 网页 <url>      网页整页截图

AI 工具（web 包内）：web:text / web:md / web:html / web:screenshot，与 web:search 同一工具包。
由 /ai 会话按需调用：渲染成功后图片直接发到当前会话（经 aitools 注入的
图片发送器），模型收到文本回执后简短收尾即可，无需重复图片全文。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from nonebot import logger
from nonebot.adapters import Bot, Event

from .._lib import aitools
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
        img = await render_text_img(content, width=width or 680)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[render] 文本渲染失败")
        raise CommandError(f"文本渲染失败：{exc}") from exc
    await send_image(bot, event, img, "渲染文本.png")


async def md(bot: Bot, event: Event, result: ParseResult) -> None:
    content = _content(result, "Markdown 内容")
    width = result.get("width") or 0
    try:
        img = await render_md_img(content, width=width or 860)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[render] Markdown 渲染失败")
        raise CommandError(f"Markdown 渲染失败：{exc}") from exc
    await send_image(bot, event, img, "渲染Markdown.png")


async def html(bot: Bot, event: Event, result: ParseResult) -> None:
    content = _content(result, "HTML 内容")
    width = result.get("width") or 0
    try:
        img = await render_html_img(content, width=width or 900)
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


# ------------------ AI 工具（并入 web 包） ------------------


async def _ai_render(kind: str, args: Dict[str, str]) -> str:
    """render:text / md / html 公共实现：渲染 → 发图 → 返回文本回执。"""
    content = (args.get("content") or args.get("text") or "").strip()
    sender = aitools.get_current_image_sender()
    if sender is None:
        return "错误：当前会话不支持发送图片，请直接用文本回复用户。"

    width = 0
    raw_w = (args.get("width") or "").strip()
    if raw_w:
        try:
            width = int(raw_w)
        except ValueError:
            width = 0

    if not content:
        return "错误：缺少必填参数 content（要渲染的内容）。"

    try:
        if kind == "text":
            img = await render_text_img(content, width=width or 680)
            filename = "渲染文本.png"
        elif kind == "md":
            img = await render_md_img(content, width=width or 860)
            filename = "渲染Markdown.png"
        else:  # html
            img = await render_html_img(content, width=width or 900)
            filename = "渲染HTML.png"
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"[ai render:{kind}] 渲染失败")
        return f"错误：渲染失败：{exc}。请如实告知用户，或改用文本回复。"

    try:
        note = await sender(img, filename)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[ai render] 图片发送失败")
        return f"错误：渲染成功但图片发送失败：{exc}。请如实告知用户，或改用文本回复。"

    return f"已把内容渲染为图片并{note}。用户可直接看到图片，你只需用一两句话收尾，不要重复图片里的全部内容。"


async def _ai_screenshot(args: Dict[str, str]) -> str:
    """render:screenshot 工具：网页整页截图 → 发图 → 返回文本回执。"""
    url = (args.get("url") or "").strip()
    sender = aitools.get_current_image_sender()
    if sender is None:
        return "错误：当前会话不支持发送图片，请直接用文本回复用户。"
    if not url:
        return "错误：缺少必填参数 url（要截图的网页地址）。"

    try:
        img = await screenshot_url(url)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[ai render:screenshot] 截图失败")
        return f"错误：网页截图失败：{exc}。请如实告知用户，或改用文本回复。"

    try:
        note = await sender(img, "网页截图.png")
    except Exception as exc:  # noqa: BLE001
        logger.exception("[ai render] 截图发送失败")
        return f"错误：截图成功但发送失败：{exc}。请如实告知用户。"

    return f"已截取网页并{note}。用户可直接看到图片，你只需简短说明页面内容要点。"



aitools.register_tool(
    aitools.Tool(
        namespace="web",
        name="text",
        description="把文本内容渲染成图片发送到当前会话。适合长文本、公告、适合整页呈现的内容。",
        handler=lambda args: _ai_render("text", args),
        params={
            "content": "要渲染的文本（必填）",
            "width": "图片宽度像素（可选，默认 680）",
        },
    )
)
aitools.register_tool(
    aitools.Tool(
        namespace="web",
        name="md",
        description="把 Markdown 内容渲染成图片发送到当前会话。表格、代码块、列表、标题排版适合用它。",
        handler=lambda args: _ai_render("md", args),
        params={
            "content": "要渲染的 Markdown 文本（必填）",
            "width": "图片宽度像素（可选，默认 860）",
        },
    )
)
aitools.register_tool(
    aitools.Tool(
        namespace="web",
        name="html",
        description="把 HTML 片段渲染成图片发送到当前会话。需要自定义样式/卡片布局时用。",
        handler=lambda args: _ai_render("html", args),
        params={
            "content": "要渲染的 HTML 片段（必填，需含完整 <style> 或内联样式）",
            "width": "图片宽度像素（可选，默认 900）",
        },
    )
)
aitools.register_tool(
    aitools.Tool(
        namespace="web",
        name="screenshot",
        description="截取指定网页的整页截图并发送到当前会话。用户要看某个网址页面内容/外观时用。",
        handler=_ai_screenshot,
        params={"url": "要截图的网页地址（必填，可省略 https:// 前缀）"},
    )
)
