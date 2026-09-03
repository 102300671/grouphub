"""zfile API 客户端：封装登录、文件上传、文件列表等操作。

目录结构：
/works/{type}/{uploader_qq}/{work_id}_{title}/{filename}
/covers/{work_id}.jpg
/avatars/{user_qq}.jpg
/uploads/{category}/{uploader_qq}/{filename}

使用 httpx 同步客户端（FastAPI 端点均为 def 而非 async def，避免阻塞事件循环的复杂度）。
Token 自动缓存，401 时自动重新登录。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


# ---------- 上传校验（PRD §10：类型 + 大小校验） ----------

# 图片：常见格式 + 矢量
IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "avif", "tif", "tiff", "ico", "heic"}
# 视频：主流容器 + 小众格式
VIDEO_EXTS = {"mp4", "mov", "webm", "mkv", "avi", "flv", "m4v", "wmv", "ts", "mpg", "mpeg", "3gp"}
# 音频：PCM + 有损/无损
AUDIO_EXTS = {"mp3", "m4a", "wav", "flac", "ogg", "aac", "wma", "ape", "opus", "midi", "mid", "aiff"}
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS

# 文档类：电子书 + 办公 + 代码/文本 + 压缩包
EBOOK_EXTS = {"epub", "mobi", "azw3", "azw", "kfx", "fb2", "ibooks", "lrf", "pdb", "rtf"}
OFFICE_EXTS = {"doc", "docx", "xls", "xlsx", "ppt", "pptx", "pdf", "odt", "ods", "odp", "csv", "rtf"}
TEXT_EXTS = {"txt", "md", "markdown", "json", "xml", "yaml", "yml", "ini", "conf", "log", "srt", "vtt", "ass", "sub", "lrc", "html", "htm", "css", "js", "ts"}
ARCHIVE_EXTS = {"zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", "zst", "cbz", "cbr", "iso"}
DOC_EXTS = EBOOK_EXTS | OFFICE_EXTS | TEXT_EXTS | ARCHIVE_EXTS

# 各业务分类的扩展名白名单
WORK_FILE_EXTS = MEDIA_EXTS | DOC_EXTS  # 作品库：全类型
GENERAL_UPLOAD_EXTS = MEDIA_EXTS | DOC_EXTS  # /uploads/*：通用上传（同人/头像等）
ATTACHMENT_EXTS = MEDIA_EXTS | OFFICE_EXTS | {"txt", "md", "epub", "mobi", "azw3", "srt", "vtt", "csv"}  # 书评/论坛附件：拒绝压缩包与源码，避免乱塞

# 封面仅允许图片类型（缩略展示）
COVER_EXTS = IMAGE_EXTS - {"svg"}  # svg 作为封面可能被误用恶意脚本，限制为位图


class UploadGuardError(Exception):
    """上传文件未通过大小/类型校验。status 为建议的 HTTP 状态码。"""

    def __init__(self, msg: str, status: int = 400):
        self.msg = msg
        self.status = status
        super().__init__(msg)


def validate_upload(
    file_name: str,
    content: bytes,
    allowed_exts: Iterable[str],
    max_bytes: int,
) -> None:
    """校验上传内容非空、大小不超限、扩展名在白名单内。不通过抛 UploadGuardError。"""
    if not content:
        raise UploadGuardError("文件内容为空", 400)
    if len(content) > max_bytes:
        raise UploadGuardError(f"文件过大（{len(content) / 1024 / 1024:.1f}MB），上限 {max_bytes // 1024 // 1024}MB", 413)
    ext = Path(file_name or "").suffix.lower().lstrip(".")
    if not ext:
        raise UploadGuardError("文件缺少扩展名，无法判断类型", 400)
    if ext not in allowed_exts:
        raise UploadGuardError(
            f"不支持的文件类型 .{ext}，允许：{', '.join(sorted(allowed_exts))}", 400
        )


# ---------- 路径构建 ----------

def _safe_name(name: str) -> str:
    """清理文件名/目录名中的不安全字符。"""
    import re
    return re.sub(r'[/\\:*?"<>|\s]+', '_', name).strip('_') or "unnamed"


class ZFilePath:
    """zfile 目录路径构建器。统一所有上传路径的拼接逻辑。"""

    @staticmethod
    def work_file_dir(work_type: str, uploader_qq: str, work_id: int, title: str) -> str:
        """作品文件目录：/works/{type}/{uploader_qq}/{work_id}_{title}"""
        title_safe = _safe_name(title)[:50]
        return f"/works/{work_type}/{uploader_qq}/{work_id}_{title_safe}"

    @staticmethod
    def work_file(work_type: str, uploader_qq: str, work_id: int, title: str, filename: str) -> str:
        """作品文件完整路径（含文件名）。"""
        return f"{ZFilePath.work_file_dir(work_type, uploader_qq, work_id, title)}/{_safe_name(filename)}"

    @staticmethod
    def cover(work_id: int, ext: str = ".jpg") -> str:
        """作品封面：/covers/{work_id}{ext}"""
        return f"/covers/{work_id}{ext}"

    @staticmethod
    def avatar(user_qq: str, ext: str = ".jpg") -> str:
        """用户头像：/avatars/{user_qq}{ext}"""
        return f"/avatars/{user_qq}{ext}"

    @staticmethod
    def general_dir(category: str, uploader_qq: str) -> str:
        """通用上传目录：/uploads/{category}/{uploader_qq}"""
        return f"/uploads/{category}/{uploader_qq}"

    @staticmethod
    def general(category: str, uploader_qq: str, filename: str) -> str:
        """通用上传完整路径。"""
        return f"{ZFilePath.general_dir(category, uploader_qq)}/{_safe_name(filename)}"


class ZFileError(Exception):
    """zfile API 调用失败。"""

    def __init__(self, msg: str, code: str = "", status: int = 0):
        self.msg = msg
        self.code = code
        self.status = status
        super().__init__(msg)


class ZFileClient:
    """zfile REST API 客户端（单例使用）。"""

    def __init__(self):
        self._settings = get_settings()
        self._token: Optional[str] = None
        self._http: Optional[httpx.Client] = None

    @property
    def base_url(self) -> str:
        return self._settings.zfile_base_url.rstrip("/")

    @property
    def storage_key(self) -> str:
        return self._settings.zfile_storage_key

    def _client(self) -> httpx.Client:
        if self._http is None or self._http.is_closed:
            self._http = httpx.Client(timeout=60.0)
        return self._http

    def _headers(self) -> Dict[str, str]:
        h: Dict[str, str] = {}
        if self._token:
            h["zfile-token"] = self._token
        return h

    # ---------- 认证 ----------

    def login(self) -> str:
        """登录 zfile 获取 token。"""
        resp = self._client().post(
            f"{self.base_url}/user/login",
            json={
                "username": self._settings.zfile_username,
                "password": self._settings.zfile_password,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        if str(data.get("code")) != "0":
            raise ZFileError(f"zfile 登录失败: {data.get('msg')}", code=str(data.get("code")))
        self._token = data["data"]["token"]
        logger.info("[zfile] 登录成功")
        return self._token

    def _ensure_token(self) -> str:
        """确保 token 有效；无效则重新登录。"""
        if self._token is None:
            return self.login()
        return self._token

    def _request(self, method: str, path: str, **kwargs) -> Any:
        """带 token 的请求；401 时自动 re-login 并重试一次。"""
        self._ensure_token()
        resp = self._client().request(
            method,
            f"{self.base_url}{path}",
            headers=self._headers(),
            **kwargs,
        )
        if resp.status_code == 401:
            logger.info("[zfile] token 过期，重新登录...")
            self._token = None
            self._ensure_token()
            resp = self._client().request(
                method,
                f"{self.base_url}{path}",
                headers=self._headers(),
                **kwargs,
            )
        resp.raise_for_status()
        data = resp.json()
        if str(data.get("code")) != "0":
            raise ZFileError(
                f"zfile 接口 {path} 返回错误: {data.get('msg')}",
                code=str(data.get("code")),
                status=resp.status_code,
            )
        return data.get("data")

    # ---------- 文件操作 ----------

    def list_files(self, path: str = "/") -> List[Dict[str, Any]]:
        """列出指定路径下的文件/文件夹。返回 files 数组。"""
        data = self._request(
            "POST",
            "/api/storage/files",
            json={
                "storageKey": self.storage_key,
                "path": path,
            },
        )
        return data.get("files", []) if data else []

    def get_upload_url(self, file_name: str, path: str = "/", size: int = 0) -> str:
        """申请上传 URL。"""
        data = self._request(
            "POST",
            "/api/file/operator/upload/file",
            json={
                "storageKey": self.storage_key,
                "path": path,
                "name": file_name,
                "size": size,
            },
        )
        # data 是字符串形式的上传 URL
        if not isinstance(data, str):
            raise ZFileError(f"获取上传 URL 失败，返回: {data}")
        return data

    def upload_file(self, file_name: str, file_content: bytes, path: str = "/") -> str:
        """上传文件到 zfile。返回文件直链 URL。

        流程：
        1. 调 /api/file/operator/upload/file 获取上传 URL
        2. PUT 文件内容到该 URL
        3. 在文件列表中查找该文件获取直链
        """
        upload_url = self.get_upload_url(file_name, path, len(file_content))

        # PUT 上传文件内容
        put_resp = self._client().put(
            upload_url,
            content=file_content,
            headers={**self._headers(), "Content-Type": "application/octet-stream"},
        )
        put_resp.raise_for_status()
        put_data = put_resp.json() if put_resp.content else {}
        if str(put_data.get("code", "0")) != "0":
            raise ZFileError(f"zfile 上传失败: {put_data.get('msg')}")

        # 获取上传后的直链：list_files 找文件名
        files = self.list_files(path)
        for f in files:
            if f.get("name") == file_name and f.get("type") == "FILE":
                return f.get("url", "")
        # 兜底：拼路径
        return f"{self.base_url}/file/{self.storage_key}{path.rstrip('/')}/{file_name}"

    def create_folder(self, path: str) -> None:
        """创建文件夹（如果不存在）。path 形如 /works/123"""
        # zfile 没有单独的创建文件夹接口；上传时会自动创建。
        # 这里预留，MVP 暂不做。
        pass

    def close(self):
        if self._http and not self._http.is_closed:
            self._http.close()


# 全局单例
_client: Optional[ZFileClient] = None


def public_url(url: str | None) -> str | None:
    """把 DB 里存的 zfile 绝对直链改写为同源相对路径（默认 /zfile/...）。

    前端（vite dev / 生产代理）再把 /zfile/* 转发到 zfile_base_url，
    浏览器无需直连 zfile 端口，内网穿透只暴露前端端口即可。
    """
    if not url:
        return url
    s = get_settings()
    prefix = s.zfile_public_prefix
    if not prefix:
        return url
    base = s.zfile_base_url.rstrip("/")
    if url.startswith(base + "/"):
        return f"{prefix}{url[len(base):]}"
    return url


def get_zfile() -> ZFileClient:
    global _client
    if _client is None:
        _client = ZFileClient()
    return _client
