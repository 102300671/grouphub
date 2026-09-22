export type UserRole = "admin" | "member";

export interface AuthUser {
  id: number;
  qq: string;
  nickname: string;
  role: UserRole;
  is_active: boolean;
  avatar_url?: string | null;
  created_at?: string;
}

export interface AuthTokenOut {
  access_token: string;
  token_type: "bearer";
  user: AuthUser;
  /** 验证码登录自动注册时，本次登录生成的一次性随机密码（仅当次返回，用于提示用户修改/记住） */
  generated_password?: string | null;
}

/** 修改密码入参：old_password（校验旧密码）与 code（QQ 验证码）二选一 */
export interface ChangePasswordIn {
  old_password?: string;
  code?: string;
  new_password: string;
}

/** 注册第一步响应：账号已暂存，返回绑定码等用户发给机器人校验 */
export interface RegisterPendingOut {
  ok: boolean;
  qq: string;
  code: string;
  expires_in_minutes: number;
  message: string;
}

/** 注册绑定状态轮询响应 */
export interface RegisterStatusOut {
  ok: boolean;
  pending: boolean;
}

/** 登录后绑定码响应：老账号未绑定官方 openid 时，发码让用户发给机器人 */
export interface BindCodeOut {
  ok: boolean;
  bound: boolean;
  code?: string | null;
  expires_in_minutes?: number | null;
  message?: string;
}

/** 登录后绑定状态轮询响应 */
export interface BindStatusOut {
  ok: boolean;
  bound: boolean;
}

/** 一条 openid 绑定记录 */
export interface OpenidBindingItem {
  id: number;
  openid: string;
  openid_type: string;
  group_id?: string | null;
  group_openid?: string | null;
  group_name?: string | null;
  /** 站点用户名（users.nickname），默认展示名，点击后才显示 openid */
  display_name?: string | null;
  created_at: string;
  updated_at: string;
}

/** 当前用户的所有 openid 绑定列表 */
export interface BindingsListOut {
  ok: boolean;
  items: OpenidBindingItem[];
  count: number;
}

export interface SimpleMessageOut {
  ok?: boolean;
  message?: string;
  details?: Record<string, unknown>;
}

export type WorkType = "novel" | "anime" | "movie" | "gallery" | "fanwork" | "other" | string;
export type ReadingStatus = "reading" | "completed" | "plan" | "pause" | "drop" | string;
export type WorkStatus = "published" | "pending" | "draft" | string;

export interface Work {
  id: number;
  title: string;
  author?: string | null;
  type: WorkType;
  status?: WorkStatus;
  summary?: string | null;
  uploader?: { id: number; nickname: string; qq: string } | null;
  tags: string[];
  created_at: string;
  updated_at?: string;
  cover_url?: string | null;
}

export interface WorkLink {
  id: number;
  site_name: string | null;
  url: string;
}

export interface WorkFile {
  id: number;
  provider: string;
  url: string;
  file_name: string | null;
  mime_type?: string | null;
  size_bytes?: number | null;
  uploader?: { id: number; nickname: string | null; qq: string } | null;
  created_at?: string | null;
}

export interface WorkDetail extends Work {
  source_work_id?: number | null;
  source_work_title?: string | null;
  cover_url?: string | null;
  links: WorkLink[];
  external_files: WorkFile[];
  relations: {
    supporter_count: number;
    recommender_count: number;
    show_supporters: boolean;
    show_recommenders: boolean;
    reading_stats: Record<string, number>;
    supporters?: { user_id: number }[];
    recommenders?: { user_id: number }[];
  };
}

/** 章节（单文件可抽出文档 = 文内/目录切章；多文件/PDF/媒体 = 每文件一章） */
export interface WorkChapter {
  index: number;
  file_id: number;
  file_name: string | null;
  title: string;
  start: number;
  end: number | null;
}

export interface WorkChaptersResponse {
  mode: "split" | "file";
  chapters: WorkChapter[];
}

export interface WorkListResponse {
  total: number;
  page: number;
  page_size: number;
  items: Work[];
}

export interface ForumUser {
  id: number;
  nickname: string | null;
  qq: string;
  avatar_url?: string | null;
}

export interface Topic {
  id: number;
  creator_id: number;
  title: string;
  cover_url?: string | null;
  body?: string | null;
  attachments: { type: string; url: string; file_name: string; mime_type?: string | null; size_bytes?: number }[];
  post_count: number;
  created_at: string;
  creator?: ForumUser | null;
}

export interface TopicPost {
  id: number;
  topic_id: number;
  user_id: number;
  nickname: string | null;
  qq: string | null;
  avatar_url?: string | null;
  content: string;
  attachments: { type: string; url: string; file_name: string; mime_type?: string | null; size_bytes?: number }[];
  created_at: string;
}

export interface TopicListResponse {
  total: number;
  page: number;
  page_size: number;
  items: Topic[];
}

export interface TopicDetailResponse {
  topic: Topic;
  posts: {
    total: number;
    page: number;
    page_size: number;
    items: TopicPost[];
  };
}

// =============== 同人创作（F8） ===============

export type FanworkStatus = "draft" | "published";

export interface FanworkAttachment {
  type: "image" | "video" | "audio" | "file";
  url: string;
  file_name: string;
  mime_type?: string | null;
  size_bytes?: number;
}

export interface Fanwork {
  id: number;
  work_id?: number | null;
  work_title?: string | null;
  author: { id: number; qq: string; nickname: string | null } | null;
  title: string;
  category?: string | null;
  cover_url?: string | null;
  body?: string | null;
  attachments: FanworkAttachment[];
  status: FanworkStatus;
  created_at: string;
}

export interface FanworkListResponse {
  ok: boolean;
  total: number;
  page: number;
  page_size: number;
  items: Fanwork[];
}

export interface FanworkDetailResponse {
  ok: boolean;
  item: Fanwork;
}

export interface AdminSummary {
  counts: {
    users: number;
    users_disabled: number;
    works: number;
    works_pending: number;
    reviews: number;
    topics: number;
    fanworks: number;
    group_members_active: number;
  };
  show_relation_threshold: number;
  works_require_review: boolean;
  admin_count_in_env: number;
  current_admin_qqs: string[];
}

/** 管理后台用户列表项 */
export interface AdminUser {
  id: number;
  qq: string;
  nickname: string | null;
  role: UserRole;
  is_active: boolean;
  created_at: string;
}

/** 管理后台运行时设置（持久化于 admin_settings 表） */
export interface AdminSettings {
  show_relation_threshold: number;
  works_require_review: boolean;
}

export interface AdminWorkListResponse {
  items: Work[];
  total: number;
  pending_total: number;
  works_require_review: boolean;
}

// =============== AI 助手 ===============

export type AIConfigKind = "remote" | "local";

export interface AIConfig {
  id: number;
  name: string;
  kind: AIConfigKind;
  api_base?: string | null;
  api_key?: string | null; // 已打码
  model?: string | null;
  system_prompt?: string | null;
  searxng_url?: string | null;
  is_active: boolean;
  is_builtin: boolean;
}

export interface AIConfigListOut {
  ok: boolean;
  items: AIConfig[];
  active_id: number; // 0 = 内置默认
}

export interface AIConfigPayload {
  name: string;
  kind?: AIConfigKind;
  api_base?: string | null;
  api_key?: string | null;
  model?: string | null;
  system_prompt?: string | null;
}

export type AIConversationSource = "web" | "group";

export interface AIConversation {
  id: number;
  title?: string | null;
  source: AIConversationSource;
  group_id?: string | null;
  ai_group_id?: number | null;
  folder_id?: number | null;
  is_default: boolean;
  config_id?: number | null;
  archived: boolean;
  created_at: string;
  updated_at: string;
  last_message?: string | null;
}

export interface AIFolder {
  id: number;
  name: string;
  openid?: string | null;
  conversations: AIConversation[];
}

export interface AIGroupTree {
  id: number;
  kind: "qq" | "web";
  name: string;
  qq?: string | null;
  folders: AIFolder[];
  conversations: AIConversation[];
}

export interface AIGroupTreeListOut {
  ok: boolean;
  groups: AIGroupTree[];
}

export interface AIConversationCreate {
  ai_group_id: number;
  folder_id?: number | null;
  title?: string | null;
}

export interface AIConversationPatch {
  title?: string | null;
  ai_group_id?: number | null;
  folder_id?: number | null;
}

/** 一次激活包/工具调用：请求与响应成对存在（与后端 agent_steps 结构对齐） */
export interface AgentCallDTO {
  id: number;
  kind: "activate" | "tool";
  name: string;
  args: Record<string, unknown>;
  raw: string;
  result?: { ok: boolean; summary: string; content: string };
}

/** 一轮模型请求 = 一段思考 + 该轮内的若干调用 */
export interface AgentStepDTO {
  reasoning: string;
  calls: AgentCallDTO[];
}

export interface AIMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  agent_steps?: AgentStepDTO[] | null;
  created_at: string;
}

export interface AIConversationListOut {
  ok: boolean;
  items: AIConversation[];
}

export interface AIConversationDetailOut {
  ok: boolean;
  conversation: AIConversation;
  messages: AIMessage[];
}
