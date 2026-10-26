"""提示词片段：动态日期 + 工具包目录 + 工具手册。

这三段都会进 system prompt，**逐字影响模型行为**：
QQ 端与 Web 端必须共用同一份实现（两套文案 = 两套行为，已记入 .ai/RISKS.md 漂移 3）。
"""
from __future__ import annotations

from datetime import datetime
from typing import List

from .protocol.textcodec import CLOSE_TAG, OPEN_TAG
from .registry import Registry


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


def package_catalog(registry: Registry) -> str:
    """生成模型侧的包目录（只有包名+简介），拼进 system prompt。

    与 tool_manual 的区别：不展开每个工具的参数，只给包列表；AI 需要时
    调用 pkg:activate 激活某包，系统返回该包的工具详情。
    无注册包时返回空串。
    """
    packages = registry.packages()
    if not packages:
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
        "2.1 用户只是想测试搜索功能（如“测试联网搜索/搜一下试试”）但没给关键词时，"
        "不要反问，直接选一个通用关键词（如“今日热点新闻”）调用一次；",
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
    for pkg in packages:
        blocks.append(f"- {pkg.name}：{pkg.description}")
    blocks.append("")
    blocks.append("最终回答中不要出现工具块；如果引用了工具返回的资料，按资料里的 [序号] 标注来源。")
    return "\n".join(blocks)


def package_tools_manual(registry: Registry, pkg_name: str) -> str:
    """生成单个包内的工具详情（激活后返回给模型）。"""
    tools = registry.tools_of(pkg_name)
    if not tools:
        return f"包「{pkg_name}」不存在或没有工具。"
    lines = [f"包 [{pkg_name}] 已激活，可用工具："]
    for tool in tools:
        lines.append(f"## {tool.fullname}")
        lines.append(f"说明：{tool.description}")
        if tool.params:
            lines.append("参数：")
            for pname, pdesc in tool.param_text().items():
                lines.append(f"  {pname}={pdesc}")
        lines.append("")
    lines.append("现在选择一个工具调用；不需要时直接回答用户。")
    return "\n".join(lines)
