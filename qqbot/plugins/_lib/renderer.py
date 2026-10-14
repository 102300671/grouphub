"""渲染公共库 —— grouphub 原生版。

将文本 / Markdown / HTML / 网页截图渲染为图片，并提供适配器无关的
图片发送（QQ 官方富媒体自动上传 / OneBot v11 base64），供命令层
（plugins/commands/render.py）与 AI 工具（render 包）共用。

渲染后端：nonebot-plugin-htmlrender（playwright provider，需
.env.prod 配置 RENDER__PROVIDER=playwright 且已 `playwright install chromium`）。
网页截图因 htmlrender 0.8.x 无直接截图 API，用 playwright 直连。

发图策略（对齐 _lib/bots.py 的适配器判定）：
- QQ 官方（主用）：MessageSegment.file_image(bytes, filename)，适配器自动富媒体上传；
- OneBot v11（备用）：base64 图片段；
- 其他通道：降级文本提示。
"""
from __future__ import annotations

import base64

from nonebot import logger
from nonebot.adapters import Bot, Event

# ---------------------------------------------------------------------------
# 兼容修补：nonebot-plugin-htmlrender 0.8.x 在后台线程（anyio worker）调用
# localstore.get_plugin_data_dir() 记录浏览器运行状态，线程栈检测不到调用方
# 插件会抛 "Cannot detect caller plugin"（服务器/PRoot 环境必现）。
# 这里把该调用兜底到 localstore 全局数据目录：正常插件上下文行为不变，
# 仅当检测失败时退回固定路径，保证渲染链路可用。
# ---------------------------------------------------------------------------
try:  # pragma: no cover - 环境差异
    import nonebot_plugin_localstore as _localstore

    if not getattr(_localstore, "_gh_render_patched", False):
        _orig_localstore_data_dir = _localstore.get_plugin_data_dir

        def _gh_safe_data_dir():
            try:
                return _orig_localstore_data_dir()
            except RuntimeError:
                return _localstore.BASE_DATA_DIR

        _localstore.get_plugin_data_dir = _gh_safe_data_dir  # type: ignore[assignment]
        _localstore._gh_render_patched = True
except Exception:  # noqa: BLE001 未装 localstore 时跳过，渲染函数会在导入时报错
    pass


# ------------------ 渲染：文本 / Markdown / HTML ------------------


async def render_text_img(text: str, width: int = 680) -> bytes:
    """纯文本渲染为 PNG 字节。"""
    from nonebot_plugin_htmlrender import render_text

    if not text.strip():
        raise ValueError("渲染内容为空")
    img = await render_text(text, width=width, device_pixel_ratio=2.0)
    return img.data


async def render_md_img(md: str, width: int = 860) -> bytes:
    """Markdown 渲染为 PNG 字节。"""
    from nonebot_plugin_htmlrender import render_markdown

    if not md.strip():
        raise ValueError("渲染内容为空")
    img = await render_markdown(md, width=width, device_pixel_ratio=2.0)
    return img.data


async def render_html_img(html: str, width: int = 900) -> bytes:
    """HTML 渲染为 PNG 字节。"""
    from nonebot_plugin_htmlrender import render_html

    if not html.strip():
        raise ValueError("渲染内容为空")
    img = await render_html(html, width=width, device_pixel_ratio=2.0)
    return img.data


# ------------------ 渲染：网页截图 ------------------


async def screenshot_url(url: str, width: int = 1280, full_page: bool = True) -> bytes:
    """网页整页截图（Playwright 直连）。

    - root 用户运行 chromium 必须 --no-sandbox；
    - 服务器无 GPU，headless 自动软件渲染，另加 --disable-gpu 保险。
    """
    from playwright.async_api import async_playwright

    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
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
            return await page.screenshot(full_page=full_page, type="png")
        finally:
            await browser.close()


# ------------------ 发图：适配器无关 ------------------


def _raw_bot(bot: Bot) -> Bot:
    """解包 AtSenderBot → 内部真实适配器 bot（类型判定用）。"""
    return getattr(bot, "raw_bot", bot)


async def send_image(
    bot: Bot, event: Event, img_bytes: bytes, filename: str = "render.png"
) -> str:
    """按当前适配器把图片发给消息来源，返回给调用方的结果描述。

    - QQ 官方：file_image(bytes, filename)，适配器自动走富媒体上传；
    - OneBot v11：base64 图片段；
    - 其他通道：降级文本提示（不抛错）。
    """
    module = type(_raw_bot(bot)).__module__
    if module.startswith("nonebot.adapters.qq"):
        from nonebot.adapters.qq import MessageSegment as QQSegment

        await bot.send(event, QQSegment.file_image(img_bytes, filename))
        return "图片已发送到当前会话"
    if module.startswith("nonebot.adapters.onebot"):
        from nonebot.adapters.onebot.v11 import MessageSegment as OneBotSegment

        b64 = base64.b64encode(img_bytes).decode()
        await bot.send(event, OneBotSegment.image(f"base64://{b64}"))
        return "图片已发送到当前会话"
    logger.warning(f"[render] 未知适配器 {module}，无法直接发图")
    await bot.send(event, f"（已渲染图片 {filename}，共 {len(img_bytes)} 字节，当前通道不支持直接发送）")
    return "已生成图片，但当前通道不支持直接发送图片"
