"""工具返回值的错误规范。

约定：工具失败**不抛异常**，而是返回一段以「错误：」开头的中文文本。
agent 循环据此把该次 tool_result 标记为 ok=False，模型看到错误自行纠正，
而不是把异常炸给用户。

注意：判断成功/失败**只认这个前缀**（errors.is_error），
不要在别处另起一套错误文案约定。
"""
from __future__ import annotations

ERROR_PREFIX = "错误："


def is_error(text: str) -> bool:
    """这段工具输出是不是一条错误。"""
    return (text or "").startswith(ERROR_PREFIX)


def tool_error(message: str) -> str:
    """构造一条错误输出（自动加前缀）。"""
    return f"{ERROR_PREFIX}{message}"
