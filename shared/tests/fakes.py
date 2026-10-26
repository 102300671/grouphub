"""单测用的假端口。

行为由 cfg 字典控制（每个用例一份），让「旧实现」与「新实现」跑在完全相同的输入上，
逐字比对输出（见 fixtures/tools_golden.json 的生成流程）。
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from shared.aitools.registry import (  # noqa: E402
    Context,
    ImageArtifact,
    SearchItem,
    SearchOutcome,
    SubmitResult,
)

SITE_BASE_URL = "http://127.0.0.1:5173"

# 站内作品（固定样例，两端一致）
SITE_ITEMS: List[Dict[str, Any]] = [
    {
        "id": 7,
        "title": "作品甲",
        "author": "作者甲",
        "type": "novel",
        "summary": "简介甲",
        "score": 12,
    },
    {
        "id": 8,
        "title": "作品乙",
        "author": "",
        "type": "anime",
        "summary": "",
        "score": 3,
    },
]

SEARCH_ITEMS = [
    SearchItem(title="标题一", url="https://example.com/1", content="内容一"),
    SearchItem(title="标题二", url="https://example.com/2", content="内容二"),
]

# 上传成功后的作品（pending 状态）
SUBMIT_WORK_PENDING = {
    "id": 21,
    "title": "新作品",
    "author": "作者丙",
    "type": "novel",
    "summary": "简介丙",
    "status": "pending",
}
# 上传成功后的作品（已发布）
SUBMIT_WORK_PUBLISHED = {
    "id": 22,
    "title": "新作品",
    "author": "",
    "type": "movie",
    "summary": "",
    "status": "published",
}


class FakeSearch:
    """假搜索端口。"""

    def __init__(self, cfg: Dict[str, Any]) -> None:
        self.cfg = cfg

    async def search(self, query: str) -> SearchOutcome:
        mode = self.cfg.get("search", "ok")
        if mode == "raise":
            raise RuntimeError("无法连接 SearXNG（http://searxng/search）：boom")
        dead = list(self.cfg.get("dead_engines", []))
        if mode == "empty":
            return SearchOutcome(items=[], dead_engines=dead)
        return SearchOutcome(items=list(SEARCH_ITEMS), dead_engines=dead)


class FakeRenderer:
    """假渲染端口：返回固定字节，并记录收到的宽度（用于校验默认值）。"""

    def __init__(self, cfg: Dict[str, Any]) -> None:
        self.cfg = cfg
        self.calls: List[Dict[str, Any]] = []

    def _check(self, kind: str, content: str, width: int) -> bytes:
        if self.cfg.get("render") == "raise":
            raise RuntimeError("渲染器炸了")
        self.calls.append({"kind": kind, "content": content, "width": width})
        return b"\x89PNG\r\n\x1a\nFAKE"

    async def render_text(self, content: str, *, width: int) -> bytes:
        return self._check("text", content, width)

    async def render_markdown(self, content: str, *, width: int) -> bytes:
        return self._check("md", content, width)

    async def render_html(self, content: str, *, width: int) -> bytes:
        return self._check("html", content, width)

    async def screenshot(self, url: str) -> bytes:
        if self.cfg.get("screenshot") == "raise":
            raise RuntimeError("截图炸了")
        self.calls.append({"kind": "screenshot", "url": url})
        return b"\x89PNG\r\n\x1a\nFAKE"


class FakeSink:
    """假图片发送出口。"""

    def __init__(self, cfg: Dict[str, Any]) -> None:
        self.cfg = cfg
        self.sent: List[ImageArtifact] = []

    async def send(self, artifact: ImageArtifact) -> str:
        if self.cfg.get("send") == "raise":
            raise RuntimeError("发图炸了")
        self.sent.append(artifact)
        return self.cfg.get("send_note", "图片已发送到当前会话")


class FakeSite:
    """假站内作品库端口。"""

    def __init__(self, cfg: Dict[str, Any]) -> None:
        self.cfg = cfg
        self.calls: List[Dict[str, Any]] = []

    async def hot(self, limit: int) -> List[Dict[str, Any]]:
        self.calls.append({"op": "hot", "limit": limit})
        if self.cfg.get("site") == "raise":
            raise RuntimeError("后端 500")
        if self.cfg.get("site") == "empty":
            return []
        return [dict(x) for x in SITE_ITEMS]

    async def search(
        self, keyword: str, limit: int, type: Optional[str]
    ) -> List[Dict[str, Any]]:
        self.calls.append({"op": "search", "keyword": keyword, "limit": limit, "type": type})
        if self.cfg.get("site") == "raise":
            raise RuntimeError("后端 500")
        if self.cfg.get("site") == "empty":
            return []
        return [dict(x) for x in SITE_ITEMS]

    async def submit(self, payload: Dict[str, Any], ctx: Context) -> SubmitResult:
        self.calls.append({"op": "submit", "payload": payload, "ctx": ctx})
        mode = self.cfg.get("submit", "ok")
        if mode == "raise":
            raise RuntimeError("提交炸了")
        if mode == "duplicate":
            return SubmitResult(
                ok=False,
                duplicate=True,
                work={"id": 99, "title": "已有作品"},
                message="已存在同名作品（ID=99），请群友到站点标记支持",
            )
        if mode == "fail":
            return SubmitResult(ok=False, message="上传者 QQ 未在站点注册")
        if mode == "fail_nomessage":
            return SubmitResult(ok=False, message=None)
        if mode == "published":
            return SubmitResult(ok=True, work=dict(SUBMIT_WORK_PUBLISHED))
        return SubmitResult(ok=True, work=dict(SUBMIT_WORK_PENDING))
