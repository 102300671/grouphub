"""8 个工具的黄金回归 + 工具手册文案锁定。

黄金用例（fixtures/tools_golden.json）由 M1 迁移时的一次性比对产出：
旧实现（qqbot 的 ai.py / render.py / work.py 里的工具实现）与新实现
（shared/aitools/tools/）对同一批输入跑一遍，输出逐字相同后才冻结。

另有一组「手册文案」断言：工具手册是发给模型的 prompt，
参数说明必须与 M1 之前逐字相同（默认值只能来自 Param.default 一处）。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from fakes import (  # noqa: E402
    SITE_BASE_URL,
    FakeRenderer,
    FakeSearch,
    FakeSink,
    FakeSite,
)
from shared.aitools import build_agent  # noqa: E402
from shared.aitools.registry import Context, ImageArtifact  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "tools_golden.json")


class QQStyleSink:
    """QQ 端 ImageSink 适配器的等价物：内部吞异常并回一条提示（不是抛）。"""

    def __init__(self, inner):
        self.inner = inner

    async def send(self, artifact: ImageArtifact) -> str:
        try:
            return await self.inner.send(artifact)
        except Exception:  # noqa: BLE001
            return "渲染成功但图片发送失败（详见日志），请改用文本回复用户。"


def _build(case):
    cfg = case.get("cfg", {})
    agent = build_agent(
        search=FakeSearch(cfg),
        renderer=FakeRenderer(cfg),
        site=FakeSite(cfg),
        site_base_url=SITE_BASE_URL,
    )
    sink = None if case.get("no_sink") else QQStyleSink(FakeSink(cfg))
    ctx = Context(
        user_qq=None if case.get("no_user_qq") else "123456",
        channel="qq",
        sink=sink,
    )
    return agent, ctx


def _run_case(case):
    agent, ctx = _build(case)
    tool = agent.registry.get_tool(case["tool"])
    assert tool is not None, f"工具未注册：{case['tool']}"
    try:
        return asyncio.run(tool.handler(case["args"], ctx))
    except Exception as exc:  # noqa: BLE001
        return f"__RAISE__{type(exc).__name__}: {exc}"


class TestToolsGolden(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(FIXTURE, encoding="utf-8") as fh:
            cls.cases = json.load(fh)

    def test_golden_file_not_empty(self):
        self.assertGreaterEqual(len(self.cases), 40)

    def test_all_tools_covered(self):
        covered = {c["tool"] for c in self.cases}
        self.assertEqual(
            covered,
            {
                "web:search",
                "web:text",
                "web:md",
                "web:html",
                "web:screenshot",
                "site:hot",
                "site:search",
                "site:add",
            },
        )

    def test_output_matches_golden(self):
        for case in self.cases:
            with self.subTest(tool=case["tool"], args=case["args"]):
                self.assertEqual(_run_case(case), case["expect"])

    def test_golden_recorded_old_and_new_agree(self):
        """黄金文件记录了旧实现输出，防止有人手改期望值糊弄过去。"""
        for case in self.cases:
            with self.subTest(tool=case["tool"], args=case["args"]):
                self.assertTrue(case["same"], "该用例的旧新实现曾不一致")
                self.assertEqual(case["expect"], case["legacy"])


class TestToolManualText(unittest.TestCase):
    """工具手册 = 发给模型的 prompt，参数说明必须与 M1 之前逐字相同。"""

    EXPECTED = {
        "web:search": {
            "query": "搜索关键词（必填，精炼，不要包含'帮我搜一下'等客套话）",
        },
        "web:text": {
            "content": "要渲染的文本（必填）",
            "width": "图片宽度像素（可选，默认 680）",
        },
        "web:md": {
            "content": "要渲染的 Markdown 文本（必填）",
            "width": "图片宽度像素（可选，默认 860）",
        },
        "web:html": {
            "content": "要渲染的 HTML 片段（必填，需含完整 <style> 或内联样式）",
            "width": "图片宽度像素（可选，默认 900）",
        },
        "web:screenshot": {
            "url": "要截图的网页地址（必填，可省略 https:// 前缀）",
        },
        "site:hot": {
            "limit": "返回条数（可选，默认 5，最大 20）",
        },
        "site:search": {
            "keyword": "搜索关键词（必填，作品名/作者名/标签等）",
            "type": "作品类型（可选）：novel/anime/movie/gallery/fanwork/other 或中文",
            "limit": "返回条数（可选，默认 10，最大 50）",
        },
        "site:add": {
            "title": "作品标题（必填）",
            "author": "作者（可选，建议先 web:search 查到再填）",
            "type": "类型（可选）：novel/anime/movie/gallery/fanwork/other 或中文",
            "summary": "一句话简介（可选，建议先 web:search 查到再填）",
            "tags": "标签（可选，逗号分隔，如：百合,连载中）",
            "links": '外链（可选，格式 "站点名=URL,站点名=URL"，如：起点=https://...）',
        },
    }

    def setUp(self):
        self.agent = build_agent(
            search=FakeSearch({}),
            renderer=FakeRenderer({}),
            site=FakeSite({}),
            site_base_url=SITE_BASE_URL,
        )

    def test_param_text_is_unchanged(self):
        for fullname, expected in self.EXPECTED.items():
            with self.subTest(tool=fullname):
                tool = self.agent.registry.get_tool(fullname)
                self.assertIsNotNone(tool)
                self.assertEqual(tool.param_text(), expected)

    def test_defaults_come_from_params(self):
        """默认值只有一个来源：Param.default（手册文案与执行兜底同源）。"""
        expected = {
            "web:text": {"width": 680},
            "web:md": {"width": 860},
            "web:html": {"width": 900},
            "site:hot": {"limit": 5},
            "site:search": {"limit": 10},
        }
        for fullname, defaults in expected.items():
            with self.subTest(tool=fullname):
                tool = self.agent.registry.get_tool(fullname)
                self.assertEqual(tool.defaults, defaults)

    def test_tool_order_is_stable(self):
        """工具顺序 = 手册里的顺序，改顺序等于改 prompt。"""
        self.assertEqual(
            self.agent.registry.tool_names(),
            [
                "web:search",
                "web:text",
                "web:md",
                "web:html",
                "web:screenshot",
                "site:hot",
                "site:search",
                "site:add",
            ],
        )
        self.assertEqual(
            [p.name for p in self.agent.registry.packages()],
            ["web", "site"],
        )

    def test_manual_contains_params(self):
        manual = self.agent.package_tools_manual("web")
        self.assertIn("## web:search", manual)
        self.assertIn("  query=搜索关键词（必填，精炼，不要包含'帮我搜一下'等客套话）", manual)
        self.assertIn("  width=图片宽度像素（可选，默认 680）", manual)


if __name__ == "__main__":
    unittest.main()
