"""AI 文本协议工具包（不依赖模型原生 function calling）。

灵感来自 OperIt：用一段固定文本协议让任意模型（含不支持 tools 接口的本地小模型）
驱动外部工具。模型在回复中输出工具调用块，bot 解析执行后把结果回灌，循环直到
模型给出不含工具块的最终回答。

协议（提示词中会完整教给模型）::

    <tool_call>
    命名空间:工具名
    参数名=参数值
    </tool_call>

    <tool_result name="命名空间:工具名">...</tool_result>

扩展新工具：用 @register_tool 装饰一个 async 函数，或直接 register_tool(Tool(...))，
工具手册（tool_manual）会自动带上新工具的说明，无需改动 agent 循环。

日志软依赖 nonebot 的 loguru（运行时可见），无 nonebot 时退回标准库 logging（可独立单测）。
"""
from __future__ import annotations

import contextvars
import json
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional

# nonebot 用 loguru 输出，标准库 logging 的记录不会出现在 `nb run` 终端；
# 故优先用 nonebot 的 logger，独立单测（无 nonebot）时退回标准库。
try:  # pragma: no cover - 取决于运行环境
    from nonebot import logger  # type: ignore
    _USING_LOGURU = True
except ImportError:  # pragma: no cover
    import logging
    logger = logging.getLogger("plugins._lib.aitools")
    _USING_LOGURU = False


def _log_exception(message: str, exc: BaseException) -> None:
    """记录异常堆栈（loguru 与标准库 API 不同，统一封装）。"""
    if _USING_LOGURU:
        logger.opt(exception=exc).error(message)
    else:
        logger.error(message, exc_info=exc)  # type: ignore[arg-type]


# ------------------ 调用者上下文（contextvars） ------------------
# 工具 handler 通过这个拿到"当前发起 AI 请求的用户 QQ"，无需模型在参数里传，
# 也避免把 QQ 这种身份信息塞进 system prompt。
# 协程安全：每个 /ai 请求的 task 会有独立 copy，互不污染。
_current_user_qq: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar(
    "aitools_current_user_qq", default=None
)


def set_current_user_qq(qq: Optional[str]) -> contextvars.Token:
    """设置当前调用者 QQ，返回 token 供 reset 用。"""
    return _current_user_qq.set(qq)


def reset_current_user_qq(token: contextvars.Token) -> None:
    """还原调用者 QQ 上下文。"""
    _current_user_qq.reset(token)


def get_current_user_qq() -> Optional[str]:
    """工具 handler 内调用，拿到当前发起 AI 请求的用户 QQ。"""
    return _current_user_qq.get()


# 工具响应写日志时的预览上限（完整内容可能上千字符，避免刷屏）
_LOG_PREVIEW = 800

# ------------------ 协议常量 ------------------

OPEN_TAG = "<tool_call>"
CLOSE_TAG = "</tool_call>"
RESULT_OPEN = "<tool_result"

# 大小写不敏感、跨行；非贪婪避免一次吞掉多个块
_TOOL_BLOCK_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL | re.IGNORECASE)


# ------------------ 数据结构与注册表 ------------------

ToolHandler = Callable[[Dict[str, str]], Awaitable[str]]


@dataclass(frozen=True)
class Tool:
    """一个可被模型调用的工具。

    params: 参数名 → 参数说明（含是否必填），用于自动生成工具手册。
    handler: async (args: dict) -> str，返回给模型看的文本结果。
    """

    namespace: str
    name: str
    description: str
    handler: ToolHandler
    params: Dict[str, str] = field(default_factory=dict)

    @property
    def fullname(self) -> str:
        return f"{self.namespace}:{self.name}"


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: Dict[str, str]
    raw: str                       # 原始块文本（便于日志）


# 全局注册表：fullname → Tool
_TOOLS: Dict[str, Tool] = {}


def register_tool(tool: Tool) -> Tool:
    """注册工具；同名覆盖（便于测试替换）。返回工具本身，可作装饰器。"""
    _TOOLS[tool.fullname] = tool
    return tool


def get_tool(fullname: str) -> Optional[Tool]:
    return _TOOLS.get(fullname)


def registered_tools() -> List[Tool]:
    return list(_TOOLS.values())


def reset_registry() -> None:
    """仅供测试：清空注册表。"""
    _TOOLS.clear()
    _PACKAGES.clear()


# ------------------ 工具包（Package）------------------

@dataclass(frozen=True)
class Package:
    """一个工具包：含名称、简介和若干工具。

    工具按包组织：system prompt 只给包目录（名称+简介），AI 需要时调用
    pkg:activate 激活某包，系统返回该包内的工具详情，AI 再选择工具调用。
    避免工具增多后把所有工具信息一股脑灌给模型。
    """

    name: str
    description: str


_PACKAGES: Dict[str, Package] = {}


def register_package(pkg: Package) -> Package:
    _PACKAGES[pkg.name] = pkg
    return pkg


def get_package(name: str) -> Optional[Package]:
    return _PACKAGES.get(name)


def registered_packages() -> List[Package]:
    return list(_PACKAGES.values())


# ------------------ 协议解析 ------------------

def parse_tool_calls(text: str) -> List[ToolCall]:
    """从模型回复中解析全部 <tool_call> 块。

    块内首行必须是 ``命名空间:工具名``；参数支持两种写法（可混用）：
      1. 每行一个 ``key=value``（推荐，小模型最稳）；
      2. 首行之后整体是一个 JSON 对象（容错：模型自发输出 JSON 时）。
    """
    calls: List[ToolCall] = []
    for match in _TOOL_BLOCK_RE.finditer(text or ""):
        body = match.group(1).strip()
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        if not lines:
            continue
        name = lines[0].strip()
        if ":" not in name:
            # 协议错误也保留下来，执行时回"工具不存在/格式错误"，让模型自纠
            calls.append(ToolCall(name=name, args={}, raw=match.group(0)))
            continue

        args: Dict[str, str] = {}
        tail = lines[1:]
        if tail and tail[0][:1] in "{[":
            # 尝试整体 JSON
            try:
                parsed = json.loads("\n".join(tail))
                if isinstance(parsed, dict):
                    args = {str(k): ("" if v is None else str(v)) for k, v in parsed.items()}
                    tail = []
            except json.JSONDecodeError:
                pass  # 落到 key=value 逐行解析
        for line in tail:
            if "=" in line:
                key, value = line.split("=", 1)
                args[key.strip()] = value.strip()
        calls.append(ToolCall(name=name, args=args, raw=match.group(0)))
    return calls


def strip_tool_calls(text: str) -> str:
    """从最终回复中剥离工具块与可能残留的 result 块，再压缩多余空行。"""
    out = _TOOL_BLOCK_RE.sub("", text or "")
    out = re.sub(r"<tool_result\b.*?</tool_result>", "", out, flags=re.DOTALL | re.IGNORECASE)
    out = re.sub(r"[ \t]+\n", "\n", out)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


# ------------------ 包目录与工具手册 ------------------

def package_catalog() -> str:
    """生成模型侧的包目录（只有包名+简介），拼进 system prompt。

    与 tool_manual 的区别：不展开每个工具的参数，只给包列表；AI 需要时
    调用 pkg:activate 激活某包，系统返回该包的工具详情。
    无注册包时返回空串。
    """
    if not _PACKAGES:
        return ""
    blocks: List[str] = [
        "# 工具调用协议",
        "当你仅凭自身知识无法可靠回答时（最新资讯、近期版本/数值、你不确定的事实），"
        "必须先调用工具获取信息，禁止编造；闲聊、观点、创作、常识性问题不要调用工具。",
        "",
        "## 两阶段调用",
        "1. 先激活包：输出 pkg:activate 块，系统返回该包内的工具列表；",
        "2. 再调用工具：按返回的工具列表选择具体工具，输出 命名空间:工具名 块。",
        "",
        "调用块格式（块外不要写解释，不要用代码围栏包裹）：",
        f"{OPEN_TAG}",
        "包名:工具名   （或 pkg:activate）",
        "参数名=参数值",
        CLOSE_TAG,
        "系统执行后以 <tool_result name=\"...\"> 回传结果；拿到结果后，"
        "要么直接输出最终回答（不含工具块），要么继续调用。一次只输出一个块。",
        "",
        "判定规则（按顺序）：",
        "1. 用户明确要求搜索/联网/查一下/最新/最近，或问题涉及新闻、近期事件、"
        "软件/游戏新版本与改动、实时数据 → 立即激活对应包并调用，不要反问；",
        "2. 你不确定或知识可能过时的具体事实 → 调用核实；",
        "2.1 用户只是想测试搜索功能（如\u201c测试联网搜索/搜一下试试\u201d）但没给关键词时，"
        "不要反问，直接选一个通用关键词（如\u201c今日热点新闻\u201d）调用一次；",
        "3. 闲聊、观点、写作、写代码、常识 → 不要调用，直接回答。",
        "",
        "示例（用户消息：泰拉瑞亚最新版本更新了什么）：",
        "第一步——激活包：",
        f"{OPEN_TAG}",
        "pkg:activate",
        "name=web",
        CLOSE_TAG,
        "第二步——系统返回 web 包的工具后，调用搜索：",
        f"{OPEN_TAG}",
        "web:search",
        "query=泰拉瑞亚 最新版本 更新内容",
        CLOSE_TAG,
        "第三步——系统返回搜索结果后，输出最终回答（带 [序号] 引用）。",
        "",
        "可用工具包：",
    ]
    for pkg in _PACKAGES.values():
        blocks.append(f"- {pkg.name}：{pkg.description}")
    blocks.append("")
    blocks.append("最终回答中不要出现工具块；如果引用了工具返回的资料，按资料里的 [序号] 标注来源。")
    return "\n".join(blocks)


def _package_tools_manual(pkg_name: str) -> str:
    """生成单个包内的工具详情（激活后返回给模型）。"""
    tools = [t for t in _TOOLS.values() if t.namespace == pkg_name]
    if not tools:
        return f"包「{pkg_name}」不存在或没有工具。"
    lines = [f"包 [{pkg_name}] 已激活，可用工具："]
    for tool in tools:
        lines.append(f"## {tool.fullname}")
        lines.append(f"说明：{tool.description}")
        if tool.params:
            lines.append("参数：")
            for pname, pdesc in tool.params.items():
                lines.append(f"  {pname}={pdesc}")
        lines.append("")
    lines.append("现在选择一个工具调用；不需要时直接回答用户。")
    return "\n".join(lines)


# ------------------ agent 循环 ------------------

ChatFn = Callable[[List[Dict[str, str]]], Awaitable[str]]
EventFn = Callable[[str, Dict[str, object]], Awaitable[None]]


def _preview(text: str, limit: int = _LOG_PREVIEW) -> str:
    """日志预览：压平换行并截断，标注原文总长度。"""
    flat = (text or "").replace("\r", "").replace("\n", " ↵ ")
    if len(flat) <= limit:
        return flat
    return f"{flat[:limit]} …（共 {len(text)} 字符，已截断）"


async def _safe_execute(call: ToolCall) -> str:
    """执行工具，任何错误都转成给模型的文本（让模型自纠，而不是炸给用户）。"""
    tool = _TOOLS.get(call.name)
    if tool is None:
        available = "、".join(sorted(_TOOLS)) or "（当前无可用工具）"
        logger.warning(f"工具调用失败：模型请求了不存在的工具 {call.name}，参数={call.args}")
        return f"错误：工具「{call.name}」不存在或格式错误。可用工具：{available}。请直接回答用户。"
    logger.info(f"执行工具：{call.name}，参数={call.args}")
    try:
        output = await tool.handler(call.args)
    except Exception as exc:  # noqa: BLE001
        _log_exception(f"工具 {call.name} 执行异常：{exc!r}", exc)
        return (
            f"错误：工具 {call.name} 执行失败：{exc}。"
            "不要重复调用同一工具，请基于已有信息回答或如实告知用户无法获取该信息。"
        )
    output = (output or "").strip()
    if not output:
        logger.warning(f"工具 {call.name} 执行成功但返回为空")
        return "工具执行成功但没有返回内容。"
    logger.info(f"工具 {call.name} 调用响应（{len(output)} 字符）：{_preview(output)}")
    return output


def _handle_activate(call: ToolCall) -> str:
    """处理 pkg:activate 元调用：返回指定包的工具详情。不计入 tool_steps。"""
    pkg_name = call.args.get("name", "").strip()
    if not pkg_name:
        pkgs = "、".join(sorted(_PACKAGES)) or "（无可用包）"
        return f"错误：请指定要激活的包名（name=包名）。可用包：{pkgs}"
    pkg = _PACKAGES.get(pkg_name)
    if pkg is None:
        pkgs = "、".join(sorted(_PACKAGES)) or "（无可用包）"
        logger.warning(f"激活包失败：模型请求了不存在的包 {pkg_name}")
        return f"错误：包「{pkg_name}」不存在。可用包：{pkgs}"
    logger.info(f"激活工具包：{pkg_name}")
    return _package_tools_manual(pkg_name)


async def run_agent_turn(
    chat: ChatFn,
    messages: List[Dict[str, str]],
    *,
    on_event: Optional[EventFn] = None,
    max_tool_steps: int = 2,
) -> str:
    """跑一轮「模型回复 → 解析工具调用 → 执行回灌 → 再回复」的循环。

    - chat: 发送 messages 返回模型回复文本的协程（由调用方绑定模型参数）；
    - messages 会被原地追加 assistant/tool_result 消息；
    - on_event(event, payload)："before_chat" {"step": int}、
      "tool_call" ToolCall 的 dict、"tool_error" {"name","error"}；
    - 返回剥离了工具块的最终回复文本。
    """
    async def emit(event: str, payload: Dict[str, object]) -> None:
        if on_event is not None:
            await on_event(event, payload)

    last_reply = ""
    tool_steps = 0
    total_rounds = 0
    max_rounds = max_tool_steps * 3 + 3  # 安全阀：含 activate 轮的总上限

    while total_rounds < max_rounds:
        total_rounds += 1
        if total_rounds > 1:
            logger.info(f"结果已回灌，AI 正在处理（第 {total_rounds} 轮模型请求）…")
        await emit("before_chat", {"step": tool_steps})
        last_reply = await chat(messages)
        calls = parse_tool_calls(last_reply)
        if not calls:
            if total_rounds > 1:
                logger.info(f"AI 已给出最终回复（{len(last_reply)} 字符）")
            return strip_tool_calls(last_reply)

        # 分离 activate 元调用与普通工具调用
        activates = [c for c in calls if c.name == "pkg:activate"]
        tool_calls = [c for c in calls if c.name != "pkg:activate"]

        messages.append({"role": "assistant", "content": last_reply})

        # 先处理 activate（不计 tool_steps）
        for ac in activates:
            logger.info(f"AI 请求激活包：{ac.args.get('name', '')}")
            result = _handle_activate(ac)
            messages.append(
                {"role": "user", "content": f'{RESULT_OPEN} name="pkg:activate">\n{result}\n</tool_result>'}
            )

        # 再处理普通工具调用
        if tool_calls:
            if tool_steps >= max_tool_steps:
                logger.warning(f"工具调用轮数达到上限 {max_tool_steps}，终止循环")
                return "抱歉，工具调用次数已达上限，请换个问法或稍后再试。"
            tool_steps += 1
            logger.info(
                f"AI 请求调用工具（第 {tool_steps} 轮，共 {len(tool_calls)} 个调用）："
                + "；".join(f"{c.name} 参数={dict(c.args)}" for c in tool_calls)
            )
            for call in tool_calls:
                await emit(
                    "tool_call",
                    {"name": call.name, "args": dict(call.args), "raw": call.raw},
                )
                result = await _safe_execute(call)
                if result.startswith("错误："):
                    await emit("tool_error", {"name": call.name, "error": result})
                messages.append(
                    {
                        "role": "user",
                        "content": f'{RESULT_OPEN} name="{call.name}">\n{result}\n</tool_result>',
                    }
                )
            logger.info(f"本轮 {len(tool_calls)} 个工具全部执行完毕，结果已回灌等待 AI 继续")

    logger.warning(f"总轮数达到安全上限 {max_rounds}，终止循环")
    return strip_tool_calls(last_reply)
