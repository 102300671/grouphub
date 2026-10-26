"""agent 循环：模型回复 → 解析工具调用 → 执行回灌 → 再回复。

一轮「问答」的完整生命周期在 run_turn() 里：
  1. 调模型拿回复；
  2. 回复里没有工具块 → 剥离后作为最终答案返回；
  3. 有工具块 → 先处理 pkg:activate（激活包，不计步数），再执行真实工具；
  4. 把结果包成 <tool_result> 消息追加进 messages，回到第 1 步。

工具失败不抛异常给用户：_safe_execute 把异常转成「错误：…」文本喂回模型，让模型自纠。

轨迹事件（on_event）与 Web 端 SSE 同构，QQ 端收集后随 assistant 消息落库：
    "round"        {"index": int}
    "reasoning"    {"text": str}
    "tool_call"    {"id","kind":"activate"|"tool","name","args","raw"}
    "tool_result"  {"id","name","ok","summary","content"}
"""
from __future__ import annotations

from typing import Awaitable, Callable, Dict, List, Optional

from .errors import is_error
from .logs import log_exception, logger
from .ports import ChatFn
from .prompt import package_tools_manual
from .protocol.textcodec import RESULT_OPEN, parse_tool_calls, strip_tool_calls
from .registry import Context, Registry, ToolCall

# 工具响应写日志时的预览上限（完整内容可能上千字符，避免刷屏）
_LOG_PREVIEW = 800

# 轨迹事件回调：async (event, payload) -> None
EventFn = Callable[[str, Dict[str, object]], Awaitable[None]]


def _preview(text: str, limit: int = _LOG_PREVIEW) -> str:
    """日志预览：压平换行并截断，标注原文总长度。"""
    flat = (text or "").replace("\r", "").replace("\n", " ↵ ")
    if len(flat) <= limit:
        return flat
    return f"{flat[:limit]} …（共 {len(text)} 字符，已截断）"


async def _safe_execute(registry: Registry, call: ToolCall, ctx: Context) -> str:
    """执行工具，任何错误都转成给模型的文本（让模型自纠，而不是炸给用户）。"""
    tool = registry.get_tool(call.name)
    if tool is None:
        available = "、".join(sorted(registry.tool_names())) or "（当前无可用工具）"
        logger.warning(f"工具调用失败：模型请求了不存在的工具 {call.name}，参数={call.args}")
        return f"错误：工具「{call.name}」不存在或格式错误。可用工具：{available}。请直接回答用户。"
    logger.info(f"执行工具：{call.name}，参数={call.args}")
    try:
        output = await tool.handler(call.args, ctx)
    except Exception as exc:  # noqa: BLE001
        log_exception(f"工具 {call.name} 执行异常：{exc!r}", exc)
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


def _handle_activate(registry: Registry, call: ToolCall) -> str:
    """处理 pkg:activate 元调用：返回指定包的工具详情。不计入 tool_steps。"""
    pkg_name = call.args.get("name", "").strip()
    if not pkg_name:
        pkgs = "、".join(sorted(p.name for p in registry.packages())) or "（无可用包）"
        return f"错误：请指定要激活的包名（name=包名）。可用包：{pkgs}"
    pkg = registry.get_package(pkg_name)
    if pkg is None:
        pkgs = "、".join(sorted(p.name for p in registry.packages())) or "（无可用包）"
        logger.warning(f"激活包失败：模型请求了不存在的包 {pkg_name}")
        return f"错误：包「{pkg_name}」不存在。可用包：{pkgs}"
    logger.info(f"激活工具包：{pkg_name}")
    return package_tools_manual(registry, pkg_name)


async def run_turn(
    registry: Registry,
    chat: ChatFn,
    messages: List[Dict[str, str]],
    *,
    ctx: Context,
    on_event=None,
    max_tool_steps: int = 2,
) -> str:
    """跑一轮「模型回复 → 解析工具调用 → 执行回灌 → 再回复」的循环。

    - chat: 签名 chat(messages, on_reasoning=回调)，返回模型回复文本；
      on_reasoning(text) 接收 reasoning_content 思维链分片（QQ 不展示，仅收集落库）；
      **必须接受 on_reasoning 关键字参数**（旧实现漏传导致 /ai 必抛 TypeError，已修）。
    - messages 会被原地追加 assistant/tool_result 消息；
    - ctx: 本轮上下文（调用者身份、图片发送出口），随工具调用传下去；
    - on_event(event, payload) 推送轨迹事件，调用方收集后随 assistant 消息同步给后端；
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
            "ok": not is_error(result),
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
            result = _handle_activate(registry, ac)
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
                result = await _safe_execute(registry, call, ctx)
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
