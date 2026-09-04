"""alist API 客户端：封装登录、文件上传、文件列表等操作。

接口与 ZFileClient 对齐（login / list_files / upload_file / create_folder），
供 storage.py 统一管理做主/回退切换。

alist 直链格式：{base_url}/p/{virtual_path}?sign={sign}
sign 基于文件 hash，永不过期。
"""
from __future__ import annotations

import logging
import urllib.parse
from typing import Any, Dict, List, Optional

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class AlistError(Exception):
    """alist API 调用失败。"""

    def __init__(self, msg: str, code: int = 0):
        self.msg = msg
        self.code = code
        super().__init__(msg)


class AlistClient:
    """alist REST API 客户端（单例使用）。"""

    def __init__(self):
        self._settings = get_settings()
        self._token: Optional[str] = None
        self._http: Optional[httpx.Client] = None

    @property
    def base_url(self) -> str:
        return self._settings.alist_base_url.rstrip("/")

    @property
    def path_prefix(self) -> str:
        """路径前缀（本地存储需要存储源名，云盘留空）。"""
        return self._settings.alist_path_prefix.rstrip("/")

    def _full_path(self, path: str) -> str:
        """将相对路径转换为 alist 完整路径。"""
        p = path.lstrip("/")
        prefix = self.path_prefix
        if prefix:
            return f"{prefix}/{p}"
        return f"/{p}"

    def _client(self) -> httpx.Client:
        if self._http is None or self._http.is_closed:
            self._http = httpx.Client(timeout=60.0)
        return self._http

    def _headers(self) -> Dict[str, str]:
        h: Dict[str, str] = {}
        if self._token:
            h["Authorization"] = self._token
        return h

    # ---------- 认证 ----------

    def login(self) -> str:
        """登录 alist 获取 token。"""
        resp = self._client().post(
            f"{self.base_url}/api/auth/login",
            json={
                "username": self._settings.alist_username,
                "password": self._settings.alist_password,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 200:
            raise AlistError(f"alist 登录失败: {data.get('message')}", code=data.get("code", 0))
        self._token = data["data"]["token"]
        logger.info("[alist] 登录成功")
        return self._token

    def _ensure_token(self) -> str:
        if self._token is None:
            return self.login()
        return self._token

    def _request(self, method: str, path: str, **kwargs) -> Any:
        """带 token 的请求；token 失效时自动 re-login 并重试一次。"""
        self._ensure_token()
        resp = self._client().request(
            method,
            f"{self.base_url}{path}",
            headers=self._headers(),
            **kwargs,
        )
        if resp.status_code in (401, 403):
            # 检查是否是 token 过期
            try:
                body = resp.json()
                if body.get("code") in (10401, 10403):
                    logger.info("[alist] token 过期，重新登录...")
                    self._token = None
                    self._ensure_token()
                    resp = self._client().request(
                        method,
                        f"{self.base_url}{path}",
                        headers=self._headers(),
                        **kwargs,
                    )
            except Exception:
                pass
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 200:
            raise AlistError(
                f"alist 接口 {path} 返回错误: {data.get('message')}",
                code=data.get("code", 0),
            )
        return data.get("data")

    # ---------- 文件操作 ----------

    def list_files(self, path: str = "/") -> List[Dict[str, Any]]:
        """列出指定路径下的文件/文件夹。

        path 是相对路径（如 /covers），内部自动转换为引擎完整路径。
        返回格式与 zfile 的 list_files 对齐：[{name, size, type, path, url}]
        """
        full_path = self._full_path(path) if path != "/" else (
            f"{self.path_prefix}" if self.path_prefix else "/"
        )
        data = self._request(
            "POST",
            "/api/fs/list",
            json={"path": full_path},
        )
        contents = data.get("contents", []) if data else []
        result: List[Dict[str, Any]] = []
        for item in contents:
            result.append({
                "name": item.get("name", ""),
                "size": item.get("size", 0),
                "type": "DIR" if item.get("is_dir") else "FILE",
                "path": item.get("path", ""),
                "url": "",  # alist 的直链需要额外请求 get_file_url
            })
        return result

    def get_file_url(self, path: str) -> str:
        """获取单个文件的直链 URL（raw_url）。"""
        data = self._request(
            "POST",
            "/api/fs/get",
            json={"path": path},
        )
        if not data:
            raise AlistError(f"alist 获取文件信息失败: {path}")
        return data.get("raw_url", "")

    def upload_file(self, file_name: str, file_content: bytes, path: str = "/") -> str:
        """上传文件到 alist。返回文件直链 URL。

        流程：
        1. PUT /api/fs/put 上传文件
        2. POST /api/fs/get 获取直链（raw_url），云盘有同步延迟，带重试
        """
        import time

        self._ensure_token()
        full_path = self._full_path(f"{path.rstrip('/')}/{file_name}")
        encoded_path = urllib.parse.quote(full_path, safe="/")

        put_resp = self._client().put(
            f"{self.base_url}/api/fs/put",
            headers={
                **self._headers(),
                "File-Path": encoded_path,
                "Content-Type": "application/octet-stream",
            },
            content=file_content,
        )
        put_resp.raise_for_status()
        put_data = put_resp.json()
        if put_data.get("code") != 200:
            raise AlistError(f"alist 上传失败: {put_data.get('message')}", code=put_data.get("code", 0))

        # 获取直链（云盘有同步延迟，最多重试 3 次，每次间隔 1s）
        last_err = None
        for attempt in range(3):
            try:
                return self.get_file_url(full_path)
            except AlistError as e:
                last_err = e
                if attempt < 2:
                    time.sleep(1.0)
        raise AlistError(f"alist 上传成功但获取直链失败（重试 3 次）: {last_err}")

    def create_folder(self, path: str) -> None:
        """创建文件夹（如果不存在）。path 形如 /grouphub/works/123"""
        self._request(
            "POST",
            "/api/fs/mk",
            json={"path": path},
        )

    def close(self):
        if self._http and not self._http.is_closed:
            self._http.close()


# 全局单例
_client: Optional[AlistClient] = None


def get_alist() -> AlistClient:
    global _client
    if _client is None:
        _client = AlistClient()
    return _client
