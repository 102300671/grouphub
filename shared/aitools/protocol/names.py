"""工具名映射：文本协议名 ⇄ 原生 function calling 名。

文本协议用「命名空间:工具名」（web:search）；原生 function calling 的函数名不含冒号，
用「命名空间_工具名」（web_search）。映射**由注册表生成**，不手写清单。

原生 function calling 的完整编解码在 M3 建立（natcodec.py，见 .ai/PLAN.md）；
本模块只提供名字映射，供 M3 复用。
"""
from __future__ import annotations

from typing import Dict, Optional

from ..registry import Registry


def build_name_map(registry: Registry) -> Dict[str, str]:
    """fullname → 原生函数名。"""
    return {t.fullname: t.fullname.replace(":", "_") for t in registry.tools()}


def build_reverse_map(registry: Registry) -> Dict[str, str]:
    """原生函数名 → fullname。"""
    return {native: full for full, native in build_name_map(registry).items()}


def to_native_name(registry: Registry, fullname: str) -> Optional[str]:
    """文本协议名 → 原生函数名；工具未注册返回 None。"""
    return build_name_map(registry).get(fullname)


def to_text_name(registry: Registry, native_name: str) -> Optional[str]:
    """原生函数名 → 文本协议名；工具未注册返回 None。"""
    return build_reverse_map(registry).get(native_name)
