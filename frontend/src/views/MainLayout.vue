<script setup lang="ts">
import { RouterLink, RouterView, useRouter } from "vue-router";
import { onBeforeUnmount, onMounted, ref } from "vue";
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

/* ---------- 老账号 openid 补绑：登录后检查一次，未绑定弹窗引导发码给机器人 ---------- */
const showBindModal = ref(false);
const bindCode = ref("");
const bindExpires = ref(10);
let bindPollTimer: ReturnType<typeof setInterval> | null = null;

onMounted(async () => {
  if (!user.isLoggedIn || !user.pendingBindCheck) return;
  user.pendingBindCheck = false;
  try {
    const out = await user.fetchBindCode();
    if (!out.bound && out.code) {
      bindCode.value = out.code;
      bindExpires.value = out.expires_in_minutes || 10;
      showBindModal.value = true;
      startBindPoll();
    }
  } catch {
    // 检查失败（如 60s 内重复登录触发限频）不影响正常使用
  }
});

function startBindPoll() {
  stopBindPoll();
  bindPollTimer = setInterval(async () => {
    try {
      const st = await user.checkBindStatus();
      if (st.bound) {
        stopBindPoll();
        showBindModal.value = false;
      }
    } catch {
      // 轮询失败忽略，下个周期重试
    }
  }, 3000);
}
function stopBindPoll() {
  if (bindPollTimer) {
    clearInterval(bindPollTimer);
    bindPollTimer = null;
  }
}
function dismissBind() {
  stopBindPoll();
  showBindModal.value = false;
}
function copyBindCode() {
  navigator.clipboard?.writeText(bindCode.value).catch(() => {});
}
onBeforeUnmount(stopBindPoll);
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
          <span v-if="user.isLoggedIn" class="text-sm muted m-btn-desktop">你好，{{ user.current?.nickname }}</span>
          <span v-if="user.role === 'admin'" class="badge badge-admin m-btn-desktop">管理员</span>
          <span v-else-if="user.role === 'member'" class="badge badge-member m-btn-desktop">群友</span>

          <!-- 核心：只有管理员才看见「切换到管理模式」按钮；普通用户完全不渲染，界面一致 -->
          <button v-if="user.isAdmin" class="btn btn-admin m-btn-desktop" @click="toggleAdminMode">
            🛠 切换到管理模式
          </button>
          <!-- 移动端紧凑版管理入口 -->
          <button v-if="user.isAdmin" class="btn btn-admin m-btn-mobile" title="切换到管理模式" @click="toggleAdminMode">
            🛠
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

    <!-- 移动端底部 Tab 导航（仅 ≤720px 显示） -->
    <nav class="tabbar">
      <RouterLink to="/" active-class="active" exact-active-class="active">
        <span class="tab-icon">🏠</span>首页
      </RouterLink>
      <RouterLink to="/works" active-class="active">
        <span class="tab-icon">📚</span>作品
      </RouterLink>
      <RouterLink to="/fanworks" active-class="active">
        <span class="tab-icon">🌸</span>同人
      </RouterLink>
      <RouterLink to="/forum" active-class="active">
        <span class="tab-icon">💬</span>论坛
      </RouterLink>
      <RouterLink to="/me" active-class="active">
        <span class="tab-icon">👤</span>我的
      </RouterLink>
    </nav>

    <!-- 老账号 openid 补绑弹窗：把验证码发给机器人即可完成绑定，绑定后自动关闭 -->
    <div v-if="showBindModal" class="modal-mask" @click.self="dismissBind">
      <div class="modal card">
        <div class="modal-head">
          <h3>🔔 绑定机器人官方身份</h3>
        </div>
        <p class="muted text-sm">
          检测到你的账号还未绑定机器人的官方通道身份，群内「热门 / 搜索 / 安利」等命令需要绑定后才能识别你。把下方验证码发给机器人即可完成：
        </p>
        <div class="bind-code-box">
          <span class="bind-code-text">{{ bindCode }}</span>
          <button class="btn btn-ghost btn-sm" type="button" @click="copyBindCode">复制</button>
        </div>
        <div class="bind-cmd-box">@机器人 绑定 {{ bindCode }}</div>
        <p class="muted text-sm">在群里 @机器人 发送上方命令（或私聊机器人），本页会自动检测，绑定成功后弹窗自动关闭。</p>
        <div class="modal-foot">
          <span class="muted text-sm">有效期 {{ bindExpires }} 分钟</span>
          <button class="btn btn-ghost btn-sm" type="button" @click="dismissBind">稍后再说</button>
        </div>
      </div>
    </div>
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
  .navbar {
    height: 54px;
  }
  .brand {
    font-size: 15px;
  }
  .content {
    padding-top: 16px;
    /* 给固定底部 Tab 留出空间 */
    padding-bottom: 72px;
  }
  .footer {
    padding-bottom: calc(72px + env(safe-area-inset-bottom, 0px));
  }
  .bind-code-text {
    font-size: 22px;
    letter-spacing: 4px;
  }
}

/* ---------- openid 补绑弹窗 ---------- */
.modal-mask {
  position: fixed;
  inset: 0;
  z-index: 50;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.modal {
  width: 100%;
  max-width: 420px;
  box-shadow: var(--shadow-md);
}
.modal-head h3 {
  margin: 0 0 8px;
}
.bind-code-box {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 12px 0 8px;
  padding: 12px 16px;
  border-radius: 12px;
  background: rgba(79, 70, 229, 0.08);
  border: 1px dashed rgba(79, 70, 229, 0.4);
}
.bind-code-text {
  font-size: 28px;
  font-weight: 700;
  letter-spacing: 6px;
  font-family: var(--font-mono, monospace);
}
.bind-cmd-box {
  padding: 10px 14px;
  border-radius: 10px;
  background: var(--color-bg-soft, #f5f5f7);
  border: 1px solid var(--color-border, #e5e5ea);
  font-family: var(--font-mono, monospace);
  margin-bottom: 8px;
  user-select: all;
}
.modal-foot {
  margin-top: 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
</style>
