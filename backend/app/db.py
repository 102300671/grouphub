"""数据库引擎、Session 与 Base。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Generator
from urllib.parse import urlparse, unquote

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


settings = get_settings()


def _ensure_sqlite_dir(url: str) -> str:
    """如果是 sqlite:/// 相对路径，把它转成绝对路径并 mkdir -p 父目录，避免「打不开数据库文件」。

    返回规范化后的 url（其它数据库原样返回）。
    """
    if not url.startswith("sqlite"):
        return url
    # sqlite:/// 去掉前缀后就是文件路径；sqlite:// 是内存，跳过
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return url
    raw_path = url[len(prefix):]
    # 如果是 sqlite:///./data/x.db 这种带 query/fragment 的场景，只取路径部分
    parsed = urlparse(raw_path)
    file_path = Path(unquote(parsed.path))
    if not file_path.is_absolute():
        # 相对于 backend 项目根（即 app/ 的上一级）
        project_root = Path(__file__).resolve().parent.parent
        file_path = (project_root / file_path).resolve()
    file_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{file_path.as_posix()}"


_database_url = _ensure_sqlite_dir(settings.database_url)

# SQLite 需要 check_same_thread=False
connect_args = {"check_same_thread": False} if _database_url.startswith("sqlite") else {}
engine = create_engine(_database_url, echo=False, future=True, connect_args=connect_args)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：返回每个请求的独立 DB Session。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """首次启动时创建所有表。"""
    # 先导入所有模型，确保 ORM 已注册
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
