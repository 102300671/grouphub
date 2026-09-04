"""全局配置：通过 .env / 环境变量加载。"""
from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """站点与鉴权配置。"""

    # 数据库
    database_url: str = "sqlite:///./data/grouphub.db"

    # JWT
    jwt_secret_key: str = "dev-only-change-me"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60 * 24 * 7  # 7 天

    # 机器人内部 API 共享 Token
    bot_api_token: str = "dev-only-bot-token"

    # nonebot2 侧 HTTP 服务地址（站点反向调用：发验证码 / 登录通知）
    qqbot_api_base: str = "http://127.0.0.1:8083"

    # 站点
    site_name: str = "群资源站"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # 管理员 QQ 白名单（英文逗号分隔）。
    # 对应 QQ 注册时会直接创建为 role=admin；登录时若发现此 QQ 在白名单但当前 role!=admin，也会自动提权
    # （防止你在改环境变量前就已经注册过一个普通账号）。
    admin_qqs: str = ""

    # 文件存储（支持 zfile 和 alist，一个主一个回退）
    storage_primary: str = "zfile"  # "zfile" | "alist"
    zfile_public_prefix: str = "/zfile"
    alist_public_prefix: str = "/alist"

    # zfile 配置
    zfile_base_url: str = "http://localhost:8081"
    zfile_username: str = "grouphub"
    zfile_password: str = ""
    zfile_storage_key: str = "grouphub"

    # alist 配置
    alist_base_url: str = "http://localhost:5244"
    alist_username: str = "grouphub"
    alist_password: str = ""
    # 路径前缀：本地存储需设为存储源名（如 /grouphub），云盘留空
    alist_path_prefix: str = ""

    # 作品详情页「张三、李四 等共 N 人」的展示阈值（PRD §9#5）：关联人数 ≥ 阈值才显示并展开
    show_relation_threshold: int = 3

    # 上传限制（PRD §10 安全要求：文件上传类型 + 大小校验）
    max_upload_mb: int = 32
    # 作品文件（小说/番剧/电影）单独放宽：电影/番剧整片可达数 GB
    max_work_upload_mb: int = 4096

    host: str = "0.0.0.0"
    port: int = 8003

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def max_work_upload_bytes(self) -> int:
        return self.max_work_upload_mb * 1024 * 1024

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origin_list(self) -> List[str]:
        """把以逗号分隔的 CORS 源拆成 list。"""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def admin_qq_set(self) -> set[str]:
        """把 admin_qqs 拆成可查询的 set。"""
        return {q.strip() for q in self.admin_qqs.split(",") if q.strip()}


@lru_cache
def get_settings() -> Settings:
    """全局单例配置。"""
    return Settings()
