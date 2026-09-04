export type UserRole = "admin" | "member";

export interface AuthUser {
  id: number;
  qq: string;
  nickname: string;
  role: UserRole;
  avatar_url?: string | null;
  created_at?: string;
}

export interface AuthTokenOut {
  access_token: string;
  token_type: "bearer";
  user: AuthUser;
}

export interface SimpleMessageOut {
  ok?: boolean;
  message?: string;
  details?: Record<string, unknown>;
}

export type WorkType = "novel" | "anime" | "movie" | "gallery" | "fanwork" | "other" | string;
export type ReadingStatus = "reading" | "completed" | "plan" | "pause" | "drop" | string;

export interface Work {
  id: number;
  title: string;
  author?: string | null;
  type: WorkType;
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

/** 章节（单文件文本 = 文内切章；多文件/非文本 = 每文件一章） */
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
    works: number;
    reviews: number;
    topics: number;
    fanworks: number;
    group_members_active: number;
  };
  show_relation_threshold: number;
  admin_count_in_env: number;
  current_admin_qqs: string[];
}
