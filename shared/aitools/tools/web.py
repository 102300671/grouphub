"""web 包：联网搜索 + 内容渲染（文本 / Markdown / HTML / 网页截图）。

两端共用同一份实现，差异只在注入的端口：
- QQ 端：搜索走本地 SearXNG，渲染走 nonebot-plugin-htmlrender，发图走 QQ 适配器；
- Web 端：搜索走同一套 SearXNG 协议（地址从用户配置取），渲染走自写 playwright。

工具说明与参数文案是**发给模型的 prompt**，逐字影响模型行为，两端禁止各写一份。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Dict, List, Optional

if TYPE_CHECKING:  # pragma: no cover - 仅类型引用
    import httpx

from ..logs import log_exception, logger
from ..ports import Renderer, SearchBackend
from ..registry import (
    Context,
    ImageArtifact,
    Package,
    Param,
    Registry,
    SearchItem,
    SearchOutcome,
    Tool,
    ToolHandler,
)

# ------------------ 工具包 ------------------

WEB_PACKAGE = Package(
    name="web",
    description=(
        "联网搜索 + 内容渲染：通过本地 SearXNG 检索网页获取最新资讯/版本信息/攻略资料；"
        "也可把文本/Markdown/HTML 渲染成图片、对网页整页截图并发到当前会话。用户要求"
        "「渲染成图片/做成图片/截图/图片展示」或回答适合用图片呈现时，也激活本包。"
    ),
)

# 渲染宽度（像素）：文本 / Markdown / HTML 各自的默认宽度。
# 唯一真源：AI 工具手册里的「默认 N」与 QQ 端 /渲染 命令都用它，避免两处各写一遍。
RENDER_WIDTHS = {
    "text": 680,
    "md": 860,
    "html": 900,
}

# ------------------ 参数说明（默认值只写这一处） ------------------

_PARAM_QUERY = Param(
    "搜索关键词", required=True, note="精炼，不要包含'帮我搜一下'等客套话"
)
_PARAM_CONTENT_TEXT = Param("要渲染的文本", required=True)
_PARAM_CONTENT_MD = Param("要渲染的 Markdown 文本", required=True)
_PARAM_CONTENT_HTML = Param(
    "要渲染的 HTML 片段", required=True, note="需含完整 <style> 或内联样式"
)
_PARAM_URL = Param("要截图的网页地址", required=True, note="可省略 https:// 前缀")
_PARAM_WIDTH_TEXT = Param("图片宽度像素", required=False, default=RENDER_WIDTHS["text"])
_PARAM_WIDTH_MD = Param("图片宽度像素", required=False, default=RENDER_WIDTHS["md"])
_PARAM_WIDTH_HTML = Param("图片宽度像素", required=False, default=RENDER_WIDTHS["html"])


# ------------------ 搜索 ------------------

def build_reference(items: List[SearchItem]) -> str:
    """把搜索结果拼成给模型的参考资料块（带 [序号] 供引用）。"""
    blocks = []
    for i, r in enumerate(items, 1):
        blocks.append(
            f"[{i}] {r.title}\n    {r.url}\n    {r.content}"
        )
    return (
        "以下是联网搜索到的参考资料（请优先依据这些资料回答用户问题；"
        "资料没有覆盖的内容按你自己的知识补充，并在回答末尾按 [序号] 列出参考来源）：\n"
        + "\n".join(blocks)
    )


def _make_search_tool(search: SearchBackend) -> ToolHandler:
    async def handler(args: Dict[str, str], ctx: Context) -> str:
        query = (args.get("query") or "").strip()
        if not query:
            return "错误：缺少必填参数 query（搜索关键词）。"
        outcome = await search.search(query)
        items = outcome.items
        dead_engines = outcome.dead_engines
        if not items:
            logger.warning(
                f"[aitools] web:search 零结果，query={query!r}，无响应引擎：{dead_engines or '（无报告）'}"
            )
            engine_note = ""
            if dead_engines:
                engine_note = (
                    "\n注意：本次所有搜索引擎均无响应，故障来自搜索服务侧而非关键词，"
                    f"详情：{'、'.join(dead_engines[:6])}。不要反复换词重试超过一次，"
                    "请如实告知用户搜索引擎暂时不可用，并给出可自行查证的渠道。"
                )
            return (
                "未搜索到任何结果。可以换一个更精炼或英文的关键词重试一次；"
                "若仍无结果就直接回答用户。" + engine_note
            )
        if dead_engines:
            logger.info(f"[aitools] web:search 命中 {len(items)} 条，部分引擎无响应：{dead_engines}")
        return build_reference(items)

    return handler


class SearXNGSearch:
    """SearXNG JSON API 搜索（两端共用实现）。

    两端唯一的差别是「地址从哪来」：QQ 端读 /ai 配置，Web 端读用户配置，
    因此这里接收一个取地址的回调。协议本身（SearXNG 的 JSON 接口）只写一次。

    失败一律抛 RuntimeError（中文可读），由调用方决定怎么转达。
    """

    def __init__(
        self,
        base_url: Callable[[], str],
        *,
        results: int = 5,
        missing_hint: str = "AI_SEARXNG_URL",
        timeout: float = 120.0,
        connect_timeout: float = 10.0,
        client: Optional["httpx.AsyncClient"] = None,
    ) -> None:
        import httpx  # 延迟导入：没装 httpx 时本模块仍可导入（仅本类不可用）

        self._base_url = base_url
        self._results = results
        self._missing_hint = missing_hint
        self._timeout = timeout
        self._connect_timeout = connect_timeout
        self._client = client

    def _http(self) -> "httpx.AsyncClient":
        import httpx  # 延迟导入，同上

        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout, connect=self._connect_timeout)
            )
        return self._client

    async def search(self, query: str, *, limit: Optional[int] = None) -> SearchOutcome:
        """检索并返回 (结果, 无响应引擎说明)。

        limit: 最多返回多少条（None 或 <=0 时用构造时的默认条数）。
        要求 SearXNG 的 settings.yml 中 search.formats 包含 json。
        """
        base = (self._base_url() or "").rstrip("/")
        if not base:
            raise RuntimeError(f"未配置 SearXNG 地址（{self._missing_hint}）")
        url = base if base.endswith("/search") else f"{base}/search"
        try:
            resp = await self._http().get(
                url,
                params={"q": query, "format": "json", "language": "zh-CN"},
                headers={"Accept": "application/json"},
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise RuntimeError(f"无法连接 SearXNG（{url}）：{exc}") from exc
        if resp.status_code != 200:
            raise RuntimeError(f"SearXNG 返回 {resp.status_code}：{resp.text[:150]}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise RuntimeError("SearXNG 未返回 JSON（请确认 search.formats 已启用 json）") from exc
        cap = limit if (limit and limit > 0) else self._results
        items: List[SearchItem] = []
        for item in (data.get("results") or [])[:cap]:
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            items.append(
                SearchItem(
                    title=title,
                    url=str(item.get("url") or "").strip(),
                    content=str(item.get("content") or "").strip(),
                )
            )
        dead: List[str] = []
        for engine, reason in (data.get("unresponsive_engines") or []):
            dead.append(f"{engine}（{reason}）")
        return SearchOutcome(items=items, dead_engines=dead)


# ------------------ 渲染 ------------------

def _make_render_tool(kind: str, renderer: Renderer) -> ToolHandler:
    """render:text / md / html 公共实现：渲染 → 发图 → 返回文本回执。"""

    async def handler(args: Dict[str, str], ctx: Context) -> str:
        content = (args.get("content") or args.get("text") or "").strip()
        if ctx.sink is None:
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
                img = await renderer.render_text(content, width=width or _PARAM_WIDTH_TEXT.default)
                filename = "渲染文本.png"
            elif kind == "md":
                img = await renderer.render_markdown(content, width=width or _PARAM_WIDTH_MD.default)
                filename = "渲染Markdown.png"
            else:  # html
                img = await renderer.render_html(content, width=width or _PARAM_WIDTH_HTML.default)
                filename = "渲染HTML.png"
        except Exception as exc:  # noqa: BLE001
            log_exception(f"[aitools render:{kind}] 渲染失败", exc)
            return f"错误：渲染失败：{exc}。请如实告知用户，或改用文本回复。"

        try:
            note = await ctx.sink.send(ImageArtifact(bytes_=img, filename=filename))
        except Exception as exc:  # noqa: BLE001
            log_exception("[aitools render] 图片发送失败", exc)
            return f"错误：渲染成功但图片发送失败：{exc}。请如实告知用户，或改用文本回复。"

        return f"已把内容渲染为图片并{note}。用户可直接看到图片，你只需用一两句话收尾，不要重复图片里的全部内容。"

    return handler


def _make_screenshot_tool(renderer: Renderer) -> ToolHandler:
    """web:screenshot 工具：网页整页截图 → 发图 → 返回文本回执。"""

    async def handler(args: Dict[str, str], ctx: Context) -> str:
        url = (args.get("url") or "").strip()
        if ctx.sink is None:
            return "错误：当前会话不支持发送图片，请直接用文本回复用户。"
        if not url:
            return "错误：缺少必填参数 url（要截图的网页地址）。"

        try:
            img = await renderer.screenshot(url)
        except Exception as exc:  # noqa: BLE001
            log_exception("[aitools render:screenshot] 截图失败", exc)
            return f"错误：网页截图失败：{exc}。请如实告知用户，或改用文本回复。"

        try:
            note = await ctx.sink.send(ImageArtifact(bytes_=img, filename="网页截图.png"))
        except Exception as exc:  # noqa: BLE001
            log_exception("[aitools render:screenshot] 截图发送失败", exc)
            return f"错误：截图成功但发送失败：{exc}。请如实告知用户。"

        return f"已截取网页并{note}。用户可直接看到图片，你只需简短说明页面内容要点。"

    return handler


# ------------------ 注册 ------------------

def register_web(registry: Registry, *, search: SearchBackend, renderer: Renderer) -> None:
    """把 web 包与 5 个工具注册进注册表。

    工具顺序 = 手册里的顺序，改动顺序等于改 prompt，需谨慎。
    """
    registry.register_package(WEB_PACKAGE)
    registry.register_tool(
        Tool(
            namespace="web",
            name="search",
            description="联网搜索网页，用于获取最新资讯、近期版本/数值、攻略资料或你不确定的事实。",
            handler=_make_search_tool(search),
            params={"query": _PARAM_QUERY},
        )
    )
    registry.register_tool(
        Tool(
            namespace="web",
            name="text",
            description="把文本内容渲染成图片发送到当前会话。适合长文本、公告、适合整页呈现的内容。",
            handler=_make_render_tool("text", renderer),
            params={"content": _PARAM_CONTENT_TEXT, "width": _PARAM_WIDTH_TEXT},
        )
    )
    registry.register_tool(
        Tool(
            namespace="web",
            name="md",
            description="把 Markdown 内容渲染成图片发送到当前会话。表格、代码块、列表、标题排版适合用它。",
            handler=_make_render_tool("md", renderer),
            params={"content": _PARAM_CONTENT_MD, "width": _PARAM_WIDTH_MD},
        )
    )
    registry.register_tool(
        Tool(
            namespace="web",
            name="html",
            description="把 HTML 片段渲染成图片发送到当前会话。需要自定义样式/卡片布局时用。",
            handler=_make_render_tool("html", renderer),
            params={"content": _PARAM_CONTENT_HTML, "width": _PARAM_WIDTH_HTML},
        )
    )
    registry.register_tool(
        Tool(
            namespace="web",
            name="screenshot",
            description="截取指定网页的整页截图并发送到当前会话。用户要看某个网址页面内容/外观时用。",
            handler=_make_screenshot_tool(renderer),
            params={"url": _PARAM_URL},
        )
    )
