"""章节切分与内容代理工具。

连载 / 阅读 / 下载策略（对应 PRD 连载处理）：
- 多文件作品：每个文件 = 一章（按 WorkExternalFile.id 升序 = 章节顺序），不拆内容
- 单文件文本（txt/md）：按「第X章/节/回…」或 "Chapter N" 行首自动切章
- 单文件非文本（epub/pdf/媒体…）：整文件 = 一章，不提供预览时仅下载

内容代理原则：zfile 直链的 Content-Type/charset 不可控（浏览器直接打开 txt 会乱码），
站内阅读/下载一律经后端 GET 代理端点拉取后，按扩展名补正确的 Content-Type 与编码再返回。
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

import httpx

# 支持「文内切章」的扩展名（纯文本族）
SPLITTABLE_EXTS = {"txt", "md", "markdown"}

# 行首章节标题：第X章/节/回/卷/集/话/部/篇 或 Chapter N（行需短，避免匹配正文）
_CHAP_RE = re.compile(
    r"^\s{0,2}(?:"
    r"第\s*[0-9一二三四五六七八九十百千两〇]+\s*[章节回卷集话部篇][^\n]{0,40}$"
    r"|[Cc]hapter\s+\d+[^\n]{0,60}$"
    r")",
    re.MULTILINE,
)


def split_text(text: str) -> List[Dict]:
    """把文本按章节标题切分。返回 [{title, start, end}]（字符偏移，不含尾部）。

    找不到 >=2 个章节标题时返回整段作为单章（title=None）。
    """
    matches = list(_CHAP_RE.finditer(text))
    if len(matches) < 2:
        return [{"title": None, "start": 0, "end": len(text)}]
    chapters: List[Dict] = []
    if matches[0].start() > 0 and text[: matches[0].start()].strip():
        chapters.append({"title": "序章", "start": 0, "end": matches[0].start()})
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chapters.append({"title": m.group(0).strip(), "start": m.start(), "end": end})
    return chapters


def decode_bytes(data: bytes) -> str:
    """按常见中文编码依次尝试解码：utf-8 → gb18030（gbk 超集）→ utf-8 容错。"""
    for enc in ("utf-8", "gb18030"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def fetch_file_bytes(url: str, timeout: float = 120.0) -> bytes:
    """从 zfile 直链拉取文件内容（公开无鉴权）。失败抛 httpx.HTTPError。"""
    resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


_IMAGE_MIME = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif",
    "webp": "image/webp", "bmp": "image/bmp", "avif": "image/avif", "svg": "image/svg+xml",
    "tif": "image/tiff", "tiff": "image/tiff", "heic": "image/heic", "ico": "image/x-icon",
}
_VIDEO_MIME = {
    "mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm", "mkv": "video/x-matroska",
    "avi": "video/x-msvideo", "flv": "video/x-flv", "m4v": "video/x-m4v", "wmv": "video/x-ms-wmv",
    "ts": "video/mp2t", "mpg": "video/mpeg", "mpeg": "video/mpeg", "3gp": "video/3gpp",
}
_AUDIO_MIME = {
    "mp3": "audio/mpeg", "m4a": "audio/mp4", "wav": "audio/wav", "flac": "audio/flac",
    "ogg": "audio/ogg", "aac": "audio/aac", "wma": "audio/x-ms-wma", "ape": "audio/x-ape",
    "opus": "audio/opus", "mid": "audio/midi", "midi": "audio/midi", "aiff": "audio/x-aiff",
}
_TEXT_MIME = {
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "markdown": "text/markdown; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "htm": "text/html; charset=utf-8",
    "json": "application/json; charset=utf-8",
    "xml": "application/xml; charset=utf-8",
    "srt": "text/plain; charset=utf-8",
    "vtt": "text/vtt; charset=utf-8",
}


def content_type_for(ext: str) -> Tuple[str, str]:
    """按扩展名返回 (media_type, disposition)。

    disposition: inline = 可站内查看；attachment = 仅下载（浏览器无法原生渲染）。
    """
    ext = (ext or "").lower()
    if ext in _TEXT_MIME:
        return _TEXT_MIME[ext], "inline"
    if ext in _IMAGE_MIME:
        return _IMAGE_MIME[ext], "inline"
    if ext in _VIDEO_MIME:
        return _VIDEO_MIME[ext], "inline"
    if ext in _AUDIO_MIME:
        return _AUDIO_MIME[ext], "inline"
    if ext == "pdf":
        return "application/pdf", "inline"
    return "application/octet-stream", "attachment"


def ext_of(file_name: str | None) -> str:
    """取小写扩展名（不含点）。无扩展名返回空串。"""
    name = file_name or ""
    i = name.rfind(".")
    if i < 0 or i == len(name) - 1:
        return ""
    return name[i + 1 :].lower()


def safe_filename(name: str | None) -> str:
    """清理文件名中的路径/特殊字符，用于 Content-Disposition。"""
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", (name or "").strip())
    return cleaned or "download"
