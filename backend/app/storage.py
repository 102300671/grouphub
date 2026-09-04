"""统一文件存储管理：支持 zfile 和 alist，主/回退自动切换。

架构说明：
- 主引擎（primary）：优先使用，由 STORAGE_PRIMARY 配置决定
- 回退引擎（fallback）：主引擎不可用时自动切换
- 上传：主引擎 → 回退引擎（自动切换）
- 读取：DB 里存的 URL 已指向具体引擎，通过 public_url() 改写为前端可访问的相对路径
- 列文件：主引擎 → 回退引擎

路径约定：
- 业务代码传入的路径始终是"相对路径"（如 /covers/1.jpg/cover.jpg）
- 各引擎内部负责将相对路径映射为引擎特有的完整路径：
  - zfile: 直接作为 storage 内路径（zfile 内部按 storageKey 隔离）
  - alist: 根据 ALIST_PATH_PREFIX 决定是否加前缀
    - 本地存储：prefix=/grouphub → 完整路径 /grouphub/covers/1.jpg/cover.jpg
    - 云盘(139Yun)：prefix=空 → 完整路径 /covers/1.jpg/cover.jpg

URL 格式（存入 DB 的绝对 URL）：
- zfile: http://localhost:8081/api/file/redirect/grouphub/...?sign=...
- alist: http://localhost:5244/p/grouphub_139/...?sign=...

前端访问（public_url 改写后）：
- /zfile/api/file/redirect/grouphub/...?sign=...  → Vite proxy → zfile:8081
- /alist/p/grouphub_139/...?sign=...              → Vite proxy → alist:5244
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.alist_client import AlistClient, AlistError, get_alist
from app.config import get_settings
from app.zfile_client import ZFileClient, ZFileError, get_zfile

logger = logging.getLogger(__name__)


class StorageError(Exception):
    """所有存储引擎均不可用。"""

    def __init__(self, msg: str):
        self.msg = msg
        super().__init__(msg)


class StorageClient:
    """统一存储接口：主引擎优先，失败自动回退到备引擎。

    业务代码用法：
        storage = get_storage()
        url = storage.upload_file("cover.jpg", img_bytes, path="/covers/1.jpg")
        files = storage.list_files(path="/covers")
    """

    def __init__(self):
        self._settings = get_settings()
        primary = self._settings.storage_primary
        if primary == "alist":
            self._primary_name = "alist"
            self._primary: Any = get_alist()
            self._fallback_name = "zfile"
            self._fallback: Any = get_zfile()
        else:
            self._primary_name = "zfile"
            self._primary = get_zfile()
            self._fallback_name = "alist"
            self._fallback = get_alist()
        logger.info(f"[storage] 主引擎={self._primary_name}, 回退={self._fallback_name}")

    # ---------- 属性 ----------

    @property
    def primary_name(self) -> str:
        """主引擎名称。"""
        return self._primary_name

    @property
    def fallback_name(self) -> str:
        """回退引擎名称。"""
        return self._fallback_name

    def engines(self) -> Dict[str, Any]:
        """返回所有引擎实例（调试用）。"""
        return {self._primary_name: self._primary, self._fallback_name: self._fallback}

    # ---------- 内部执行器 ----------

    def _execute(self, operation: str, **kwargs) -> Any:
        """执行操作，主失败则回退。"""
        try:
            return getattr(self._primary, operation)(**kwargs)
        except Exception as exc:
            logger.warning(f"[storage] {self._primary_name}.{operation} 失败，回退 {self._fallback_name}：{exc}")
            try:
                return getattr(self._fallback, operation)(**kwargs)
            except Exception as exc2:
                raise StorageError(
                    f"{operation} 在两个引擎上均失败：\n"
                    f"  {self._primary_name}: {exc}\n"
                    f"  {self._fallback_name}: {exc2}"
                )

    # ---------- 文件操作 ----------

    def upload_file(self, file_name: str, file_content: bytes, path: str = "/") -> str:
        """上传文件，返回直链 URL。

        主引擎优先，失败自动回退。返回的 URL 指向实际存储该文件的引擎。
        """
        return self._execute("upload_file", file_name=file_name, file_content=file_content, path=path)

    def list_files(self, path: str = "/") -> List[Dict[str, Any]]:
        """列出指定路径下的文件。

        返回格式（统一）：
        [{
            "name": str,       # 文件名/目录名
            "size": int,       # 文件大小（字节），目录为 0
            "type": str,       # "FILE" | "DIR"
            "path": str,       # 完整路径
            "url": str,        # 直链 URL（FILE 有值，DIR 为空）
        }]
        """
        return self._execute("list_files", path=path)

    def get_file_url(self, rel_path: str) -> str:
        """根据相对路径获取文件直链。

        rel_path 形如 "/covers/1.jpg/cover.jpg"。
        先尝试主引擎，再尝试回退引擎。
        """
        # 构建引擎完整路径
        if self._primary_name == "alist":
            full = self._primary._full_path(rel_path)
            try:
                return self._primary.get_file_url(full)
            except Exception:
                pass
        else:
            try:
                return self._primary.get_file_url(rel_path)
            except Exception:
                pass

        # 回退
        if self._fallback_name == "alist":
            full = self._fallback._full_path(rel_path)
            return self._fallback.get_file_url(full)
        else:
            return self._fallback.get_file_url(rel_path)

    def create_folder(self, path: str) -> None:
        """创建文件夹（如果不存在）。"""
        return self._execute("create_folder", path=path)

    def file_exists(self, rel_path: str) -> bool:
        """检查文件是否存在（先查主引擎，再查回退引擎）。"""
        for engine_name, engine in [(self._primary_name, self._primary), (self._fallback_name, self._fallback)]:
            try:
                if engine_name == "alist":
                    full = engine._full_path(rel_path)
                    data = engine._request("POST", "/api/fs/get", json={"path": full})
                    return data is not None and not data.get("is_dir")
                else:
                    # zfile: 从 list_files 查找
                    parent = rel_path.rsplit("/", 1)[0] or "/"
                    name = rel_path.rsplit("/", 1)[-1]
                    files = engine.list_files(parent)
                    return any(f.get("name") == name and f.get("type") == "FILE" for f in files)
            except Exception:
                continue
        return False

    def close(self):
        """关闭所有引擎的 HTTP 连接。"""
        try:
            self._primary.close()
        except Exception:
            pass
        try:
            self._fallback.close()
        except Exception:
            pass


# ---------- public_url：兼容两种 URL 格式 ----------

def public_url(url: str | None) -> str | None:
    """把 DB 里存的绝对直链改写为同源相对路径。

    支持两种格式：
    - zfile: http://localhost:8081/... → /zfile/...
    - alist: http://localhost:5244/... → /alist/...
    - 外部链接（如 QQ 头像 qlogo.cn）：原样返回
    """
    if not url:
        return url
    s = get_settings()

    # zfile
    zfile_base = s.zfile_base_url.rstrip("/")
    zfile_prefix = s.zfile_public_prefix
    if zfile_prefix and url.startswith(zfile_base + "/"):
        return f"{zfile_prefix}{url[len(zfile_base):]}"

    # alist
    alist_base = s.alist_base_url.rstrip("/")
    alist_prefix = s.alist_public_prefix
    if alist_prefix and url.startswith(alist_base + "/"):
        return f"{alist_prefix}{url[len(alist_base):]}"

    # 外部 URL 原样返回
    return url


# ---------- 全局单例 ----------

_storage: Optional[StorageClient] = None


def get_storage() -> StorageClient:
    global _storage
    if _storage is None:
        _storage = StorageClient()
    return _storage
