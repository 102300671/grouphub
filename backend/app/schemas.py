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
    # 验证码登录自动注册时，本次登录生成的一次性随机密码（仅当次响应返回，
    # 前端据此提示用户修改/记住密码；不落库、后续登录不再返回）
    generated_password: Optional[str] = None


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


class ChangePasswordIn(BaseModel):
    """修改密码：old_password（校验旧密码）与 code（QQ 验证码）二选一，至少提供一种。

    - old_password：常规改密（知道自己当前密码）
    - code：忘记密码时用验证码验证（先调 /auth/send-code，QQ 私聊收码后回填）
    """
    old_password: Optional[str] = None
    code: Optional[str] = Field(None, min_length=6, max_length=6)
    new_password: str = Field(..., min_length=6, max_length=72)


class OpenidBindingItem(BaseModel):
    """一条 openid 绑定记录。"""
    id: int
    openid: str
    openid_type: str
    group_id: Optional[str] = None
    group_openid: Optional[str] = None
    group_name: Optional[str] = None
    # 站点用户名（users.nickname）：列表默认展示名，点击才暴露 openid
    display_name: Optional[str] = None
    created_at: str
    updated_at: str


class ProfileUpdateIn(BaseModel):
    """用户修改自己的显示名称（站点昵称，建议填群内名称）。"""
    nickname: str = Field(..., min_length=1, max_length=50)


class AdminPatchActiveIn(BaseModel):
    """管理员启用/禁用用户。"""
    is_active: bool


class AdminSettingsPatchIn(BaseModel):
    """管理后台运行时设置（任一字段可选，只改传入项；持久化到 admin_settings 表）。"""
    show_relation_threshold: Optional[int] = Field(None, ge=0, le=1000)
    works_require_review: Optional[bool] = None


class AdminWorkStatusPatchIn(BaseModel):
    """作品审核：通过=published，驳回=draft，也可改回 pending。"""
    status: str = Field(pattern=r"^(published|draft|pending)$")


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
    group_openid: Optional[str] = None  # 群 openid（QQ 官方通道事件自带）
    group_name: Optional[str] = None   # 群名称（OneBot 通道可查）
    nickname_in_group: Optional[str] = None


class BotResolveOpenidOut(BaseModel):
    """openid → 真实 QQ 解析结果。

    入参 openid 为纯数字时按真实 QQ 直查（OneBot v11 通道）。
    is_active 为站点账号封禁状态：None=查无账号（视为未注册）。
    """
    ok: bool = True
    qq: Optional[str] = None
    is_active: Optional[bool] = None


class BotNotifyLoginIn(BaseModel):
    qq: str
    login_at: datetime
    ip: Optional[str] = None


class BotWorkSubmitIn(BaseModel):
    """插件 #4 works_submit：群内「安利」提交作品。"""
    qq: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1, max_length=255)
    author: Optional[str] = None
    type: Optional[str] = None  # novel/anime/movie/gallery/fanwork/other，默认 other（对齐 models.WorkType）
    summary: Optional[str] = None
    tags: Optional[List[str]] = None
    links: Optional[List[Dict[str, str]]] = None  # [{"site_name": "...", "url": "..."}]
    source_work_id: Optional[int] = None  # 同人文 → 原作
    uploader_qq: Optional[str] = None  # 特权参数：指定上传者 QQ（仅 X-Bot-Token 鉴权的机器人/管理员可用）；
    # 不传 → uploader 即提交者（qq），走旧的"自动建号"逻辑；
    # 传了 → 必须是已在站点注册的账号，否则驳回（不自动建号）。


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
    status: str = "published"
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


# =============== AI 配置 / 会话 ===============

class AIConfigIn(BaseModel):
    """新建 AI 配置。"""
    name: str = Field(..., min_length=1, max_length=100)
    kind: str = Field("remote", pattern="^(remote|local)$")
    api_base: Optional[str] = Field(None, max_length=500)
    api_key: Optional[str] = Field(None, max_length=500)
    model: Optional[str] = Field(None, max_length=200)
    system_prompt: Optional[str] = None


class AIConfigPatchIn(BaseModel):
    """编辑自己的 AI 配置；任意字段可选。"""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    api_base: Optional[str] = Field(None, max_length=500)
    api_key: Optional[str] = Field(None, max_length=500)
    model: Optional[str] = Field(None, max_length=200)
    system_prompt: Optional[str] = None


class AIConfigTestIn(BaseModel):
    """测试远程配置连通性（不落库）。"""
    api_base: str = Field(..., min_length=1, max_length=500)
    api_key: Optional[str] = Field(None, max_length=500)
    model: Optional[str] = None


class AIConfigOut(BaseModel):
    id: int
    name: str
    kind: str
    api_base: Optional[str] = None
    api_key: Optional[str] = None  # 已打码
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    searxng_url: Optional[str] = None
    is_active: bool = False
    is_builtin: bool = False


class AIConfigListOut(BaseModel):
    ok: bool
    items: List[AIConfigOut]
    active_id: int  # 0 表示内置默认


class AIConversationIn(BaseModel):
    """新建会话：必须属于某大组；folder_id 可空（未分组）。"""
    ai_group_id: int = Field(..., description="所属大组 id")
    folder_id: Optional[int] = Field(None, description="所属组 id（可空=未分组）")
    title: Optional[str] = Field(None, max_length=255)


class AIConversationPatchIn(BaseModel):
    """会话编辑：改名 / 移动大组 / 分组（folder_id 传 null=取消分组）。"""
    title: Optional[str] = Field(None, max_length=255)
    ai_group_id: Optional[int] = None
    folder_id: Optional[int] = None  # 显式 null 表示取消分组（移到大组下）


class AIMessageOut(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime


class AIConversationOut(BaseModel):
    id: int
    title: Optional[str] = None
    source: str
    group_id: Optional[str] = None
    ai_group_id: Optional[int] = None
    folder_id: Optional[int] = None
    is_default: bool = False
    config_id: Optional[int] = None
    archived: bool = False
    created_at: datetime
    updated_at: datetime
    last_message: Optional[str] = None


class AIConversationListOut(BaseModel):
    ok: bool
    items: List[AIConversationOut]


class AIConversationDetailOut(BaseModel):
    ok: bool
    conversation: AIConversationOut
    messages: List[AIMessageOut]


class AIGroupPatchIn(BaseModel):
    """大组改名。"""
    name: str = Field(..., min_length=1, max_length=100)


class AIFolderIn(BaseModel):
    """新建组（分组）。"""
    group_id: int = Field(..., description="所属大组 id")
    name: str = Field(..., min_length=1, max_length=100)


class AIFolderPatchIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


class AIConversationOutPatch(BaseModel):
    """会话补丁（移动大组 / 分组 / 取消分组）。"""
    title: Optional[str] = Field(None, max_length=255)
    ai_group_id: Optional[int] = None
    folder_id: Optional[int] = None


class AIGroupTreeOut(BaseModel):
    """大组树节点。"""
    id: int
    kind: str
    name: str
    qq: Optional[str] = None
    folders: List[dict] = []
    conversations: List[AIConversationOut] = []


class AIGroupTreeListOut(BaseModel):
    ok: bool
    groups: List[AIGroupTreeOut]


class SimpleIdOut(BaseModel):
    ok: bool
    id: int


class AIMessageIn(BaseModel):
    """本地配置浏览器直连时，用它单独持久化一条消息。"""
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str = Field(..., min_length=1)


class AIChatIn(BaseModel):
    """网页端远程对话（SSE 流式返回）。"""
    conversation_id: int
    content: str = Field(..., min_length=1)
    config_id: Optional[int] = None  # 不传则用当前生效配置


# ---- Bot 内部 ----

class AIBuiltinSyncIn(BaseModel):
    """机器人启动/配置变更时同步的一套内置远程配置。"""
    name: Optional[str] = Field("默认配置", max_length=100)
    api_base: str = Field(..., min_length=1, max_length=500)
    api_key: Optional[str] = Field(None, max_length=500)
    model: Optional[str] = Field(None, max_length=200)
    system_prompt: Optional[str] = None
    searxng_url: Optional[str] = Field(None, max_length=500)


class AIBuiltinSyncListIn(BaseModel):
    """机器人批量同步内置配置（.env.prod 可写多套，全部下发）。"""
    configs: List[AIBuiltinSyncIn]


class AIActiveConfigOut(BaseModel):
    """给机器人的生效配置（含真实密钥，仅 X-Bot-Token 通道）。"""
    config_id: int  # 0 = 内置默认
    kind: str
    api_base: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    searxng_url: Optional[str] = None


class AIActivateIn(BaseModel):
    qq: str = Field(..., min_length=1)
    config_id: int  # 0 = 切回内置默认


class AIBotConversationIn(BaseModel):
    """机器人取/建会话：按 openid 定位组（群名/机器人名）。"""
    qq: str = Field(..., min_length=1)
    group_id: str = Field(..., min_length=1)
    openid: Optional[str] = Field(None, max_length=128)
    folder_name: Optional[str] = Field(None, max_length=100, description="组名：群名或机器人名")
    title: Optional[str] = Field(None, max_length=255)
    force_new: bool = Field(False, description="True=归档当前默认会话并新建")


class AIBotConversationQuery(BaseModel):
    """机器人列会话：scope=current|all|web。"""
    qq: str = Field(..., min_length=1)
    openid: Optional[str] = Field(None, max_length=128)  # current 范围用
    scope: str = Field("current", pattern="^(current|all|web)$")


class AIBotSwitchIn(BaseModel):
    """切换当前会话（设为组内默认）。"""
    qq: str = Field(..., min_length=1)
    conversation_id: int = Field(..., gt=0)


class AIBotMoveIn(BaseModel):
    """把其它范围（其它 openid 组 / 前端大组）的会话移到当前 openid 组。"""
    qq: str = Field(..., min_length=1)
    openid: Optional[str] = Field(None, max_length=128)  # 目标组 openid；缺省按当前定位
    folder_name: Optional[str] = Field(None, max_length=100)  # 目标组不存在时用此名创建
    conversation_id: int = Field(..., gt=0)


class AIBotMessagesIn(BaseModel):
    qq: str = Field(..., min_length=1)
    messages: List[AIMessageIn]


class AIBotResetIn(BaseModel):
    qq: str = Field(..., min_length=1)
    group_id: str = Field(..., min_length=1)
