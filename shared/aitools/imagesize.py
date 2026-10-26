"""图片尺寸探测：HTTP Range 拉文件头 → 解析宽高。

**为什么需要**：QQ 官方 markdown 图片语法要求带尺寸 ``![alt #宽px #高px](url)``。
2026-10 的 M0 实测（M0 文档：.ai/M0-markdown-可行性实测.md）确认：
缺尺寸时手机端**只显示 alt 文本**，电脑端才 fallback 显示图片。
因此凡是往 QQ 发 markdown 嵌图，都要先探测真实宽高。

**怎么做**：用 Range 请求只拉文件头（不下载整图），解析 PNG / JPEG / GIF / WebP 的头。

实现参考 tencent-connect/openclaw-qqbot 的 src/outbound/image-size.ts（2.0.4），
与官方的两处**有意差异**（已记入 .ai/FACTS.md）：

1. Range 取 64KB（bytes=0-65535），官方取 32KB（bytes=0-32767）。
   理由：带大 EXIF/APP 段的 JPEG，头部可能超过 32KB；多拉 32KB 成本可忽略。
2. 额外支持 WebP 的 **VP8X**（扩展格式，画布尺寸在 VP8X 块里）。官方 2.0.4 未覆盖，
   而带 alpha / 画布裁剪的 WebP 常见是 VP8X，官方实现在这类文件上返回 null。

另外，官方在 fetch 前做 SSRF 校验（utils/ssrf-guard.ts，禁内网与云元数据地址）。
本实现的调用方只传**自己生成的**图片地址（渲染产物），暂不引入 IP 段黑名单，
以免误伤内网自建渲染服务；若将来允许用户指定 URL，必须补上该防护
（已记入 .ai/RISKS.md）。
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional
from urllib.parse import urlsplit

if TYPE_CHECKING:  # pragma: no cover - 仅类型引用
    import httpx

# Range 拉取长度：前 64KB（官方实现是 32KB，见模块 docstring 的差异说明）
RANGE_BYTES = 65535
# 探测超时（官方默认 5000ms）
DEFAULT_TIMEOUT = 5.0


@dataclass(frozen=True)
class ImageSize:
    """图片宽高（像素）。"""

    width: int
    height: int


# ------------------ 头部解析 ------------------

def _parse_png(data: bytes) -> Optional[ImageSize]:
    # IHDR 块：前 8 字节签名 + 4 长度 + 4 类型 → 宽在 offset 16，高在 offset 20（均为 BE uint32）
    if len(data) < 24:
        return None
    width, height = struct.unpack(">I", data[16:20])[0], struct.unpack(">I", data[20:24])[0]
    return ImageSize(width=width, height=height)


def _parse_jpeg(data: bytes) -> Optional[ImageSize]:
    # 逐段扫描，找 SOF0..SOF15（排除 C4/DHT、C8/JPG、CC/DAC）
    offset = 2
    end = len(data)
    while offset < end - 9:
        if data[offset] != 0xFF:
            offset += 1
            continue
        marker = data[offset + 1]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height = struct.unpack(">H", data[offset + 5:offset + 7])[0]
            width = struct.unpack(">H", data[offset + 7:offset + 9])[0]
            return ImageSize(width=width, height=height)
        seg_len = struct.unpack(">H", data[offset + 2:offset + 4])[0]
        if seg_len < 2:
            return None
        offset += 2 + seg_len
    return None


def _parse_gif(data: bytes) -> Optional[ImageSize]:
    # 宽在 offset 6、高在 offset 8（均为 LE uint16）
    if len(data) < 10:
        return None
    width = struct.unpack("<H", data[6:8])[0]
    height = struct.unpack("<H", data[8:10])[0]
    return ImageSize(width=width, height=height)


def _parse_webp(data: bytes) -> Optional[ImageSize]:
    chunk = data[12:16]
    if chunk == b"VP8 " and len(data) >= 30:
        # 有损 VP8：宽在 offset 26、高在 offset 28（LE uint16，各取低 14 位）
        width = struct.unpack("<H", data[26:28])[0] & 0x3FFF
        height = struct.unpack("<H", data[28:30])[0] & 0x3FFF
        return ImageSize(width=width, height=height)
    if chunk == b"VP8L" and len(data) >= 25:
        # 无损 VP8L：offset 21 起 4 字节，低 14 位 = 宽-1，次 14 位 = 高-1
        bits = struct.unpack("<I", data[21:25])[0]
        return ImageSize(width=(bits & 0x3FFF) + 1, height=((bits >> 14) & 0x3FFF) + 1)
    if chunk == b"VP8X" and len(data) >= 30:
        # 扩展格式 VP8X（官方未覆盖）：画布宽高各 3 字节 LE，存的是 值-1
        width = data[24] | (data[25] << 8) | (data[26] << 16)
        height = data[27] | (data[28] << 8) | (data[29] << 16)
        return ImageSize(width=width + 1, height=height + 1)
    return None


def parse_image_size(data: bytes) -> Optional[ImageSize]:
    """从图片文件头字节解析宽高；识别不了或数据不足返回 None。

    纯函数，不触网，便于单测。
    """
    try:
        if not data or len(data) < 8:
            return None
        if data[:4] == b"\x89PNG":
            return _parse_png(data)
        if data[:2] == b"\xff\xd8":
            return _parse_jpeg(data)
        if data[:3] == b"GIF":
            return _parse_gif(data)
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return _parse_webp(data)
        return None
    except Exception:  # noqa: BLE001 - 任何解析异常都降级为 None，绝不抛给调用方
        return None


# ------------------ 网络探测 ------------------

async def probe_image_size(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    client: Optional["httpx.AsyncClient"] = None,
) -> Optional[ImageSize]:
    """探测远程图片的宽高；失败一律返回 None（不抛异常）。

    只用 Range 请求拉文件头。服务端不支持 Range（返回 200 + 全量）时也能解析，
    只是多下载了些字节。
    """
    try:
        import httpx  # 延迟导入：没装 httpx 时本模块仍可导入（仅本函数不可用）

        scheme = urlsplit(url).scheme.lower()
        if scheme not in ("http", "https"):
            return None
        headers = {"Range": f"bytes=0-{RANGE_BYTES}"}
        if client is not None:
            resp = await client.get(url, headers=headers, timeout=timeout)
        else:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
                resp = await c.get(url, headers=headers)
        if not (200 <= resp.status_code < 300):
            return None
        return parse_image_size(resp.content)
    except Exception:  # noqa: BLE001 - 探测失败不是错误，降级为 None
        return None


# ------------------ markdown 尺寸提示 ------------------

def format_markdown_image(alt: str, url: str, size: Optional[ImageSize] = None) -> str:
    """生成带尺寸提示的 QQ markdown 图片语法。

    官方文档与 M0 实测都用 ``![alt #宽px #高px](url)`` 这种写法
    （bot.q.qq.com/wiki/develop/api-v2/server-inter/message/type/markdown.html）。
    注意与 openclaw 插件的 ``#宽x高`` 写法不同——以官方文档为准。

    探测不到尺寸时退化为无尺寸写法（电脑端仍可显示，手机端只显示 alt）。
    """
    if size is None:
        return f"![{alt}]({url})"
    return f"![{alt} #{size.width}px #{size.height}px]({url})"
