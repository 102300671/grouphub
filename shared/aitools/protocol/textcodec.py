"""文本协议编解码：模型输出里的工具块 ↔ 结构化调用。

协议（提示词中会完整教给模型）::

    <tool_call>
    命名空间:工具名
    参数名=参数值
    </tool_call>

    <tool_result name="命名空间:工具名">...</tool_result>

模型在回复中输出工具调用块，bot 解析执行后把结果回灌，循环直到模型给出不含工具块的最终回答。

本模块**不认识**任何具体工具，只负责「文本 ↔ 结构」的转换；
工具执行在 loop.py，工具实现 in tools/。

两份正则副本（qqbot 与 backend 各一份，已于 M1 合并到此，见 .ai/ARCHITECTURE.md）：
此处的容错规则来自 QQ 端那份更完整的实现。
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from ..registry import ToolCall

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
