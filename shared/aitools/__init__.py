"""AI 工具层：文本协议工具包 + agent 循环 + 图片尺寸探测。

对外唯一入口：build_agent()。

    from shared.aitools import build_agent

    agent = build_agent(search=..., renderer=..., site=..., site_base_url="...")
    answer = await agent.run_turn(chat, messages, ctx=ctx)

约束（.ai/CONTRACT.md）：
- 本包**不得** import fastapi / nonebot；宿主能力一律走 ports.py；
- 同一份实现只写一次，QQ 端与 Web 端共用，禁止各自复制。
"""
from __future__ import annotations

from .agent import Agent, build_agent
from .errors import ERROR_PREFIX, is_error, tool_error
from .imagesize import (
    ImageSize,
    format_markdown_image,
    parse_image_size,
    probe_image_size,
)
from .ports import (
    ChatBackend,
    ChatFn,
    ImageSink,
    Renderer,
    SearchBackend,
    SiteDAO,
)
from .registry import (
    Context,
    ImageArtifact,
    Package,
    Param,
    Registry,
    SearchItem,
    SearchOutcome,
    SubmitResult,
    Tool,
    ToolCall,
)

__all__ = [
    "Agent",
    "build_agent",
    # 注册表与数据结构
    "Registry",
    "Tool",
    "ToolCall",
    "Package",
    "Param",
    "Context",
    "ImageArtifact",
    "SearchItem",
    "SearchOutcome",
    "SubmitResult",
    # 端口
    "ChatBackend",
    "ChatFn",
    "ImageSink",
    "Renderer",
    "SearchBackend",
    "SiteDAO",
    # 错误规范
    "ERROR_PREFIX",
    "is_error",
    "tool_error",
    # 图片尺寸探测
    "ImageSize",
    "probe_image_size",
    "parse_image_size",
    "format_markdown_image",
]
