"""Agent：把注册表与一轮问答的流程绑在一起。

build_agent() 是共享包对外的唯一入口（见 .ai/CONTRACT.md）：
两端各自实现 ports 里的协议，注入进来，拿到一个 Agent。
"""
from __future__ import annotations

from typing import List, Optional

from .loop import EventFn, run_turn
from .ports import ChatFn, Renderer, SearchBackend, SiteDAO
from .prompt import date_hint, package_catalog, package_tools_manual
from .registry import Context, Registry
from .tools.site import register_site
from .tools.web import register_web


class Agent:
    """一个装配好的 AI 助手（注册表 + 提示词 + 一轮问答流程）。"""

    def __init__(self, registry: Registry) -> None:
        self._registry = registry

    @property
    def registry(self) -> Registry:
        return self._registry

    # ------------------ 提示词片段 ------------------

    def date_hint(self) -> str:
        return date_hint()

    def package_catalog(self) -> str:
        return package_catalog(self._registry)

    def package_tools_manual(self, pkg_name: str) -> str:
        return package_tools_manual(self._registry, pkg_name)

    # ------------------ 一轮问答 ------------------

    async def run_turn(
        self,
        chat: ChatFn,
        messages: List[dict],
        *,
        ctx: Context,
        on_event: Optional[EventFn] = None,
        max_tool_steps: int = 2,
    ) -> str:
        return await run_turn(
            self._registry,
            chat,
            messages,
            ctx=ctx,
            on_event=on_event,
            max_tool_steps=max_tool_steps,
        )


def build_agent(
    *,
    search: SearchBackend,
    renderer: Renderer,
    site: SiteDAO,
    site_base_url: str = "",
) -> Agent:
    """装配一个 Agent：注册 web / site 两个工具包。

    - search / renderer / site: 宿主实现的能力端口；
    - site_base_url: 作品链接前缀（QQ 端给站点绝对地址，Web 端留空即相对 /works/{id}）。
    """
    registry = Registry()
    register_web(registry, search=search, renderer=renderer)
    register_site(registry, site=site, site_base_url=site_base_url)
    return Agent(registry)
