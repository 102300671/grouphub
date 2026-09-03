<script setup lang="ts">
import { RouterLink, RouterView, useRouter } from "vue-router";
import { useUserStore } from "@/stores/user";

const user = useUserStore();
const router = useRouter();

/**
 * 【需求硬对齐】
 * 管理员登录进入本站默认展示的界面，和普通用户 100% 相同；
 * 仅当 user.isAdmin === true 时，右上角额外出现两个按钮：
 *   ①「管理员模式」—— 点击后进入 /admin/* 路由体系
 *   ②「退出登录」
 * 普通用户完全看不到切换按钮。
 */
async function toggleAdminMode() {
  if (!user.isAdmin) return;
  router.push("/admin");
}
async function exitAdminMode() {
  router.push("/");
}
async function onLogout() {
  await user.logout();
  router.replace("/login");
}
</script>

<template>
  <div class="page">
    <header class="navbar">
      <div class="container navbar-inner">
        <RouterLink to="/" class="brand">
          <span class="logo">👭</span>
          <span>群资源站</span>
        </RouterLink>

        <nav class="nav-links">
          <RouterLink to="/" active-class="active" exact-active-class="active">首页</RouterLink>
          <RouterLink to="/works" active-class="active">作品库</RouterLink>
          <RouterLink to="/fanworks" active-class="active">同人</RouterLink>
          <RouterLink to="/forum" active-class="active">论坛</RouterLink>
          <RouterLink to="/me" active-class="active">我的</RouterLink>
        </nav>

        <div class="actions">
          <span v-if="user.isLoggedIn" class="text-sm muted">你好，{{ user.current?.nickname }}</span>
          <span v-if="user.role === 'admin'" class="badge badge-admin">管理员</span>
          <span v-else-if="user.role === 'member'" class="badge badge-member">群友</span>

          <!-- 核心：只有管理员才看见「切换到管理模式」按钮；普通用户完全不渲染，界面一致 -->
          <button v-if="user.isAdmin" class="btn btn-admin" @click="toggleAdminMode">
            🛠 切换到管理模式
          </button>

          <button v-if="user.isLoggedIn" class="btn btn-ghost" @click="onLogout">退出</button>
          <RouterLink v-else to="/login" class="btn btn-primary">登录</RouterLink>
        </div>
      </div>
    </header>

    <main class="container content">
      <RouterView />
    </main>

    <footer class="container footer muted text-sm">
      🌸 © 群资源站 · 私域百合资源共享空间 · 由 QQ 群成员白名单保障权限
    </footer>
  </div>
</template>

<style scoped>
.page {
  min-height: 100%;
  display: flex;
  flex-direction: column;
}
.navbar {
  position: sticky;
  top: 0;
  z-index: 20;
  height: var(--navbar-h);
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: saturate(180%) blur(10px);
  border-bottom: 1px solid var(--color-border);
}
.navbar-inner {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 700;
  font-size: 16px;
  color: inherit;
  text-decoration: none;
}
.brand:hover {
  text-decoration: none;
}
.logo {
  width: 30px;
  height: 30px;
  border-radius: 8px;
  background: var(--accent-gradient);
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
}
.nav-links {
  display: flex;
  gap: 18px;
  font-size: 14px;
}
.nav-links a {
  color: var(--color-muted);
  padding: 6px 2px;
  border-bottom: 2px solid transparent;
}
.nav-links a:hover {
  text-decoration: none;
  color: var(--color-text);
}
.nav-links a.active {
  color: var(--color-primary);
  border-bottom-color: var(--color-primary);
  font-weight: 600;
}
.actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.content {
  flex: 1;
  padding-top: 24px;
  padding-bottom: 40px;
}
.footer {
  padding: 18px 20px 26px;
  text-align: center;
  border-top: 1px solid transparent;
}
@media (max-width: 720px) {
  .nav-links {
    display: none;
  }
}
</style>
