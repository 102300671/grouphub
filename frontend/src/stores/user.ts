import { authClient, clearAuth, extractErrMsg, loadCachedUser, saveAuth, saveCachedUser } from "@/api/http";
import { defineStore } from "pinia";
import { computed, ref } from "vue";
import type { AuthTokenOut, UserRole } from "@/types/api";

export const useUserStore = defineStore("user", () => {
  /** 启动时先用 localStorage 里缓存的 user 占位，避免刷新页面立刻丢失登录态。 */
  const current = ref<AuthTokenOut["user"] | null>(loadCachedUser());
  const fetchingMe = ref(false);
  const lastError = ref<string | null>(null);

  const isLoggedIn = computed(() => !!current.value);
  const isAdmin = computed(() => current.value?.role === "admin");
  const role = computed<UserRole | "">(() => current.value?.role ?? "");

  function _apply(out: AuthTokenOut) {
    saveAuth(out);
    current.value = out.user;
    lastError.value = null;
  }

  async function login(qq: string, password: string): Promise<void> {
    const out = await authClient.login({ qq, password });
    _apply(out);
  }

  async function register(payload: { qq: string; password: string; nickname?: string }) {
    // 注册第一步：只生成绑定码并返回（账号在验证通过前无法登录，不写登录态）
    return authClient.register(payload);
  }

  async function registerStatus(qq: string) {
    return authClient.registerStatus(qq);
  }

  async function loginByCode(qq: string, code: string) {
    const out = await authClient.confirmCode({ qq, code });
    _apply(out);
  }

  async function logout() {
    try {
      await authClient.logout();
    } catch (e) {
      // 登出失败忽略（多半是 token 过期），仍要清本地状态
    }
    clearAuth();
    current.value = null;
  }

  /**
   * 每次路由切换 / 应用启动时调用一次。
   * 核心：如果本地有缓存的 user，但实际服务端 role 已经变了（例如管理员从 env 里被加上/剔除），
   * 这里会用服务端的返回覆盖本地，前端才能正确显示/隐藏「切换到管理界面」按钮。
   */
  async function ensureMe(): Promise<AuthTokenOut["user"] | null> {
    if (!current.value) return null;
    fetchingMe.value = true;
    try {
      const me = await authClient.me();
      current.value = me;
      saveCachedUser(me);
      lastError.value = null;
      return me;
    } catch (e) {
      // 401 已在 axios 拦截器里跳走；其它错误保留缓存但记录一下
      lastError.value = extractErrMsg(e, "获取当前用户失败");
      return current.value;
    } finally {
      fetchingMe.value = false;
    }
  }

  return {
    current,
    fetchingMe,
    lastError,
    isLoggedIn,
    isAdmin,
    role,
    login,
    register,
    registerStatus,
    loginByCode,
    logout,
    ensureMe,
  };
});
