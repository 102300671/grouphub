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
from datetime import datetime
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
# 注：实测部分模型（agnes-3.0-flash 等）标签漂移——闭标签误写为 think/function、
# 工具名被尖括号包裹、混入 parameter 等杂标签；以下一组正则用于容错解析。
_TOOL_BLOCK_RE = re.compile(r"<tool_call\b[^>]*>", re.IGNORECASE)
_TOOL_CLOSE_RE = re.compile(
    r"<\s*/\s*(?:tool_call|function_call|function_calls|function|invoke|tool|think)\s*>",
    re.IGNORECASE,
)
_FUNCTION_TAG_RE = re.compile(r"<\s*function\s*[=\s]\s*([^>]+?)\s*>", re.IGNORECASE)
_FUNCTION_CLOSE_RE = re.compile(r"<\s*/\s*function\s*>", re.IGNORECASE)
_PURGE_TAG_RE = re.compile(
    r"<\s*/?\s*(?:parameter(?:\s*=\s*\w+)?|function|invoke|think|tool_call)[^>]*>",
    re.IGNORECASE,
)
_RESULT_BLOCK_RE = re.compile(r"<tool_result\b.*?</tool_result>", re.DOTALL | re.IGNORECASE)
_NAME_RE = re.compile(r"([A-Za-z_]\w*\s*:\s*[A-Za-z_]\w*)")
_PARAM_KV_RE = re.compile(r"^<\s*parameter\s*=\s*(\w+)\s*>\s*(.*)$", re.IGNORECASE)
_TRAILING_CLOSE_RE = re.compile(r"\s*<\s*/\s*[a-z_]+\s*>\s*$", re.IGNORECASE)
_BARE_TAG_LINE_RE = re.compile(r"^<\s*/?\s*[a-z_]+\s*>$", re.IGNORECASE)
_CLOSE_PARAMETER = "<" + "/parameter>"


def _iter_tool_blocks(text: str):
    """遍历工具块 (start, end, body)；容忍错误闭合标签与未闭合块。"""
    for m in _TOOL_BLOCK_RE.finditer(text or ""):
        tail = text[m.end():]
        close = _TOOL_CLOSE_RE.search(tail)
        next_open = _TOOL_BLOCK_RE.search(tail)
        if next_open and (not close or next_open.start() < close.start()):
            end, body = m.end() + next_open.start(), tail[: next_open.start()]
        elif close:
            end, body = m.end() + close.end(), tail[: close.start()]
        else:
            end, body = len(text), tail
        yield m.start(), end, body


def _iter_function_blocks(text: str):
    """无 tool_call 外壳时的回退：识别 function 标签块。"""
    for m in _FUNCTION_TAG_RE.finditer(text or ""):
        tail = text[m.end():]
        cm = _FUNCTION_CLOSE_RE.search(tail)
        if cm:
            yield m.start(), m.end() + cm.end(), text[m.start(): m.end() + cm.end()]
        else:
            yield m.start(), len(text), text[m.start():]


def _parse_tool_body(body: str) -> Dict[str, object]:
    """解析块体：兼容标准 key=value、整体 JSON 与模型漂移出的杂标签。"""
    text = body.strip()
    name: Optional[str] = None
    name_from_tag = False
    fm = _FUNCTION_TAG_RE.search(text)
    if fm:
        nm = _NAME_RE.search(fm.group(1))
        if nm:
            name = nm.group(1).replace(" ", "")
            name_from_tag = True
            text = (text[: fm.start()] + "\n" + text[fm.end():])

    lines: List[str] = []
    for raw in text.splitlines():
        ln = raw.strip()
        if not ln or _BARE_TAG_LINE_RE.match(ln):
            continue
        lines.append(ln)

    if name is None:
        first = (lines[0] if lines else "").lstrip("<").rstrip(">").strip()
        nm = _NAME_RE.search(first)
        name = nm.group(1).replace(" ", "") if nm else first
        if lines:
            lines[0] = first

    args: Dict[str, str] = {}
    tail = lines if name_from_tag else lines[1:]
    joined = "\n".join(tail).strip()
    if joined[:1] in "{[":
        try:
            parsed = json.loads(joined)
            if isinstance(parsed, dict):
                args = {str(k): ("" if v is None else str(v)) for k, v in parsed.items()}
                tail = []
        except json.JSONDecodeError:
            pass
    for line in tail:
        ln = line.replace(_CLOSE_PARAMETER, "").strip()
        pm = _PARAM_KV_RE.match(ln)
        if pm:
            key, value = pm.group(1), pm.group(2)
        elif "=" in ln:
            key, value = ln.split("=", 1)
        else:
            continue
        value = _TRAILING_CLOSE_RE.sub("", value).rstrip(">").strip()
        args[key.strip().strip("<>")] = value
    return {"name": name or "", "args": args}


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
    """从模型回复中解析全部工具块（容忍标签漂移）。

    标准写法：首行 ``命名空间:工具名``，后续每行一个 ``key=value`` 或整体 JSON；
    同时容错：闭标签误写（think/function 等）、工具名被尖括号包裹、
    parameter/function 等训练格式杂标签、纯 function 标签块。
    协议错误（名字不含冒号）也保留，执行时回错让模型自纠。
    """
    calls: List[ToolCall] = []
    blocks = list(_iter_tool_blocks(text or ""))
    if not blocks:
        blocks = list(_iter_function_blocks(text or ""))
    for start, end, raw_block in blocks:
        parsed = _parse_tool_body(raw_block)
        name = str(parsed.get("name") or "")
        if not name:
            continue
        calls.append(
            ToolCall(
                name=name,
                args=dict(parsed.get("args") or {}),
                raw=text[start:end],
            )
        )
    return calls


def strip_tool_calls(text: str) -> str:
    """从最终回复中剥离工具块与可能残留的 result 块，再压缩多余空行。"""
    out = text or ""
    spans = [(s, e) for s, e, _ in _iter_tool_blocks(out)]
    spans += [(s, e) for s, e, _ in _iter_function_blocks(out)]
    for start, end in sorted(spans, reverse=True):
        out = out[:start] + out[end:]
    out = _RESULT_BLOCK_RE.sub("", out)
    out = _PURGE_TAG_RE.sub("", out)
    out = re.sub(r"[ \t]+\n", "\n", out)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


# ------------------ 包目录与工具手册 ------------------

def date_hint() -> str:
    """动态日期提示：每次问答注入 system，校准模型对「现在/今年/最新」的认知。

    模型训练数据可能停留在旧年份（如 2025），静态提示词无法跨年，
    因此日期在请求时实时生成。
    """
    now = datetime.now()
    weekdays = "一二三四五六日"
    return (
        "# 当前时间\n"
        f"今天是 {now.year} 年 {now.month} 月 {now.day} 日"
        f"（星期{weekdays[now.weekday()]}），北京时间（UTC+8）。\n"
        "- 回答涉及「现在、今天、今年、最新、最近、当前、近期」等时效性问题时，"
        "一律以该日期为准；你的训练数据可能停留在更早的时间，严禁沿用旧年份（例如 2025）。\n"
        "- 需要最新信息（新闻、新版本、近期作品/赛事/价格等）时先调用 web:search 联网搜索，"
        f"搜索关键词优先带上当前年份 {now.year}；结论以搜索结果标注的发布日期为准，"
        "不要把旧年份的结果当作最新。\n"
        "- 若无法确认当前日期，可先用 web:search 搜索「今天日期」校准。"
    )


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

# chat(messages, on_reasoning=回调) -> 模型回复文本；on_reasoning 接收思维链分片
ChatFn = Callable[..., Awaitable[str]]
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

    - chat: 签名 chat(messages, on_reasoning=回调)，返回模型回复文本；
      on_reasoning(text) 接收 reasoning_content 思维链分片（QQ 不展示，仅收集落库）；
    - messages 会被原地追加 assistant/tool_result 消息；
    - on_event(event, payload) 推送与网页端 SSE 同构的轨迹事件，调用方收集后
      随 assistant 消息同步给后端，前端可完整还原思维链/工具链：
        "round"        {"index": int}
        "reasoning"    {"text": str}
        "tool_call"    {"id","kind":"activate"|"tool","name","args","raw"}
        "tool_result"  {"id","name","ok","summary","content"}
    - 返回剥离了工具块的最终回复文本。
    """

    async def emit(event: str, payload: Dict[str, object]) -> None:
        if on_event is not None:
            await on_event(event, payload)

    async def _on_reasoning(text: str) -> None:
        await emit("reasoning", {"text": text})

    def _result_payload(result: str) -> Dict[str, object]:
        # 与后端 _sse_response 的预览规则保持一致
        return {
            "ok": not result.startswith("错误："),
            "summary": result.replace("\n", " ").strip()[:120],
            "content": result,
        }

    last_reply = ""
    tool_steps = 0
    total_rounds = 0
    call_seq = 0
    max_rounds = max_tool_steps * 3 + 3  # 安全阀：含 activate 轮的总上限

    while total_rounds < max_rounds:
        total_rounds += 1
        if total_rounds > 1:
            logger.info(f"结果已回灌，AI 正在处理（第 {total_rounds} 轮模型请求）…")
        await emit("round", {"index": total_rounds - 1})
        last_reply = await chat(messages, on_reasoning=_on_reasoning)
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
            call_seq += 1
            call_obj: Dict[str, object] = {
                "id": call_seq,
                "kind": "activate",
                "name": "pkg:activate",
                "args": dict(ac.args),
                "raw": ac.raw,
            }
            await emit("tool_call", call_obj)
            result = _handle_activate(ac)
            call_obj["result"] = _result_payload(result)
            await emit(
                "tool_result",
                {"id": call_seq, "name": "pkg:activate", **call_obj["result"]},
            )
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
                call_seq += 1
                call_obj = {
                    "id": call_seq,
                    "kind": "tool",
                    "name": call.name,
                    "args": dict(call.args),
                    "raw": call.raw,
                }
                await emit("tool_call", call_obj)
                result = await _safe_execute(call)
                call_obj["result"] = _result_payload(result)
                await emit(
                    "tool_result",
                    {"id": call_seq, "name": call.name, **call_obj["result"]},
                )
                messages.append(
                    {
                        "role": "user",
                        "content": f'{RESULT_OPEN} name="{call.name}">\n{result}\n</tool_result>',
                    }
                )
            logger.info(f"本轮 {len(tool_calls)} 个工具全部执行完毕，结果已回灌等待 AI 继续")

    logger.warning(f"总轮数达到安全上限 {max_rounds}，终止循环")
    return strip_tool_calls(last_reply)
