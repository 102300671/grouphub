"""群图书馆 ORM 模型 —— 与 PRD §7 对齐。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .db import Base


# ---------- 通用 ----------

def utcnow() -> datetime:
    """当前 UTC 时间（naive，与本项目 DateTime 列约定一致）。

    Python 3.12 起 datetime.utcnow() 已弃用，统一走这里。
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _now() -> datetime:
    return utcnow()


# ---------- 枚举 ----------
# 用字符串 enum，读数据库时一目了然

class UserRole(str):
    ADMIN = "admin"
    MEMBER = "member"


class WorkStatus(str):
    DRAFT = "draft"
    PUBLISHED = "published"


class WorkType(str):
    NOVEL = "novel"       # 小说
    ANIME = "anime"       # 番剧/动漫
    MOVIE = "movie"       # 电影（按视频类作品处理，与番剧复用逻辑）
    GALLERY = "gallery"   # 图/集（图集：多张图片，封面默认第一张，可指定任一张）
    FANWORK = "fanwork"   # 同人文（可关联原作或独立）
    OTHER = "other"


class ReadingStatus(str):
    UNSTARTED = "unstarted"
    READING_NOW = "reading_now"   # 正在看（当前）
    IN_PROGRESS = "in_progress"   # 在读中（搁置中但没丢）
    FINISHED = "finished"
    PLAN_TO_READ = "read"


class RelationRole(str):
    SUPPORTER = "supporter"
    RECOMMENDER = "recommender"
    # 注意：uploader 不在这里，写死在 works.uploader_id


class TopicStatus(str):
    OPEN = "open"
    PINNED = "pinned"
    LOCKED = "locked"
    ARCHIVED = "archived"


class FanworkStatus(str):
    DRAFT = "draft"
    PUBLISHED = "published"


class ExternalFileProvider(str):
    ZFILE = "zfile"
    URL = "url"  # 外站直链（不落本地/zfile，站内阅读/下载经后端代理拉取）


# ---------- 表 ----------

class User(Base):
    """群友账号。"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    qq = Column(String(20), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    nickname = Column(String(50), nullable=True)
    avatar_url = Column(String(500), nullable=True)
    role = Column(String(20), nullable=False, default=UserRole.MEMBER, server_default=UserRole.MEMBER)
    created_at = Column(DateTime, default=_now, nullable=False)


class GroupMember(Base):
    """群成员白名单（由 nonebot2 插件 #1 维护）。"""
    __tablename__ = "group_members"
    __table_args__ = (
        UniqueConstraint("qq", "group_id", name="uq_group_members_qq_group"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    qq = Column(String(20), index=True, nullable=False)
    group_id = Column(String(30), index=True, nullable=False)
    nickname_in_group = Column(String(100), nullable=True)
    avatar_url = Column(String(500), nullable=True)  # QQ 头像（插件 #1 同步时落 zfile 直链）
    is_active = Column(Boolean, nullable=False, default=True, server_default="1")
    first_seen_at = Column(DateTime, default=_now, nullable=False)
    last_synced_at = Column(DateTime, default=_now, onupdate=_now, nullable=False)


class VerificationCode(Base):
    """QQ 验证码临时表（插件 #2 auth_code 用）。

    purpose 区分用途：
      - login：登录验证码（站点生成 → bot 私聊下发 → 用户回填）
      - register：注册绑定码（站点生成 → 页面展示给用户 → 用户发给 bot 校验）
    """
    __tablename__ = "verification_codes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    qq = Column(String(20), index=True, nullable=False)
    code = Column(String(6), nullable=False)
    purpose = Column(String(20), nullable=False, default="login", server_default="login", index=True)
    used = Column(Boolean, nullable=False, default=False, server_default="0")
    expires_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=_now, nullable=False)


class QQOpenidBinding(Base):
    """QQ 官方平台 openid ↔ 真实 QQ 绑定（注册验证通过时建立）。

    官方平台 openid 按场景区分：群聊 member_openid（type='group'）、
    单聊 user_openid（type='c2c'），同一用户两类 openid 不同，各存一条。
    用于把官方通道收到的群/单聊消息解析回真实 QQ 号。
    """
    __tablename__ = "qq_openid_bindings"
    __table_args__ = (
        UniqueConstraint("openid", name="uq_qq_openid_openid"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    qq = Column(String(20), index=True, nullable=False)
    openid = Column(String(128), nullable=False)
    openid_type = Column(String(10), nullable=False, default="group", server_default="group")  # group | c2c
    group_id = Column(String(30), nullable=True)  # 群号（OneBot 通道；官方拿不到群号，存 SYNC_GROUPS 兜底值）
    group_openid = Column(String(128), nullable=True)  # 群 openid（官方通道事件自带；OneBot 通道为空）
    group_name = Column(String(100), nullable=True)  # 绑定时的群名称（openapi 查询；失败为空）
    created_at = Column(DateTime, default=_now, nullable=False)
    updated_at = Column(DateTime, default=_now, onupdate=_now, nullable=False)


class Work(Base):
    """作品。"""
    __tablename__ = "works"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(255), index=True, nullable=False)
    author = Column(String(255), nullable=True)
    type = Column(String(30), nullable=False, default=WorkType.OTHER, server_default=WorkType.OTHER)
    source_work_id = Column(Integer, ForeignKey("works.id"), nullable=True, index=True)  # 同人→原作
    cover_url = Column(String(500), nullable=True)
    summary = Column(Text, nullable=True)
    uploader_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    status = Column(String(30), nullable=False, default=WorkStatus.PUBLISHED, server_default=WorkStatus.PUBLISHED)
    sort_rank = Column(Integer, nullable=False, default=0, server_default="0")
    tags_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=_now, nullable=False)
    updated_at = Column(DateTime, default=_now, onupdate=_now, nullable=False)

    uploader = relationship("User", lazy="selectin")
    source_work = relationship("Work", remote_side="Work.id", lazy="selectin")
    links = relationship("WorkLink", lazy="selectin", cascade="all, delete-orphan")
    external_files = relationship("WorkExternalFile", lazy="selectin", cascade="all, delete-orphan", order_by="WorkExternalFile.id")


class WorkLink(Base):
    """作品源站跳转链接（0+）。"""
    __tablename__ = "work_links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    work_id = Column(Integer, ForeignKey("works.id"), nullable=False, index=True)
    site_name = Column(String(100), nullable=True)
    url = Column(String(500), nullable=False)


class WorkExternalFile(Base):
    """作品外部存储引用（zfile 等）。"""
    __tablename__ = "work_external_files"

    id = Column(Integer, primary_key=True, autoincrement=True)
    work_id = Column(Integer, ForeignKey("works.id"), nullable=False, index=True)
    provider = Column(String(30), nullable=False, default=ExternalFileProvider.ZFILE, server_default=ExternalFileProvider.ZFILE)
    url = Column(String(500), nullable=False)
    file_name = Column(String(255), nullable=True)
    mime_type = Column(String(100), nullable=True)
    size_bytes = Column(Integer, nullable=True)
    uploader_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=_now, nullable=False)

    uploader = relationship("User", lazy="selectin")


class WorkTag(Base):
    """标签（MVP 阶段少用，主要靠 Work.tags_json 先跑；预留规范化扩展位）。"""
    __tablename__ = "work_tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), unique=True, nullable=False, index=True)


class UserWork(Base):
    """用户-作品关系：supporter / recommender / reading_status。"""
    __tablename__ = "user_works"
    __table_args__ = (
        UniqueConstraint("user_id", "work_id", name="uq_user_works_user_work"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    work_id = Column(Integer, ForeignKey("works.id"), nullable=False, index=True)
    # 允许同时是 supporter + recommender，用逗号分隔的多值存，或拆位；MVP 用枚举数组（JSON）更灵活
    relation_roles = Column(JSON, nullable=False, default=list)  # ["supporter"], ["recommender"], 或都有
    reading_status = Column(String(30), nullable=True)  # ReadingStatus.*
    created_at = Column(DateTime, default=_now, nullable=False)
    updated_at = Column(DateTime, default=_now, onupdate=_now, nullable=False)


class Review(Base):
    """书评打分。"""
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True, autoincrement=True)
    work_id = Column(Integer, ForeignKey("works.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    rating = Column(Integer, nullable=True)  # 1~5（可选）
    title = Column(String(255), nullable=True)
    content = Column(Text, nullable=True)
    attachments_json = Column(JSON, nullable=True)  # 多媒体附件
    created_at = Column(DateTime, default=_now, nullable=False)
    updated_at = Column(DateTime, default=_now, onupdate=_now, nullable=False)

    user = relationship("User", lazy="selectin")


class Topic(Base):
    """讨论主题。"""
    __tablename__ = "topics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    creator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(255), nullable=False)
    cover_url = Column(String(500), nullable=True)
    body = Column(Text, nullable=True)
    attachments_json = Column(JSON, nullable=True)  # 主题附件（可选多媒体）
    status = Column(String(30), nullable=False, default=TopicStatus.OPEN, server_default=TopicStatus.OPEN)
    created_at = Column(DateTime, default=_now, nullable=False)


class TopicPost(Base):
    """讨论回帖。"""
    __tablename__ = "topic_posts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    topic_id = Column(Integer, ForeignKey("topics.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    content = Column(Text, nullable=False)
    attachments_json = Column(JSON, nullable=True)  # 回帖附件（可选多媒体）
    created_at = Column(DateTime, default=_now, nullable=False)

    user = relationship("User", lazy="selectin")


class Fanwork(Base):
    """同人创作展示。"""
    __tablename__ = "fanworks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    work_id = Column(Integer, ForeignKey("works.id"), nullable=True, index=True)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(255), nullable=False)
    category = Column(String(50), nullable=True)
    cover_url = Column(String(500), nullable=True)
    body = Column(Text, nullable=True)
    attachments_json = Column(JSON, nullable=True)  # 附件数组：[{"type":"image|video|audio", "url":"...", ...}]
    status = Column(String(30), nullable=False, default=FanworkStatus.DRAFT, server_default=FanworkStatus.DRAFT)
    created_at = Column(DateTime, default=_now, nullable=False)

    author = relationship("User", lazy="selectin")
    work = relationship("Work", lazy="selectin")


# ------------------ AI 配置 / 会话 / 消息 ------------------

class AIConfig(Base):
    """AI 接口配置。

    - owner_id 为 NULL：系统内置默认配置（唯一一行），由 nonebot2 启动时把
      .env.prod 解析出的远程配置同步过来，所有用户可见但不可改；
    - owner_id 指向用户：用户自建配置（BYOK），kind=remote 走后端代理，
      kind=local 由浏览器直连用户本地模型端点（用户本地网络）。
    is_active：该用户当前选中的配置（每个用户至多一行）；没有选中项时生效内置默认。
    """
    __tablename__ = "ai_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    name = Column(String(100), nullable=False)
    kind = Column(String(20), nullable=False, default="remote", server_default="remote")  # remote | local
    api_base = Column(String(500), nullable=True)
    api_key = Column(String(500), nullable=True)
    model = Column(String(200), nullable=True)
    system_prompt = Column(Text, nullable=True)
    searxng_url = Column(String(500), nullable=True)  # 仅内置默认使用
    is_active = Column(Boolean, nullable=False, default=False, server_default="0")
    created_at = Column(DateTime, default=_now, nullable=False)
    updated_at = Column(DateTime, default=_now, onupdate=_now, nullable=False)

    owner = relationship("User", lazy="selectin")


class AIConversation(Base):
    """AI 会话：网页端会话（source=web）或群内会话（source=group，按用户+群独立）。

    群内会话由机器人同步，用户在前端可查看历史，但仅展示用途；
    archived_at 非空表示已被「/ai 重置」归档，下次提问会开新会话（旧会话保留）。
    """
    __tablename__ = "ai_conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    source = Column(String(20), nullable=False, default="web", server_default="web")  # web | group
    title = Column(String(255), nullable=True)
    group_id = Column(String(30), nullable=True, index=True)
    config_id = Column(Integer, ForeignKey("ai_configs.id"), nullable=True)
    archived_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now, nullable=False)
    updated_at = Column(DateTime, default=_now, onupdate=_now, nullable=False)

    owner = relationship("User", lazy="selectin")
    config = relationship("AIConfig", lazy="selectin")
    messages = relationship(
        "AIMessage",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="AIMessage.id",
    )


class AIMessage(Base):
    """AI 会话内的一条消息（system 不落库，仅存 user / assistant）。"""
    __tablename__ = "ai_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(Integer, ForeignKey("ai_conversations.id"), nullable=False, index=True)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=_now, nullable=False)
