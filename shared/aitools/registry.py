"""注册表与核心数据结构。

共享包不持有全局状态：注册表是**实例**（Registry），由 build_agent() 建 Agent 时创建。
QQ 端是进程级单例（bot 启动时建一次），Web 端可按请求建（每请求工具表可能不同）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional

if TYPE_CHECKING:  # pragma: no cover - 仅类型引用，避免运行时循环导入
    from .ports import ImageSink


@dataclass(frozen=True)
class Param:
    """一个工具参数的说明。

    参数说明文本由结构化字段**生成**，不手写。渲染结果形如::

        搜索关键词（必填，精炼，不要包含'帮我搜一下'等客套话）
        图片宽度像素（可选，默认 680）

    默认值只写在 default 一处：工具手册里的「默认 N」与工具执行时的兜底值同源，
    从根上避免「手册说 680、代码用 420」这类漂移（.ai/RISKS.md 漂移 1）。
    """

    desc: str
    required: bool = True
    default: Optional[int] = None
    note: str = ""       # 括号内的补充说明，如「最大 20」
    suffix: str = ""     # 括号后的追加说明，如「：novel/anime/...」

    def render(self) -> str:
        inner = "必填" if self.required else "可选"
        if self.default is not None:
            inner += f"，默认 {self.default}"
        if self.note:
            inner += f"，{self.note}"
        return f"{self.desc}（{inner}）{self.suffix}"


# 工具处理函数：async (args, ctx) -> str
# 返回给模型看的文本；失败也返回文本（以「错误：」开头），不抛异常。
ToolHandler = Callable[[Dict[str, str], "Context"], Awaitable[str]]


@dataclass(frozen=True)
class Tool:
    """一个可被模型调用的工具。"""

    namespace: str
    name: str
    description: str
    handler: ToolHandler
    params: Dict[str, Param] = field(default_factory=dict)

    @property
    def fullname(self) -> str:
        return f"{self.namespace}:{self.name}"

    @property
    def defaults(self) -> Dict[str, int]:
        """参数默认值（取自 Param.default），执行兜底与手册文案同源。"""
        return {k: v.default for k, v in self.params.items() if v.default is not None}

    def param_text(self) -> Dict[str, str]:
        """参数说明文本（参数名 → 渲染后的说明）。"""
        return {k: v.render() for k, v in self.params.items()}


@dataclass(frozen=True)
class ToolCall:
    """模型输出的一次工具调用。"""

    name: str
    args: Dict[str, str]
    raw: str                       # 原始块文本（便于日志）


@dataclass(frozen=True)
class Package:
    """一个工具包：含名称、简介和若干工具。

    工具按包组织：system prompt 只给包目录（名称+简介），AI 需要时调用
    pkg:activate 激活某包，系统返回该包内的工具详情，AI 再选择工具调用。
    避免工具增多后把所有工具信息一股脑灌给模型。
    """

    name: str
    description: str


@dataclass(frozen=True)
class ImageArtifact:
    """待发送的图片产物。

    bytes_ 必填；rel_url / abs_url 在需要把图片落盘并给出可访问链接时才填
    （M4 的 Web 端图片存储用，QQ 端直接发字节）。
    """

    bytes_: bytes
    filename: str = "image.png"
    rel_url: Optional[str] = None
    abs_url: Optional[str] = None


@dataclass
class Context:
    """一次请求的上下文（随工具调用传递，取代旧的 contextvar）。

    owner_id      Web 端登录用户 id（QQ 端为 None）
    user_qq       QQ 端调用者 QQ（Web 端为 None）
    conversation_id 会话 id（两端都有，QQ 端为后端群会话 id）
    channel       通道标识，如 "qq" / "web"
    sink          图片发送出口；None 表示当前会话不支持发图
    """

    owner_id: Optional[int] = None
    user_qq: Optional[str] = None
    conversation_id: Optional[int] = None
    channel: str = "unknown"
    sink: Optional["ImageSink"] = None


@dataclass(frozen=True)
class SearchItem:
    """一条搜索结果。"""

    title: str
    url: str
    content: str


@dataclass(frozen=True)
class SearchOutcome:
    """搜索结果 + 无响应引擎说明。

    dead_engines 用于区分「关键词没结果」与「搜索引擎整体挂了」，
    两者给模型的措辞不同（见 tools/web.py）。
    """

    items: List[SearchItem] = field(default_factory=list)
    dead_engines: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class SubmitResult:
    """作品提交结果。

    message 为 None 表示后端没给原因（展示时回退成「未知原因」）；
    空串是后端明确给了空消息，两者语义不同。
    """

    ok: bool
    duplicate: bool = False
    work: Dict[str, Any] = field(default_factory=dict)
    message: Optional[str] = None


class Registry:
    """工具与工具包的注册表（实例级，无全局状态）。"""

    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}
        self._packages: Dict[str, Package] = {}

    # ------------------ 工具 ------------------

    def register_tool(self, tool: Tool) -> Tool:
        """注册工具；同名覆盖（便于测试替换）。返回工具本身，可作装饰器。"""
        self._tools[tool.fullname] = tool
        return tool

    def get_tool(self, fullname: str) -> Optional[Tool]:
        return self._tools.get(fullname)

    def tools(self) -> List[Tool]:
        """按注册顺序返回全部工具。"""
        return list(self._tools.values())

    def tool_names(self) -> List[str]:
        return list(self._tools)

    def tools_of(self, namespace: str) -> List[Tool]:
        """某命名空间下的工具（保持注册顺序）。"""
        return [t for t in self._tools.values() if t.namespace == namespace]

    # ------------------ 工具包 ------------------

    def register_package(self, pkg: Package) -> Package:
        self._packages[pkg.name] = pkg
        return pkg

    def get_package(self, name: str) -> Optional[Package]:
        return self._packages.get(name)

    def packages(self) -> List[Package]:
        """按注册顺序返回全部工具包。"""
        return list(self._packages.values())

    def clear(self) -> None:
        """清空注册表（仅供测试）。"""
        self._tools.clear()
        self._packages.clear()
