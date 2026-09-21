"""数据库引擎、Session 与 Base。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Generator
from urllib.parse import urlparse, unquote

from sqlalchemy import create_engine, text
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


def _migrate_sqlite(engine) -> None:
    """SQLite 轻量迁移：create_all 不会给已有表加列，这里幂等补列。"""
    if not _database_url.startswith("sqlite"):
        return
    from sqlalchemy import text

    wanted = {
        "verification_codes": {
            "purpose": "VARCHAR(20) NOT NULL DEFAULT 'login'",
        },
        "qq_openid_bindings": {
            "group_id": "VARCHAR(30)",
            "group_openid": "VARCHAR(128)",
            "group_name": "VARCHAR(100)",
        },
        "ai_conversations": {
            "ai_group_id": "INTEGER",
            "folder_id": "INTEGER",
            "is_default": "BOOLEAN NOT NULL DEFAULT 0",
        },
    }
    with engine.connect() as conn:
        for table, columns in wanted.items():
            rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
            if not rows:
                continue  # 表还不存在，create_all 会按新模型建
            existing = {row[1] for row in rows}
            for col, ddl in columns.items():
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
        conn.commit()


def _migrate_qq_bindings(engine) -> None:
    """绑定表重建：旧约束（openid 唯一）阻止同一用户多群绑定，重建为按群记录。"""
    with engine.begin() as conn:
        exists = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='index' AND name IN "
            "('uq_qq_openid_openid', 'sqlite_autoindex_qq_openid_bindings_1')"
        )).fetchone()
        if not exists:
            return
        conn.execute(text("ALTER TABLE qq_openid_bindings RENAME TO qq_openid_bindings_old"))
        conn.execute(text("""CREATE TABLE qq_openid_bindings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                qq VARCHAR(20) NOT NULL,
                openid VARCHAR(128) NOT NULL,
                openid_type VARCHAR(10) NOT NULL DEFAULT 'group',
                group_id VARCHAR(30),
                group_openid VARCHAR(128),
                group_name VARCHAR(100),
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )"""))
        conn.execute(text("""INSERT INTO qq_openid_bindings
               (id, qq, openid, openid_type, group_id, group_openid, group_name, created_at, updated_at)
               SELECT id, qq, openid, openid_type, group_id, group_openid, group_name, created_at, updated_at
               FROM qq_openid_bindings_old"""))
        conn.execute(text("DROP TABLE qq_openid_bindings_old"))
        conn.execute(text("CREATE UNIQUE INDEX uq_qq_openid_group ON qq_openid_bindings(group_openid)"
            " WHERE openid_type='group'"))
        conn.execute(text("CREATE UNIQUE INDEX uq_qq_openid_c2c ON qq_openid_bindings(openid)"
            " WHERE openid_type='c2c'"))
        print("[db] qq_openid_bindings 已重建为按群记录")


def _migrate_ai_groups(engine) -> None:
    """旧 AI 会话归位：group 会话 -> QQ 大组 + 群组；web 会话 -> 前端大组。"""
    from sqlalchemy.orm import Session
    from . import models  # noqa: F401

    def _folder_name(conv) -> str:
        if conv.source == "group":
            t = (conv.title or "").strip()
            if t.startswith("群聊 · "):
                return t[len("群聊 · "):][:50]
            return f"群 {conv.group_id or '?'}"
        return "默认"

    with Session(engine) as db:
        convs = (
            db.query(models.AIConversation)
            .filter(models.AIConversation.ai_group_id.is_(None))
            .all()
        )
        if not convs:
            return
        for conv in convs:
            user = conv.owner
            if conv.source == "group":
                g = (
                    db.query(models.AIGroup)
                    .filter(
                        models.AIGroup.owner_id == user.id,
                        models.AIGroup.kind == "qq",
                        models.AIGroup.qq == user.qq,
                    )
                    .first()
                )
                if g is None:
                    g = models.AIGroup(
                        owner_id=user.id, kind="qq", qq=user.qq, name=f"QQ {user.qq}"
                    )
                    db.add(g)
                    db.flush()
                folder = (
                    db.query(models.AIFolder)
                    .filter(
                        models.AIFolder.group_id == g.id,
                        models.AIFolder.openid.is_(None),
                        models.AIFolder.name == _folder_name(conv),
                    )
                    .first()
                )
                if folder is None:
                    folder = models.AIFolder(
                        group_id=g.id,
                        owner_id=user.id,
                        name=_folder_name(conv),
                        openid=None,
                    )
                    db.add(folder)
                    db.flush()
                conv.ai_group_id = g.id
                conv.folder_id = folder.id
            else:
                g = (
                    db.query(models.AIGroup)
                    .filter(
                        models.AIGroup.owner_id == user.id,
                        models.AIGroup.kind == "web",
                    )
                    .first()
                )
                if g is None:
                    g = models.AIGroup(owner_id=user.id, kind="web", name="我的空间")
                    db.add(g)
                    db.flush()
                conv.ai_group_id = g.id
        db.commit()


def init_db() -> None:
    """首次启动时创建所有表。"""
    # 先导入所有模型，确保 ORM 已注册
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _migrate_sqlite(engine)
    _migrate_ai_groups(engine)
    _migrate_qq_bindings(engine)
