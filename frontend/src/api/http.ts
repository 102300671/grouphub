import axios, { AxiosError, AxiosRequestConfig, InternalAxiosRequestConfig } from "axios";
import type { AuthTokenOut, RegisterPendingOut, RegisterStatusOut, SimpleMessageOut } from "@/types/api";

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
  login(data: { qq: string; password: string }) {
    return request<AuthTokenOut>({ url: "/auth/login", method: "POST", data });
  },
  sendCode(qq: string) {
    return request<SimpleMessageOut>({ url: "/auth/send-code", method: "POST", data: { qq } });
  },
  confirmCode(data: { qq: string; code: string }) {
    return request<AuthTokenOut>({ url: "/auth/confirm-code", method: "POST", data });
  },
  me() {
    return request<AuthTokenOut["user"]>({ url: "/auth/me", method: "GET" });
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
