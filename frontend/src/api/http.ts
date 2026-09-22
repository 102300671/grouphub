import axios, { AxiosError, AxiosRequestConfig, InternalAxiosRequestConfig } from "axios";
import type {
  AIConfigListOut,
  AIConfigPayload,
  AIConversationCreate,
  AIConversationDetailOut,
  AIConversationListOut,
  AIConversationPatch,
  AIGroupTreeListOut,
  AuthTokenOut,
  BindCodeOut,
  BindStatusOut,
  BindingsListOut,
  RegisterPendingOut,
  RegisterStatusOut,
  SimpleMessageOut,
} from "@/types/api";

const TOKEN_KEY = "grouphub.access_token";
const USER_KEY = "grouphub.current_user";

/** 读取环境变量里的 API 基址；默认 /api（配合 vite.config.ts 代理到 backend）。 */
export const apiBase = (import.meta.env.VITE_API_BASE || "/api").replace(/\/$/, "");

export const http = axios.create({
  baseURL: apiBase,
  timeout: 15000,
  withCredentials: false,
  headers: { "Content-Type": "application/json" },
});

// 请求拦截器：注入 JWT
http.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const token = getToken();
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error),
);

// 响应拦截器：统一 401 清理登录态 + 跳转
http.interceptors.response.use(
  (res) => res,
  (err: AxiosError<{ detail?: string }>) => {
    if (err.response?.status === 401) {
      clearAuth();
      const url = window.location.pathname;
      if (url !== "/login" && url !== "/register") {
        window.location.href = `/login?redirect=${encodeURIComponent(url)}`;
      }
    }
    return Promise.reject(err);
  },
);

/* ----------------- 辅助：读业务错误信息 ----------------- */
export function extractErrMsg(err: unknown, fallback = "请求失败"): string {
  if (axios.isAxiosError(err)) {
    const data = err.response?.data as { detail?: string; message?: string } | undefined;
    if (typeof data?.detail === "string") return data.detail;
    if (typeof data?.message === "string") return data.message;
    if (err.message) return err.message;
  }
  if (err instanceof Error) return err.message;
  return fallback;
}

/* ----------------- 鉴权存储 ----------------- */
export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function saveAuth(out: AuthTokenOut) {
  localStorage.setItem(TOKEN_KEY, out.access_token);
  localStorage.setItem(USER_KEY, JSON.stringify(out.user));
}

export function clearAuth() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

export function loadCachedUser(): AuthTokenOut["user"] | null {
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    clearAuth();
    return null;
  }
}

export function saveCachedUser(u: AuthTokenOut["user"]) {
  localStorage.setItem(USER_KEY, JSON.stringify(u));
}

/* ----------------- 通用请求：自带 .data 解包 ----------------- */
export async function request<T = unknown>(config: AxiosRequestConfig): Promise<T> {
  const res = await http.request<unknown, { data: T }>(config);
  return res.data as T;
}

/* ----------------- 按业务分类的 client ----------------- */
export const authClient = {
  register(data: { qq: string; password: string; nickname?: string }) {
    return request<RegisterPendingOut>({ url: "/auth/register", method: "POST", data });
  },
  registerStatus(qq: string) {
    return request<RegisterStatusOut>({
      url: "/auth/register/status",
      method: "GET",
      params: { qq },
    });
  },
  bindCode() {
    return request<BindCodeOut>({ url: "/auth/bind-code", method: "POST" });
  },
  bindStatus() {
    return request<BindStatusOut>({ url: "/auth/bind/status", method: "GET" });
  },
  listBindings() {
    return request<BindingsListOut>({ url: "/auth/bindings", method: "GET" });
  },
  deleteBinding(bindingId: number) {
    return request<SimpleMessageOut>({
      url: `/auth/bindings/${bindingId}`,
      method: "DELETE",
    });
  },
  login(data: { qq: string; password: string }) {
    return request<AuthTokenOut>({ url: "/auth/login", method: "POST", data });
  },
  sendCode(qq: string) {
    return request<SimpleMessageOut>({ url: "/auth/send-code", method: "POST", data: { qq } });
  },
  confirmCode(data: { qq: string; code: string }) {
    return request<AuthTokenOut>({ url: "/auth/confirm-code", method: "POST", data });
  },
  changePassword(data: { old_password?: string; code?: string; new_password: string }) {
    return request<SimpleMessageOut>({ url: "/auth/change-password", method: "POST", data });
  },
  me() {
    return request<AuthTokenOut["user"]>({ url: "/auth/me", method: "GET" });
  },
  updateProfile(data: { nickname: string }) {
    return request<AuthTokenOut["user"]>({ url: "/auth/me", method: "PATCH", data });
  },
  logout() {
    return request<SimpleMessageOut>({ url: "/auth/logout", method: "POST" });
  },
  userWorks() {
    return request<{
      ok: boolean;
      uploaded: Array<{
        id: number;
        title: string;
        type: string;
        author: string | null;
        cover_url: string | null;
        updated_at: string | null;
      }>;
      supported: Array<{
        id: number;
        title: string;
        type: string;
        author: string | null;
        cover_url: string | null;
        updated_at: string | null;
      }>;
      recommended: Array<{
        id: number;
        title: string;
        type: string;
        author: string | null;
        cover_url: string | null;
        updated_at: string | null;
      }>;
    }>({ url: "/auth/user-works", method: "GET" });
  },
};

/* ----------------- AI 助手 ----------------- */

export const aiClient = {
  listConfigs() {
    return request<AIConfigListOut>({ url: "/ai/configs", method: "GET" });
  },
  createConfig(data: AIConfigPayload) {
    return request({ url: "/ai/configs", method: "POST", data });
  },
  updateConfig(id: number, data: Partial<AIConfigPayload>) {
    return request({ url: `/ai/configs/${id}`, method: "PATCH", data });
  },
  deleteConfig(id: number) {
    return request<SimpleMessageOut>({ url: `/ai/configs/${id}`, method: "DELETE" });
  },
  activateConfig(id: number) {
    return request<AIConfigListOut>({
      url: `/ai/configs/${id}/activate`,
      method: "POST",
    });
  },
  testConfig(data: { api_base: string; api_key?: string; model?: string }) {
    return request<SimpleMessageOut>({
      url: "/ai/configs/test",
      method: "POST",
      data,
    });
  },
  listConversations() {
    return request<AIConversationListOut>({ url: "/ai/conversations", method: "GET" });
  },
  createConversation(data?: AIConversationCreate) {
    return request<AIConversationDetailOut>({
      url: "/ai/conversations",
      method: "POST",
      data: data || {},
    });
  },
  getConversation(id: number) {
    return request<AIConversationDetailOut>({
      url: `/ai/conversations/${id}`,
      method: "GET",
    });
  },
  deleteConversation(id: number) {
    return request<SimpleMessageOut>({
      url: `/ai/conversations/${id}`,
      method: "DELETE",
    });
  },
  /** 本地配置浏览器直连时：单独持久化一条消息（assistant 可带思维链/工具链轨迹） */
  appendMessage(
    id: number,
    role: "user" | "assistant",
    content: string,
    agentSteps?: unknown,
  ) {
    return request<SimpleMessageOut>({
      url: `/ai/conversations/${id}/messages`,
      method: "POST",
      data: agentSteps && role === "assistant"
        ? { role, content, agent_steps: agentSteps }
        : { role, content },
    });
  },
  /* ---------- 会话层级（大组 / 组） ---------- */
  listGroups() {
    return request<AIGroupTreeListOut>({ url: "/ai/groups", method: "GET" });
  },
  renameGroup(id: number, name: string) {
    return request<SimpleMessageOut>({
      url: `/ai/groups/${id}`,
      method: "PATCH",
      data: { name },
    });
  },
  createFolder(group_id: number, name: string) {
    return request<SimpleMessageOut>({
      url: "/ai/folders",
      method: "POST",
      data: { group_id, name },
    });
  },
  renameFolder(id: number, name: string) {
    return request<SimpleMessageOut>({
      url: `/ai/folders/${id}`,
      method: "PATCH",
      data: { name },
    });
  },
  deleteFolder(id: number) {
    return request<SimpleMessageOut>({
      url: `/ai/folders/${id}`,
      method: "DELETE",
    });
  },
  patchConversation(id: number, data: AIConversationPatch) {
    return request<AIConversationDetailOut>({
      url: `/ai/conversations/${id}`,
      method: "PATCH",
      data,
    });
  },
  setDefaultConversation(id: number) {
    return request<SimpleMessageOut>({
      url: `/ai/conversations/${id}/default`,
      method: "POST",
    });
  },
};

/**
 * 远程配置流式对话（走后端代理）。
 * POST /ai/chat，逐行读取后端 SSE：
 *   data: {"type":"delta","text":"..."} / {"type":"done"} / {"type":"error","message":"..."}
 * 返回 Promise（done 结束 / error 抛出）。
 */
export type ToolCallKind = "activate" | "tool";

export async function streamRemoteChat(
  conversationId: number,
  content: string,
  handlers: {
    onDelta: (text: string) => void;
    onReasoning?: (text: string) => void;
    /** 一轮模型请求开始：思考与该轮内的调用归入同一个步骤卡片 */
    onRound?: (index: number) => void;
    onToolCall?: (
      id: number,
      kind: ToolCallKind,
      name: string,
      args: Record<string, unknown>,
      raw: string,
    ) => void;
    onToolResult?: (
      id: number,
      name: string,
      ok: boolean,
      summary: string,
      content: string,
    ) => void;
  },
): Promise<void> {
  const token = getToken();
  const resp = await fetch(`${apiBase}/ai/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ conversation_id: conversationId, content }),
  });
  if (!resp.ok || !resp.body) {
    const text = await resp.text().catch(() => "");
    throw new Error(text || `请求失败（HTTP ${resp.status}）`);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      if (!line.startsWith("data:")) continue;
      const payload = line.slice(5).trim();
      if (!payload) continue;
      try {
        const evt = JSON.parse(payload);
        if (evt.type === "delta") handlers.onDelta(evt.text || "");
        else if (evt.type === "reasoning") handlers.onReasoning?.(evt.text || "");
        else if (evt.type === "round") handlers.onRound?.(Number(evt.index) || 0);
        else if (evt.type === "tool_call")
          handlers.onToolCall?.(
            Number(evt.id),
            (evt.kind as ToolCallKind) || "tool",
            evt.name || "",
            evt.args || {},
            evt.raw || "",
          );
        else if (evt.type === "tool_result")
          handlers.onToolResult?.(
            Number(evt.id),
            evt.name || "",
            evt.ok !== false,
            evt.summary || "",
            evt.content || evt.summary || "",
          );
        else if (evt.type === "error") throw new Error(evt.message || "上游错误");
      } catch (e) {
        if (e instanceof SyntaxError) continue;
        throw e;
      }
    }
  }
}

/**
 * 本地配置浏览器直连：直接 POST 到用户本地模型端点（OpenAI 兼容，流式）。
 * 不持久化（调用方自行通过 aiClient.appendMessage 落库）。
 */
export async function streamLocalChat(
  endpoint: { api_base: string; api_key?: string | null; model: string },
  messages: Array<{ role: string; content: string }>,
  onDelta: (text: string) => void,
  onReasoning?: (text: string) => void,
): Promise<void> {
  const base = (endpoint.api_base || "").replace(/\/$/, "");
  const url = base.endsWith("/chat/completions")
    ? base
    : `${base}/chat/completions`;
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (endpoint.api_key) headers.Authorization = `Bearer ${endpoint.api_key}`;
  // 先带 enable_thinking（Qwen3 等：思考走 reasoning_content 独立通道）；
  // 本地端点不认识该参数（400/422）时去参重试一次。
  const buildBody = (withThinking: boolean) =>
    JSON.stringify(
      withThinking
        ? { model: endpoint.model, messages, stream: true, enable_thinking: true }
        : { model: endpoint.model, messages, stream: true },
    );
  let resp = await fetch(url, {
    method: "POST",
    headers,
    body: buildBody(true),
  });
  if (resp.status === 400 || resp.status === 422) {
    resp = await fetch(url, {
      method: "POST",
      headers,
      body: buildBody(false),
    });
  }
  if (!resp.ok || !resp.body) {
    const text = await resp.text().catch(() => "");
    throw new Error(text || `本地端点返回 HTTP ${resp.status}（请确认服务已启动且允许跨域）`);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith("data:")) continue;
      const data = trimmed.slice(5).trim();
      if (!data || data === "[DONE]") continue;
      try {
        const chunk = JSON.parse(data);
        const delta = chunk.choices?.[0]?.delta || {};
        const reasoning = delta.reasoning_content || "";
        if (reasoning && onReasoning) onReasoning(reasoning);
        const piece = delta.content || "";
        if (piece) onDelta(piece);
      } catch (SyntaxError) {
        // 忽略半行/心跳
      }
    }
  }
}
