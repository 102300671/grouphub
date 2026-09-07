"""Pydantic 请求/响应 Schema。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# =============== 群成员 ===============

class GroupMemberBatchItem(BaseModel):
    """批量 upsert 中的单个成员条目（不含 group_id，统一取自外层）。"""
    qq: str = Field(..., min_length=1)
    nickname_in_group: Optional[str] = None


class GroupMemberIn(GroupMemberBatchItem):
    """单条 upsert：插件 #1 进群或增量同步用。多了 group_id 字段。"""
    group_id: str = Field(..., min_length=1)


class GroupMemberBatchIn(BaseModel):
    """批量 upsert：插件 #1 全量同步用。"""
    group_id: str = Field(..., min_length=1)
    members: List[GroupMemberBatchItem]
    mark_inactive_others: bool = Field(
        True, description="若为 true，group_id 下不在本次列表的群成员会被置为 is_active=false"
    )


class GroupMemberSetInactiveIn(BaseModel):
    """退群/被踢用。"""
    group_id: str
    qq: str


class AvatarSyncIn(BaseModel):
    """头像同步单条：插件 #1 从群成员信息拿到 QQ 头像 URL，推给站点转存 zfile。"""
    qq: str = Field(..., min_length=1)
    avatar_url: str = Field(..., min_length=1, description="QQ 头像外链（q.qlogo.cn 等）")


class AvatarSyncBatchIn(BaseModel):
    """头像同步批量。"""
    items: List[AvatarSyncIn]


class GroupMemberStatusGroupItem(BaseModel):
    group_id: str
    is_active: bool


class GroupMemberStatusOut(BaseModel):
    qq: str
    in_group: bool
    groups: List[GroupMemberStatusGroupItem] = []
    last_synced_at: Optional[datetime] = None


class SimpleMessageOut(BaseModel):
    """通用成功/失败结构。"""
    ok: bool = True
    message: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


# =============== 认证 ===============

class RegisterIn(BaseModel):
    qq: str = Field(..., min_length=1)
    password: str = Field(..., min_length=6)
    nickname: Optional[str] = None


class LoginIn(BaseModel):
    qq: str = Field(..., min_length=1)
    password: str = Field(...)


class SendCodeIn(BaseModel):
    qq: str = Field(..., min_length=1)


class ConfirmCodeIn(BaseModel):
    qq: str = Field(..., min_length=1)
    code: str = Field(..., min_length=6, max_length=6)


class AuthTokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: Dict[str, Any]  # {"id", "qq", "nickname", "role"}


class RegisterPendingOut(BaseModel):
    """注册第一步响应：账号已暂存，返回绑定码等用户发给机器人校验。"""
    ok: bool = True
    qq: str
    code: str
    expires_in_minutes: int
    message: str


class RegisterStatusOut(BaseModel):
    """注册绑定状态轮询响应。"""
    ok: bool = True
    pending: bool  # True=还有待验证的注册码（未验证）


class BindCodeOut(BaseModel):
    """登录后绑定码响应：老账号未绑定官方 openid 时，发码让用户发给机器人完成绑定。"""
    ok: bool = True
    bound: bool  # True=已绑定，无需再发码（code 为 None）
    code: Optional[str] = None
    expires_in_minutes: Optional[int] = None
    message: str = ""


class BindStatusOut(BaseModel):
    """登录后绑定状态轮询响应。"""
    ok: bool = True
    bound: bool


class OpenidBindingItem(BaseModel):
    """一条 openid 绑定记录。"""
    id: int
    openid: str
    openid_type: str
    group_id: Optional[str] = None
    group_name: Optional[str] = None
    created_at: str
    updated_at: str


class BindingsListOut(BaseModel):
    """当前用户的所有 openid 绑定列表。"""
    ok: bool = True
    items: list[OpenidBindingItem]
    count: int


# =============== Bot 发给站点（验证码发送回调入参） ===============

class BotSendCodeIn(BaseModel):
    """站点 POST /auth/send-code → 再调 nonebot2 POST /bot/auth/send-code 的请求体。
    这里是 nonebot2 端 HTTP 服务收到的入参模型定义（放 schemas 里统一定义）。"""
    qq: str
    code: str


class BotVerifyRegisterIn(BaseModel):
    """用户把注册绑定码发给 bot → bot 调 /bot/auth/verify-register 校验。

    - OneBot v11 通道：qq=真实 QQ 号（事件自带），group_id=真实群号（群消息时）
    - QQ 官方通道：openid=member_openid/user_openid，拿不到真实 QQ 与真实群号
    - group_name：群名称（OneBot 通道可查；官方通道为空）
    """
    code: str = Field(..., min_length=4, max_length=8)
    qq: Optional[str] = None          # OneBot 通道提供
    openid: Optional[str] = None      # QQ 官方通道提供
    openid_type: Optional[str] = None  # group | c2c
    group_id: Optional[str] = None    # 白名单归属群（拿不到时由 bot 传 SYNC_GROUPS 兜底）
    group_name: Optional[str] = None   # 群名称（OneBot 通道可查）
    nickname_in_group: Optional[str] = None


class BotResolveOpenidOut(BaseModel):
    """openid → 真实 QQ 解析结果。"""
    ok: bool = True
    qq: Optional[str] = None


class BotNotifyLoginIn(BaseModel):
    qq: str
    login_at: datetime
    ip: Optional[str] = None


class BotWorkSubmitIn(BaseModel):
    """插件 #4 works_submit：群内「安利」提交作品。"""
    qq: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1, max_length=255)
    author: Optional[str] = None
    type: Optional[str] = None  # novel/anime/comic/game/fanwork/other，默认 other
    summary: Optional[str] = None
    tags: Optional[List[str]] = None
    links: Optional[List[Dict[str, str]]] = None  # [{"site_name": "...", "url": "..."}]
    source_work_id: Optional[int] = None  # 同人文 → 原作


# =============== 作品（MVP 占位） ===============

class WorkIn(BaseModel):
    """上传作品。title 必填，其它可选（对应 F3 两种模式：仅名称 / 带详情）。"""
    title: str = Field(..., min_length=1)
    author: Optional[str] = None
    type: str = "other"  # novel / anime / movie / fanwork / other
    source_work_id: Optional[int] = None  # 同人→原作 ID
    cover_url: Optional[str] = None
    summary: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    links: List[Dict[str, str]] = Field(default_factory=list)
    external_files: List[Dict[str, Any]] = Field(default_factory=list)


class WorkOut(BaseModel):
    id: int
    title: str
    author: Optional[str] = None
    type: str
    source_work_id: Optional[int] = None
    source_work_title: Optional[str] = None
    cover_url: Optional[str] = None
    summary: Optional[str] = None
    uploader: Dict[str, Any]  # {"id", "nickname", "qq"}
    tags: List[str] = []
    created_at: datetime


class WorkPatchIn(BaseModel):
    """编辑作品（任意字段都可选）。上传者本人或管理员可改。"""
    title: Optional[str] = Field(None, min_length=1)
    author: Optional[str] = None
    type: Optional[str] = None
    source_work_id: Optional[int] = None
    cover_url: Optional[str] = None
    summary: Optional[str] = None
    tags: Optional[List[str]] = None
    # links 传了则整体替换为本次列表（= 增 + 删 + 改的语义）
    links: Optional[List[Dict[str, str]]] = None


class WorkLinkOut(BaseModel):
    id: int
    site_name: Optional[str] = None
    url: str


class WorkFileOut(BaseModel):
    id: int
    provider: str
    url: str
    file_name: Optional[str] = None
    mime_type: Optional[str] = None
    size_bytes: Optional[int] = None
    uploader: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None


class WorkFileUrlIn(BaseModel):
    """外站直链添加为作品文件（不搬运文件本体）。"""
    url: str = Field(..., min_length=1, max_length=1000)
    file_name: Optional[str] = Field(None, max_length=255, description="可选：自定义显示文件名，缺省从直链路径推断")


# =============== 用户-作品关系（MVP 占位） ===============

class UserWorkPatchIn(BaseModel):
    """本人编辑自己的 supporter / recommender / 阅读状态。"""
    is_supporter: Optional[bool] = None
    is_recommender: Optional[bool] = None
    reading_status: Optional[str] = None


# =============== 同人创作（F8） ===============

class FanworkIn(BaseModel):
    """发布同人创作。title 必填；附件先走 /uploads/?category=fanwork 拿 URL 再提交。"""
    title: str = Field(..., min_length=1, max_length=255)
    work_id: Optional[int] = None  # 关联原作（可空，表示自由创作）
    category: Optional[str] = Field(None, max_length=50)
    cover_url: Optional[str] = Field(None, max_length=500)
    body: Optional[str] = None
    attachments: List[Dict[str, Any]] = Field(default_factory=list)
    status: str = "draft"  # draft / published


class FanworkPatchIn(BaseModel):
    """编辑同人创作；所有字段可选，传了才改。"""
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    work_id: Optional[int] = None
    category: Optional[str] = Field(None, max_length=50)
    cover_url: Optional[str] = Field(None, max_length=500)
    body: Optional[str] = None
    attachments: Optional[List[Dict[str, Any]]] = None
    status: Optional[str] = None
