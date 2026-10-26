"""图片尺寸探测：四种格式的头部解析 + Range 失败降级。

解析规则对齐 tencent-connect/openclaw-qqbot 的 src/outbound/image-size.ts（2.0.4），
差异见 shared/aitools/imagesize.py 的模块 docstring。
"""
from __future__ import annotations

import asyncio
import os
import struct
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from shared.aitools.imagesize import (  # noqa: E402
    RANGE_BYTES,
    format_markdown_image,
    parse_image_size,
    probe_image_size,
)

try:  # httpx 只在有网环境（设备/云端）装了；没装时跳过联网相关用例
    import httpx

    HAS_HTTPX = True
except ImportError:  # pragma: no cover
    httpx = None
    HAS_HTTPX = False


def make_png(width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + struct.pack(">I", width)
        + struct.pack(">I", height)
        + b"\x08\x06\x00\x00\x00"
    )


def make_jpeg(width: int, height: int) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"\x00" * 14
    sof = (
        b"\xff\xc0"
        + struct.pack(">H", 17)
        + b"\x08"
        + struct.pack(">H", height)
        + struct.pack(">H", width)
        + b"\x03\x01\x11\x00\x02\x11\x01\x03\x11\x01"
    )
    return b"\xff\xd8" + app0 + sof + b"\xff\xd9"


def make_gif(width: int, height: int) -> bytes:
    return (
        b"GIF89a"
        + struct.pack("<H", width)
        + struct.pack("<H", height)
        + b"\xf7\x00\x00"
    )


def make_webp_vp8(width: int, height: int) -> bytes:
    return (
        b"RIFF"
        + struct.pack("<I", 30)
        + b"WEBPVP8 "
        + struct.pack("<I", 10)
        + b"\x00\x00\x00"
        + b"\x9d\x01\x2a"
        + struct.pack("<H", width & 0x3FFF)
        + struct.pack("<H", height & 0x3FFF)
    )


def make_webp_vp8l(width: int, height: int) -> bytes:
    bits = (width - 1) | ((height - 1) << 14)
    return (
        b"RIFF"
        + struct.pack("<I", 25)
        + b"WEBPVP8L"
        + struct.pack("<I", 5)
        + b"\x2f"
        + struct.pack("<I", bits)
    )


def make_webp_vp8x(width: int, height: int) -> bytes:
    return (
        b"RIFF"
        + struct.pack("<I", 30)
        + b"WEBPVP8X"
        + struct.pack("<I", 10)
        + b"\x10\x00\x00\x00"
        + struct.pack("<I", width - 1)[:3]
        + struct.pack("<I", height - 1)[:3]
    )


class TestParseImageSize(unittest.TestCase):
    def test_png(self):
        size = parse_image_size(make_png(800, 600))
        self.assertEqual((size.width, size.height), (800, 600))

    def test_jpeg(self):
        size = parse_image_size(make_jpeg(1920, 1080))
        self.assertEqual(size.width, 1920)
        self.assertEqual(size.height, 1080)

    def test_gif(self):
        size = parse_image_size(make_gif(320, 240))
        self.assertEqual(size.width, 320)
        self.assertEqual(size.height, 240)

    def test_webp_vp8(self):
        size = parse_image_size(make_webp_vp8(640, 480))
        self.assertEqual(size.width, 640)
        self.assertEqual(size.height, 480)

    def test_webp_vp8l(self):
        size = parse_image_size(make_webp_vp8l(100, 50))
        self.assertEqual(size.width, 100)
        self.assertEqual(size.height, 50)

    def test_webp_vp8x(self):
        """VP8X（官方未覆盖）：画布尺寸在 VP8X 块里。"""
        size = parse_image_size(make_webp_vp8x(1200, 800))
        self.assertEqual(size.width, 1200)
        self.assertEqual(size.height, 800)

    def test_unknown_format_returns_none(self):
        self.assertIsNone(parse_image_size(b"not an image at all"))
        self.assertIsNone(parse_image_size(b""))
        self.assertIsNone(parse_image_size(b"\x89PNG"))

    def test_truncated_headers_return_none(self):
        self.assertIsNone(parse_image_size(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8))
        self.assertIsNone(parse_image_size(b"GIF89a" + b"\x01\x02"))

    def test_never_raises(self):
        """任何脏数据都不能抛异常——探测失败只返回 None。"""
        for junk in (b"\xff\xd8\xff\xe0\x00", b"RIFF\x00\x00\x00\x00WEBP", b"\x00" * 3):
            self.assertIsNone(parse_image_size(junk))


class TestFormatMarkdownImage(unittest.TestCase):
    def test_with_size(self):
        from shared.aitools.imagesize import ImageSize

        self.assertEqual(
            format_markdown_image("图", "https://x/a.png", ImageSize(800, 600)),
            "![图 #800px #600px](https://x/a.png)",
        )

    def test_without_size(self):
        self.assertEqual(
            format_markdown_image("图", "https://x/a.png", None),
            "![图](https://x/a.png)",
        )


@unittest.skipUnless(HAS_HTTPX, "需要 httpx")
class TestProbeImageSize(unittest.TestCase):
    def _client(self, handler):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def test_probe_png(self):
        def handler(request):
            self.assertEqual(request.headers.get("Range"), f"bytes=0-{RANGE_BYTES}")
            return httpx.Response(200, content=make_png(1024, 768))

        async def run():
            async with self._client(handler) as client:
                return await probe_image_size("https://example.com/a.png", client=client)

        size = asyncio.run(run())
        self.assertIsNotNone(size)
        self.assertEqual((size.width, size.height), (1024, 768))

    def test_probe_accepts_206(self):
        def handler(request):
            return httpx.Response(206, content=make_png(10, 20))

        async def run():
            async with self._client(handler) as client:
                return await probe_image_size("https://example.com/a.png", client=client)

        size = asyncio.run(run())
        self.assertEqual((size.width, size.height), (10, 20))

    def test_http_error_returns_none(self):
        def handler(request):
            return httpx.Response(404, content=b"nope")

        async def run():
            async with self._client(handler) as client:
                return await probe_image_size("https://example.com/a.png", client=client)

        self.assertIsNone(asyncio.run(run()))

    def test_network_error_returns_none(self):
        def handler(request):
            raise httpx.ConnectError("连不上")

        async def run():
            async with self._client(handler) as client:
                return await probe_image_size("https://example.com/a.png", client=client)

        self.assertIsNone(asyncio.run(run()))

    def test_non_image_returns_none(self):
        def handler(request):
            return httpx.Response(200, content=b"<html>404</html>")

        async def run():
            async with self._client(handler) as client:
                return await probe_image_size("https://example.com/a.png", client=client)

        self.assertIsNone(asyncio.run(run()))

    def test_bad_scheme_returns_none(self):
        self.assertIsNone(asyncio.run(probe_image_size("file:///etc/passwd")))
        self.assertIsNone(asyncio.run(probe_image_size("not-a-url")))


if __name__ == "__main__":
    unittest.main()
