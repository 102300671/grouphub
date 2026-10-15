"""backend 渲染模块 —— 网页端 AI 渲染工具核心。

纯 Playwright 渲染（backend 为 FastAPI 环境，不走 nonebot-plugin-htmlrender）：
文本 / Markdown / HTML / 网页截图 → PNG 字节。渲染出的图片由调用方
（app/api/ai.py 的渲染工具 handler）上传到站点存储后返回直链，AI 回复中
以 markdown 图片语法展示给网页端用户。

Markdown → HTML 用 python-markdown（fenced_code / tables / nl2br 扩展）。
chromium 复用 qqbot 部署时下载的 /root/.cache/ms-playwright。
"""
from __future__ import annotations

import asyncio
import html
from typing import Optional

import markdown
from playwright.async_api import async_playwright

# root 用户运行 chromium 必须 --no-sandbox；服务器无 GPU 用软件渲染
_LAUNCH_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]

# 渲染并发上限：playwright 每次独立 launch，限制并发避免服务器资源耗尽
_RENDER_SEMAPHORE = asyncio.Semaphore(2)

_BASE_CSS = """
body { margin: 0; padding: 20px 24px; background: #ffffff;
       font-family: -apple-system, "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
       color: #1f2d3d; line-height: 1.65; word-break: break-word; }
h1 { font-size: 22px; margin: 0 0 10px; color: #1f2d3d; }
h2 { font-size: 18px; margin: 14px 0 8px; }
h3 { font-size: 15px; margin: 12px 0 6px; }
p { margin: 8px 0; }
ul, ol { margin: 8px 0; padding-left: 24px; }
li { margin: 3px 0; }
code { background: #f0f2f5; padding: 2px 6px; border-radius: 4px; font-size: 13px; }
pre { background: #f6f8fa; border: 1px solid #e4e7ed; border-radius: 8px;
      padding: 12px 14px; overflow-x: auto; }
pre code { background: none; padding: 0; }
blockquote { border-left: 4px solid #d3dce6; margin: 8px 0; padding: 4px 12px; color: #5d6d7e; }
table { border-collapse: collapse; margin: 10px 0; }
th, td { border: 1px solid #d8dee6; padding: 6px 10px; text-align: left; }
th { background: #f0f2f5; }
a { color: #3370ff; }
hr { border: none; border-top: 1px solid #e4e7ed; margin: 12px 0; }
.text-body { white-space: pre-wrap; font-size: 15px; }
"""


def _wrap(inner: str, width: int) -> str:
    """包一层完整 HTML 文档，固定视口宽度供截图。"""
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<style>{_BASE_CSS}</style></head>"
        f"<body style='width:{width}px'>{inner}</body></html>"
    )


def text_to_html(text: str) -> str:
    """纯文本 → HTML（转义 + 保留换行）。"""
    return f"<div class='text-body'>{html.escape(text)}</div>"


def md_to_html(md_text: str) -> str:
    """Markdown → HTML（代码块/表格/换行）。"""
    return markdown.markdown(
        md_text or "",
        extensions=["fenced_code", "tables", "nl2br", "sane_lists"],
    )


async def _screenshot_page(html_doc: str, width: int) -> bytes:
    """在 Playwright 页面中渲染 HTML 文档并整页截图（PNG 字节）。"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=_LAUNCH_ARGS)
        try:
            page = await browser.new_page(
                viewport={"width": width, "height": 800},
                device_scale_factor=2.0,
            )
            await page.set_content(html_doc, wait_until="load")
            # 等字体/布局稳定
            await page.wait_for_timeout(200)
            return await page.screenshot(full_page=True, type="png")
        finally:
            await browser.close()


async def render_text(text: str, width: int = 680) -> bytes:
    """纯文本渲染为 PNG 字节。"""
    if not text.strip():
        raise ValueError("渲染内容为空")
    async with _RENDER_SEMAPHORE:
        return await _screenshot_page(_wrap(text_to_html(text), width), width)


async def render_markdown_img(md_text: str, width: int = 860) -> bytes:
    """Markdown 渲染为 PNG 字节。"""
    if not md_text.strip():
        raise ValueError("渲染内容为空")
    async with _RENDER_SEMAPHORE:
        return await _screenshot_page(_wrap(md_to_html(md_text), width), width)


async def render_html(html_content: str, width: int = 900) -> bytes:
    """HTML 片段渲染为 PNG 字节（自动补完整文档外壳）。"""
    if not html_content.strip():
        raise ValueError("渲染内容为空")
    async with _RENDER_SEMAPHORE:
        return await _screenshot_page(_wrap(html_content, width), width)


async def screenshot_url(url: str, width: int = 1280) -> bytes:
    """网页整页截图（PNG 字节）。"""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    async with _RENDER_SEMAPHORE:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=_LAUNCH_ARGS)
            try:
                page = await browser.new_page(
                    viewport={"width": width, "height": 900},
                    device_scale_factor=1.5,
                )
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:  # noqa: BLE001 网络空闲等待失败不阻断截图
                    pass
                return await page.screenshot(full_page=True, type="png")
            finally:
                await browser.close()
