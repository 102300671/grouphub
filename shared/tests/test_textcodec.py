"""文本协议编解码回归：与 M1 之前的实现逐字比对。

黄金用例由 tools/codec 生成流程产出（fixtures/codec_golden.json）：
旧实现（qqbot/plugins/_lib/aitools.py）与新实现（shared/aitools/protocol/textcodec.py）
对同一批模型输出跑一遍，逐字比对后冻结。
"""
from __future__ import annotations

import json
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from shared.aitools.protocol.textcodec import (  # noqa: E402
    CLOSE_TAG,
    OPEN_TAG,
    RESULT_OPEN,
    parse_tool_calls,
    strip_tool_calls,
)

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "codec_golden.json")


class TestProtocolTags(unittest.TestCase):
    def test_tags_are_plain_ascii(self):
        """协议标签必须是纯 ASCII：混入零宽字符会让模型输出的块解析不到。"""
        for tag in (OPEN_TAG, CLOSE_TAG, RESULT_OPEN):
            self.assertTrue(tag.isascii(), f"{tag!r} 含非 ASCII 字符")
        self.assertEqual(OPEN_TAG, "<" + "tool_call" + ">")
        self.assertEqual(CLOSE_TAG, "</" + "tool_call" + ">")
        self.assertEqual(RESULT_OPEN, "<tool_result")


class TestCodecGolden(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(FIXTURE, encoding="utf-8") as fh:
            cls.cases = json.load(fh)

    def test_golden_file_is_not_empty(self):
        self.assertGreaterEqual(len(self.cases), 20)

    def test_parse_matches_golden(self):
        for case in self.cases:
            with self.subTest(text=case["text"][:40]):
                calls = [
                    {"name": c.name, "args": c.args, "raw": c.raw}
                    for c in parse_tool_calls(case["text"])
                ]
                self.assertEqual(calls, case["calls"])

    def test_strip_matches_golden(self):
        for case in self.cases:
            with self.subTest(text=case["text"][:40]):
                self.assertEqual(strip_tool_calls(case["text"]), case["stripped"])

    def test_golden_recorded_old_and_new_agree(self):
        """黄金文件本身记录了「旧实现 == 新实现」，防止有人手改期望值糊弄过去。"""
        for case in self.cases:
            with self.subTest(text=case["text"][:40]):
                self.assertTrue(case["same"], "该用例的旧新实现曾不一致")
                self.assertEqual(case["calls"], case["legacy_calls"])
                self.assertEqual(case["stripped"], case["legacy_stripped"])


if __name__ == "__main__":
    unittest.main()
