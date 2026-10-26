"""宿主能力接口（ports）。

共享包不认识 fastapi / nonebot / 任何具体数据库、渲染器、消息适配器；
两端各自实现下面的协议，把能力注入进来（见 aitools.build_agent）。

约定：
- 端口方法**不抛业务异常**给上层以外的调用方，工具内部自行兜底成「错误：…」文本；
- per-request 能力（图片发送出口、调用者身份）走 Context，不做进程级注入。
"""
from __future__ import annotations

from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
    runtime_checkable,
)

from .registry import Context, ImageArtifact, SearchOutcome, SubmitResult

# 思维链分片回调：async (text) -> None
ReasoningFn = Callable[[str], Awaitable[None]]

# 模型调用入口：async (messages, *, on_reasoning=None) -> str
ChatFn = Callable[..., Awaitable[str]]


@runtime_checkable
class ChatBackend(Protocol):
    """模型调用后端（QQ 端与 Web 端各自实现）。"""

    async def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        on_reasoning: Optional[ReasoningFn] = None,
    ) -> str:
        """发一轮对话，返回模型回复文本。

        on_reasoning 收到 reasoning_content 分片（QQ 不展示，只收集落库）。
        实现方**必须**接受该关键字参数——agent 循环固定会传它。
        """
        ...


@runtime_checkable
class ImageSink(Protocol):
    """图片发送出口（per-request，放 Context.sink）。

    返回一段给模型看的中文回执（如「图片已发送到当前会话」）。
    """

    async def send(self, artifact: ImageArtifact) -> str:
        ...


@runtime_checkable
class SearchBackend(Protocol):
    """联网搜索后端。"""

    async def search(self, query: str) -> SearchOutcome:
        ...


@runtime_checkable
class Renderer(Protocol):
    """渲染后端：把文本/Markdown/HTML/网页转成图片字节。"""

    async def render_text(self, content: str, *, width: int) -> bytes:
        ...

    async def render_markdown(self, content: str, *, width: int) -> bytes:
        ...

    async def render_html(self, content: str, *, width: int) -> bytes:
        ...

    async def screenshot(self, url: str) -> bytes:
        ...


@runtime_checkable
class SiteDAO(Protocol):
    """站内作品库读写。"""

    async def hot(self, limit: int) -> List[Dict[str, Any]]:
        """热门作品榜；元素含 id/title/author/type/summary/score。"""
        ...

    async def search(
        self, keyword: str, limit: int, type: Optional[str]
    ) -> List[Dict[str, Any]]:
        """按关键词搜索；type 为 None 表示不过滤。"""
        ...

    async def submit(self, payload: Dict[str, Any], ctx: Context) -> SubmitResult:
        """提交作品。

        提交者身份由 ctx 决定（QQ 端用 ctx.user_qq，Web 端用 ctx.owner_id），
        端口实现自己映射到后端接口的字段。
        """
        ...
