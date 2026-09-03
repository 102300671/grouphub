import { createRouter, createWebHistory } from "vue-router";
import type { RouteRecordRaw } from "vue-router";
import { useUserStore } from "@/stores/user";

/**
 * meta.mode：
 *   "user"   —— 普通用户模式（和管理员登录默认进入的模式一致）
 *   "admin"  —— 管理模式（管理员登录后才允许切到；前端按钮才可见/可点）
 */
export const routes: RouteRecordRaw[] = [
  {
    path: "/login",
    name: "login",
    component: () => import("@/views/LoginView.vue"),
    meta: { title: "登录", requiresAuth: false, mode: "user" },
  },
  {
    path: "/register",
    name: "register",
    component: () => import("@/views/RegisterView.vue"),
    meta: { title: "注册", requiresAuth: false, mode: "user" },
  },

  // ---------- 主布局（含「切换到管理界面」按钮） ----------
  {
    path: "/",
    component: () => import("@/views/MainLayout.vue"),
    meta: { requiresAuth: true, mode: "user" },
    children: [
      { path: "", name: "home", component: () => import("@/views/HomeView.vue"), meta: { title: "首页", mode: "user" } },
      { path: "works", name: "works", component: () => import("@/views/WorksView.vue"), meta: { title: "作品库", mode: "user" } },
      { path: "works/new", name: "work-create", component: () => import("@/views/CreateWorkView.vue"), meta: { title: "上传作品", mode: "user" } },
      { path: "works/:id", name: "work-detail", component: () => import("@/views/WorkDetailView.vue"), meta: { title: "作品详情", mode: "user" }, props: true },
      { path: "forum", name: "forum", component: () => import("@/views/ForumView.vue"), meta: { title: "论坛", mode: "user" } },
      { path: "forum/:id", name: "forum-topic", component: () => import("@/views/ForumTopicView.vue"), meta: { title: "论坛主题", mode: "user" }, props: true },
      { path: "fanworks", name: "fanworks", component: () => import("@/views/FanworksView.vue"), meta: { title: "同人创作", mode: "user" } },
      { path: "fanworks/new", name: "fanwork-create", component: () => import("@/views/FanworkEditView.vue"), meta: { title: "发布同人", mode: "user" } },
      { path: "fanworks/:id", name: "fanwork-detail", component: () => import("@/views/FanworkDetailView.vue"), meta: { title: "同人作品详情", mode: "user" }, props: true },
      { path: "fanworks/:id/edit", name: "fanwork-edit", component: () => import("@/views/FanworkEditView.vue"), meta: { title: "编辑同人", mode: "user" }, props: true },
      { path: "me", name: "me", component: () => import("@/views/MeView.vue"), meta: { title: "我的", mode: "user" } },
    ],
  },

  // ---------- 管理模式布局 ----------
  {
    path: "/admin",
    component: () => import("@/views/AdminLayout.vue"),
    meta: { requiresAuth: true, mode: "admin" }, // 路由守卫会同时校验 role=admin
    children: [
      { path: "", name: "admin-dashboard", component: () => import("@/views/admin/DashboardView.vue"), meta: { title: "仪表盘", mode: "admin" } },
      { path: "users", name: "admin-users", component: () => import("@/views/admin/UsersView.vue"), meta: { title: "用户管理", mode: "admin" } },
      { path: "works", name: "admin-works", component: () => import("@/views/admin/WorksAdminView.vue"), meta: { title: "作品管理", mode: "admin" } },
      { path: "settings", name: "admin-settings", component: () => import("@/views/admin/SettingsView.vue"), meta: { title: "站点设置", mode: "admin" } },
    ],
  },

  // ---------- 站内阅读（独立全屏页，供「新标签页阅读」使用） ----------
  {
    path: "/read/:id",
    name: "work-reader",
    component: () => import("@/views/ReaderView.vue"),
    meta: { title: "站内阅读", requiresAuth: true, mode: "user" },
    props: true,
  },

  { path: "/:pathMatch(.*)*", name: "not-found", component: () => import("@/views/NotFound.vue") },
];

export const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
});

router.beforeEach(async (to) => {
  const user = useUserStore();

  // 1) 需要登录且本地未登录 → 跳登录
  if (to.meta.requiresAuth && !user.isLoggedIn) {
    return { path: "/login", query: to.fullPath === "/login" ? undefined : { redirect: to.fullPath } };
  }

  // 2) 已经登录 → 后台拉一次 /auth/me 保证 role 是最新的（支持动态提权立即生效）
  if (user.isLoggedIn) {
    if (!user.fetchingMe) {
      // 不阻塞异常（拦截器会自动处理 401）
      void user.ensureMe();
    }

    // 3) 管理模式路由必须 role=admin；否则打回首页
    if (to.meta.mode === "admin" && !user.isAdmin) {
      return { path: "/" };
    }

    // 4) 已登录用户访问登录/注册 → 回首页
    if ((to.name === "login" || to.name === "register")) {
      return { path: "/" };
    }
  }

  const title = to.meta.title as string | undefined;
  if (title) {
    document.title = `${title} · 群资源站`;
  }
  return true;
});
