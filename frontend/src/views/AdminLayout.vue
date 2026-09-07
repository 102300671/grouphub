<script setup lang="ts">
import { RouterLink, RouterView, useRoute, useRouter } from "vue-router";
import { useUserStore } from "@/stores/user";

const user = useUserStore();
const router = useRouter();
const route = useRoute();

const navs = [
  { to: "/admin", label: "仪表盘", icon: "📊", exact: true },
  { to: "/admin/users", label: "用户管理", icon: "👥", exact: false },
  { to: "/admin/works", label: "作品管理", icon: "📚", exact: false },
  { to: "/admin/settings", label: "站点设置", icon: "⚙️", exact: false },
];

function backToUserMode() {
  router.push("/");
}
async function onLogout() {
  await user.logout();
  router.replace("/login");
}
function isActive(to: string, exact: boolean) {
  if (exact) return route.path === to;
  return route.path.startsWith(to);
}
</script>

<template>
  <div class="admin-page">
    <header class="topbar">
      <div class="topbar-inner container">
        <div class="brand">
          <span class="logo">🛠</span>
          <div>
            <div class="title">群资源站 · 管理后台</div>
            <div class="subtitle muted text-sm">仅管理员可见 · 与普通用户界面共享登录态</div>
          </div>
        </div>
        <div class="row">
          <span class="badge badge-admin">{{ user.current?.nickname }} · 管理员</span>
          <button class="btn" @click="backToUserMode">⬅ 返回普通界面</button>
          <button class="btn btn-ghost" @click="onLogout">退出</button>
        </div>
      </div>
    </header>

    <div class="container layout">
      <aside class="side">
        <RouterLink
          v-for="n in navs"
          :key="n.to"
          :to="n.to"
          class="side-item"
          :class="{ active: isActive(n.to, n.exact) }"
        >
          <span class="icon">{{ n.icon }}</span>
          <span>{{ n.label }}</span>
        </RouterLink>
      </aside>

      <section class="main">
        <RouterView />
      </section>
    </div>
  </div>
</template>

<style scoped>
.admin-page {
  min-height: 100%;
  background: var(--color-bg);
}
.topbar {
  height: 66px;
  background: linear-gradient(135deg, #1e1b4b 0%, #312e81 100%);
  color: #fff;
  border-bottom: 1px solid #4338ca;
}
.topbar-inner {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.brand {
  display: flex;
  align-items: center;
  gap: 12px;
}
.brand .title {
  font-weight: 700;
  font-size: 16px;
}
.brand .subtitle {
  color: #c7d2fe !important;
}
.logo {
  width: 38px;
  height: 38px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.14);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
}
.topbar .btn {
  background: rgba(255, 255, 255, 0.1);
  border-color: rgba(255, 255, 255, 0.2);
  color: #fff;
}
.topbar .btn:hover {
  background: rgba(255, 255, 255, 0.18);
}
.topbar .btn-ghost {
  background: transparent;
  border-color: transparent;
  color: #e0e7ff;
}
.topbar .badge-admin {
  background: rgba(129, 140, 248, 0.25);
  color: #e0e7ff;
}

.layout {
  display: grid;
  grid-template-columns: 200px 1fr;
  gap: 20px;
  padding-top: 20px;
  padding-bottom: 40px;
  align-items: flex-start;
}
.side {
  background: #fff;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 10px;
  position: sticky;
  top: 86px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.side-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  color: inherit;
  font-size: 14px;
}
.side-item:hover {
  text-decoration: none;
  background: #f5f3ff;
  color: #4f46e5;
}
.side-item.active {
  background: #eef2ff;
  color: #4338ca;
  font-weight: 600;
  box-shadow: inset 3px 0 0 var(--color-primary);
}
.icon {
  font-size: 16px;
}
.main {
  min-width: 0;
}
@media (max-width: 860px) {
  .layout {
    grid-template-columns: 1fr;
  }
  .side {
    position: static;
    flex-direction: row;
    flex-wrap: wrap;
  }
}
@media (max-width: 640px) {
  .topbar {
    height: auto;
    min-height: 66px;
    padding: 10px 0;
  }
  .topbar-inner {
    flex-wrap: wrap;
  }
  .brand .subtitle {
    display: none;
  }
  .brand .title {
    font-size: 14px;
  }
  .topbar .row {
    gap: 8px;
    flex-wrap: wrap;
  }
  .layout {
    padding-top: 14px;
    padding-bottom: 24px;
    gap: 14px;
  }
}
</style>
