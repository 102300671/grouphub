"""agent 循环：轨迹事件、回灌、步数上限、错误处理。

轨迹事件结构与 M1 之前一致（QQ 端收集后随 assistant 消息落库，
Web 端前端按同构结构还原思维链/工具链）。
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from fakes import SITE_BASE_URL, FakeRenderer, FakeSearch, FakeSite  # noqa: E402
from shared.aitools import build_agent  # noqa: E402
from shared.aitools.protocol.textcodec import CLOSE_TAG, OPEN_TAG  # noqa: E402
from shared.aitools.registry import Context  # noqa: E402


def _agent():
    return build_agent(
        search=FakeSearch({}),
        renderer=FakeRenderer({}),
        site=FakeSite({}),
        site_base_url=SITE_BASE_URL,
    )


def _tool_block(name: str, body: str = "") -> str:
    return f"{OPEN_TAG}\n{name}\n{body}\n{CLOSE_TAG}"


class TestLoop(unittest.TestCase):
    def _run(self, replies, *, max_tool_steps=2, tool="web:search", body="query=x"):
        agent = _agent()
        events = []
        state = {"i": 0}

        async def chat(messages, *, on_reasoning=None):
            reply = replies[min(state["i"], len(replies) - 1)]
            state["i"] += 1
            if on_reasoning is not None:
                await on_reasoning("思考中")
            return reply

        async def on_event(kind, payload):
            events.append((kind, dict(payload)))

        messages = [{"role": "user", "content": "问题"}]
        answer = asyncio.run(
            agent.run_turn(
                chat,
                messages,
                ctx=Context(user_qq="123", channel="qq"),
                on_event=on_event,
                max_tool_steps=max_tool_steps,
            )
        )
        return answer, events, messages

    def test_plain_answer_no_tool(self):
        answer, events, messages = self._run(["直接回答"])
        self.assertEqual(answer, "直接回答")
        self.assertEqual([k for k, _ in events], ["round", "reasoning"])
        self.assertEqual(events[0][1]["index"], 0)

    def test_tool_call_then_answer(self):
        answer, events, messages = self._run(
            [_tool_block("web:search", "query=天气"), "最终回答"]
        )
        kinds = [k for k, _ in events]
        self.assertEqual(kinds, ["round", "reasoning", "tool_call", "tool_result", "round", "reasoning"])
        call = events[2][1]
        self.assertEqual(call["name"], "web:search")
        self.assertEqual(call["kind"], "tool")
        self.assertEqual(call["args"], {"query": "天气"})
        self.assertEqual(call["id"], 1)
        result = events[3][1]
        self.assertEqual(result["name"], "web:search")
        self.assertTrue(result["ok"])
        self.assertIn("以下是联网搜索到的参考资料", result["content"])
        # 结果回灌成 user 消息
        self.assertEqual(messages[-1]["role"], "user")
        self.assertIn("<tool_result name=\"web:search\">", messages[-1]["content"])
        # assistant 原文也留在上下文里
        self.assertEqual(messages[-2]["role"], "assistant")
        self.assertEqual(answer, "最终回答")

    def test_activate_does_not_count_tool_steps(self):
        agent = _agent()
        events = []
        state = {"i": 0}
        replies = [
            _tool_block("pkg:activate", "name=web"),
            _tool_block("web:search", "query=x"),
            "答案",
        ]

        async def chat(messages, *, on_reasoning=None):
            reply = replies[min(state["i"], len(replies) - 1)]
            state["i"] += 1
            return reply

        async def on_event(kind, payload):
            events.append((kind, dict(payload)))

        async def run():
            return await agent.run_turn(
                chat,
                [{"role": "user", "content": "问题"}],
                ctx=Context(user_qq="1", channel="qq"),
                on_event=on_event,
                max_tool_steps=1,
            )

        answer = asyncio.run(run())
        self.assertEqual(answer, "答案")
        calls = [p for k, p in events if k == "tool_call"]
        self.assertEqual([c["kind"] for c in calls], ["activate", "tool"])
        self.assertEqual(calls[0]["name"], "pkg:activate")
        # 激活包返回工具手册
        results = [p for k, p in events if k == "tool_result"]
        self.assertIn("包 [web] 已激活，可用工具：", results[0]["content"])

    def test_unknown_tool_returns_error_text(self):
        agent = _agent()
        events = []
        state = {"i": 0}

        async def chat(messages, *, on_reasoning=None):
            state["i"] += 1
            if state["i"] == 1:
                return _tool_block("web:nope", "x=1")
            return "好的，直接回答。"

        async def on_event(kind, payload):
            events.append((kind, dict(payload)))

        async def run():
            return await agent.run_turn(
                chat,
                [{"role": "user", "content": "问题"}],
                ctx=Context(channel="qq"),
                on_event=on_event,
            )

        answer = asyncio.run(run())
        # 错误不是抛给用户，而是回灌给模型（模型看到后再自己改口）
        self.assertEqual(answer, "好的，直接回答。")
        result = [p for k, p in events if k == "tool_result"][0]
        self.assertFalse(result["ok"])
        self.assertTrue(
            result["content"].startswith("错误：工具「web:nope」不存在或格式错误。")
        )

    def test_tool_exception_becomes_error_text(self):
        agent = build_agent(
            search=FakeSearch({"search": "raise"}),
            renderer=FakeRenderer({}),
            site=FakeSite({}),
        )
        events = []
        state = {"i": 0}

        async def chat(messages, *, on_reasoning=None):
            state["i"] += 1
            if state["i"] == 1:
                return _tool_block("web:search", "query=x")
            return "搜索失败，如实告知用户。"

        async def on_event(kind, payload):
            events.append((kind, dict(payload)))

        async def run():
            return await agent.run_turn(
                chat,
                [{"role": "user", "content": "问题"}],
                ctx=Context(channel="qq"),
                on_event=on_event,
            )

        answer = asyncio.run(run())
        self.assertEqual(answer, "搜索失败，如实告知用户。")
        result = [p for k, p in events if k == "tool_result"][0]
        self.assertFalse(result["ok"])
        self.assertIn("执行失败", result["content"])

    def test_max_tool_steps(self):
        agent = _agent()

        async def chat(messages, *, on_reasoning=None):
            return _tool_block("web:search", "query=x")

        async def run():
            return await agent.run_turn(
                chat,
                [{"role": "user", "content": "问题"}],
                ctx=Context(channel="qq"),
                max_tool_steps=1,
            )

        answer = asyncio.run(run())
        self.assertEqual(answer, "抱歉，工具调用次数已达上限，请换个问法或稍后再试。")

    def test_chat_receives_on_reasoning(self):
        """chat 必须接受 on_reasoning 关键字参数（旧实现漏传导致 /ai 必抛 TypeError）。"""
        agent = _agent()
        seen = {}

        async def chat(messages, *, on_reasoning=None):
            seen["has_cb"] = on_reasoning is not None
            if on_reasoning is not None:
                await on_reasoning("思考")
            return "答案"

        async def run():
            return await agent.run_turn(
                chat,
                [{"role": "user", "content": "问题"}],
                ctx=Context(channel="qq"),
            )

        asyncio.run(run())
        self.assertTrue(seen["has_cb"])


if __name__ == "__main__":
    unittest.main()
