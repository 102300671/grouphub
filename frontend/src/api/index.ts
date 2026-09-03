import type { AuthUser, AuthTokenOut } from "@/types/api";
import { apiBase, authClient, request, extractErrMsg } from "@/api/http";
import type { Work, WorkDetail, WorkFile, WorkChaptersResponse, Fanwork, FanworkListResponse, FanworkDetailResponse, FanworkAttachment } from "@/types/api";

/** 按直链扩展名猜附件类型：image / video / audio / file */
export function guessAttType(url: string): "image" | "video" | "audio" | "file" {
  const ext = (url.split(/[?#]/)[0].split(".").pop() || "").toLowerCase();
  if (["jpg", "jpeg", "png", "gif", "webp", "bmp", "avif", "svg"].includes(ext)) return "image";
  if (["mp4", "webm", "mkv", "avi", "mov", "m4v", "flv", "ts", "wmv", "mpg", "mpeg"].includes(ext)) return "video";
  if (["mp3", "wav", "flac", "aac", "ogg", "m4a", "opus"].includes(ext)) return "audio";
  return "file";
}

/** 由直链直接构造附件 dict（不走文件上传，随 payload 提交） */
export function attachmentFromUrl(
  url: string,
  fileName?: string,
): { type: "image" | "video" | "audio" | "file"; url: string; file_name: string } {
  const path = url.split(/[?#]/)[0];
  const base = decodeURIComponent(path.split("/").pop() || "");
  return { type: guessAttType(url), url, file_name: fileName || base || "file" };
}

export interface WorkListResponse {
  total: number;
  page: number;
  page_size: number;
  items: Work[];
}

export const worksClient = {
  list(params?: { type?: string; keyword?: string; page?: number; page_size?: number }) {
    return request<WorkListResponse>({ url: "/works/", method: "GET", params });
  },
  detail(id: string | number) {
    return request<WorkDetail>({ url: `/works/${id}`, method: "GET" });
  },
  create(data: {
    title: string;
    author?: string;
    type?: string;
    cover_url?: string;
    summary?: string;
    tags?: string[];
    links?: { site_name: string; url: string }[];
    external_files?: Record<string, unknown>[];
  }) {
    return request<Work>({ url: "/works/", method: "POST", data });
  },
  update(id: string | number, data: {
    title?: string;
    author?: string | null;
    type?: string;
    source_work_id?: number | null;
    cover_url?: string | null;
    summary?: string | null;
    tags?: string[];
    links?: { site_name?: string | null; url: string }[];
  }) {
    return request<WorkDetail>({ url: `/works/${id}`, method: "PATCH", data });
  },
  remove(id: string | number) {
    return request<{ ok: boolean; message?: string }>({ url: `/works/${id}`, method: "DELETE" });
  },
  updateRelation(id: string | number, data: {
    is_supporter?: boolean;
    is_recommender?: boolean;
    reading_status?: string;
  }) {
    return request({ url: `/works/${id}/relation`, method: "PATCH", data });
  },
  async uploadCover(id: string | number, file: File): Promise<string> {
    const fd = new FormData();
    fd.append("file", file);
    const res = await request<{ ok: boolean; cover_url: string }>({
      url: `/works/${id}/cover`,
      method: "POST",
      data: fd,
      headers: { "Content-Type": "multipart/form-data" },
    });
    return res.cover_url;
  },
  async uploadFile(id: string | number, file: File): Promise<WorkFile> {
    const fd = new FormData();
    fd.append("file", file);
    const res = await request<{ ok: boolean; file: WorkFile }>({
      url: `/works/${id}/files`,
      method: "POST",
      data: fd,
      headers: { "Content-Type": "multipart/form-data" },
    });
    return res.file;
  },
  async uploadFilesBatch(id: string | number, files: File[]): Promise<WorkFile[]> {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    const res = await request<{ ok: boolean; files: WorkFile[] }>({
      url: `/works/${id}/files/batch`,
      method: "POST",
      data: fd,
      headers: { "Content-Type": "multipart/form-data" },
    });
    return res.files;
  },
  /** 外站直链添加为作品文件（不上传本体，站内阅读/下载经后端代理拉取） */
  async fileByUrl(id: string | number, url: string, fileName?: string): Promise<WorkFile> {
    const res = await request<{ ok: boolean; file: WorkFile }>({
      url: `/works/${id}/files/url`,
      method: "POST",
      data: { url, file_name: fileName || undefined },
    });
    return res.file;
  },
  removeFile(id: string | number, fileId: number) {
    return request<{ ok: boolean }>({ url: `/works/${id}/files/${fileId}`, method: "DELETE" });
  },
  /** 章节列表（单文件文本 = 文内切章；多文件 = 每文件一章） */
  chapters(id: string | number) {
    return request<WorkChaptersResponse>({ url: `/works/${id}/chapters`, method: "GET" });
  },
  /** 站内阅读代理直链（补正确 Content-Type/charset，根治新标签页打开乱码） */
  rawUrl(workId: string | number, fileId: number, start?: number, end?: number): string {
    let u = `${apiBase}/works/${workId}/files/${fileId}/raw`;
    if (start != null || end != null) {
      u += `?start=${start ?? 0}${end != null ? `&end=${end}` : ""}`;
    }
    return u;
  },
  /** 下载直链：整本（不传参数）/ 选文件 / 单文本文件选章节抽取合并 */
  downloadUrl(workId: string | number, opts?: { files?: number[]; chapters?: number[] }): string {
    const q: string[] = [];
    if (opts?.files?.length) q.push(`files=${opts.files.join(",")}`);
    if (opts?.chapters?.length) q.push(`chapters=${opts.chapters.join(",")}`);
    return `${apiBase}/works/${workId}/download${q.length ? `?${q.join("&")}` : ""}`;
  },
};

// ------------------- 同人创作（F8） -------------------

export interface FanworkPayload {
  title: string;
  work_id?: number | null;
  category?: string | null;
  cover_url?: string | null;
  body?: string | null;
  attachments?: FanworkAttachment[];
  status?: "draft" | "published";
}

export const fanworksClient = {
  list(params?: { keyword?: string; category?: string; work_id?: number; mine?: boolean; all?: boolean; page?: number; page_size?: number }) {
    return request<FanworkListResponse>({ url: "/fanworks/", method: "GET", params });
  },
  detail(id: string | number) {
    return request<FanworkDetailResponse>({ url: `/fanworks/${id}`, method: "GET" });
  },
  create(data: FanworkPayload) {
    return request<FanworkDetailResponse>({ url: "/fanworks/", method: "POST", data });
  },
  update(id: string | number, data: Partial<FanworkPayload>) {
    return request<FanworkDetailResponse>({ url: `/fanworks/${id}`, method: "PATCH", data });
  },
  remove(id: string | number) {
    return request<{ ok: boolean; message?: string }>({ url: `/fanworks/${id}`, method: "DELETE" });
  },
  async uploadCover(id: string | number, file: File): Promise<string> {
    const fd = new FormData();
    fd.append("file", file);
    const res = await request<{ ok: boolean; cover_url: string }>({
      url: `/fanworks/${id}/cover`,
      method: "POST",
      data: fd,
      headers: { "Content-Type": "multipart/form-data" },
    });
    return res.cover_url;
  },
  /** 上传同人附件到 /uploads/?category=fanwork，并按扩展名补全 type 字段 */
  async uploadAttachment(file: File): Promise<FanworkAttachment> {
    const fd = new FormData();
    fd.append("file", file);
    const res = await request<{ url: string; file_name: string; mime_type?: string | null; size_bytes?: number }>({
      url: "/uploads/?category=fanwork",
      method: "POST",
      data: fd,
      headers: { "Content-Type": "multipart/form-data" },
    });
    const mime = res.mime_type || file.type || "";
    const name = res.file_name || file.name;
    let type: FanworkAttachment["type"] = "file";
    if (mime.startsWith("image/") || /\.(png|jpe?g|gif|webp|bmp)$/i.test(name)) type = "image";
    else if (mime.startsWith("video/") || /\.(mp4|mov|mkv|webm)$/i.test(name)) type = "video";
    else if (mime.startsWith("audio/") || /\.(mp3|m4a|wav|ogg|flac)$/i.test(name)) type = "audio";
    return { type, url: res.url, file_name: name, mime_type: mime, size_bytes: res.size_bytes };
  },
};

export type { Fanwork };

export type AdminUser = AuthUser & { created_at?: string };

export const adminClient = {
  me() {
    return request<AdminUser>({ url: "/admin/me", method: "GET" });
  },
  summary() {
    return request<{ counts: Record<string, number>; show_relation_threshold: number; admin_count_in_env: number; current_admin_qqs: string[] }>({ url: "/admin/summary", method: "GET" });
  },
  users(params?: { q?: string; role?: "admin" | "member"; page?: number; size?: number }) {
    return request<AdminUser[]>({ url: "/admin/users", method: "GET", params });
  },
  changeRole(id: number, role: "admin" | "member") {
    return request({ url: `/admin/users/${id}/role`, method: "PATCH", data: { role } });
  },
  deleteUser(id: number) {
    return request<{ ok: boolean; message?: string }>({ url: `/admin/users/${id}`, method: "DELETE" });
  },
  triggerMemberSync() {
    return request<{ ok: boolean; message?: string; note?: string }>({ url: "/admin/trigger_member_sync", method: "POST" });
  },
  getThreshold() {
    return request<{ show_relation_threshold: number }>({ url: "/admin/settings/show_relation_threshold", method: "GET" });
  },
  setThreshold(threshold: number) {
    return request<{ ok: boolean; show_relation_threshold: number; note: string }>({
      url: "/admin/settings/show_relation_threshold",
      method: "PATCH",
      data: { threshold },
    });
  },
};

export { authClient, extractErrMsg };
