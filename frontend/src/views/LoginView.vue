<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useUserStore } from "@/stores/user";
import { extractErrMsg } from "@/api/http";

const user = useUserStore();
const router = useRouter();
const route = useRoute();

const qq = ref("");
const password = ref("");
const submitting = ref(false);
const errorMsg = ref("");

async function onSubmit() {
  errorMsg.value = "";
  if (!qq.value || !password.value) {
    errorMsg.value = "QQ 和密码都不能为空";
    return;
  }
  submitting.value = true;
  try {
    await user.login(qq.value.trim(), password.value);
    const redirect = typeof route.query.redirect === "string" ? route.query.redirect : "/";
    router.replace(redirect);
  } catch (e) {
    errorMsg.value = extractErrMsg(e, "登录失败");
  } finally {
    submitting.value = false;
  }
}

onMounted(() => {
  // 已登录的直接进首页
  if (user.isLoggedIn) router.replace("/");
});
</script>

<template>
  <main class="auth-page">
    <div class="auth-card card">
      <div class="brand">
        <div class="logo">🌸</div>
        <div>
          <h1>群资源站</h1>
          <p class="muted text-sm">百合（GL）群友实名登录 · 与 QQ 群成员白名单联动</p>
        </div>
      </div>

      <form @submit.prevent="onSubmit">
        <div v-if="errorMsg" class="alert alert-error">{{ errorMsg }}</div>

        <div class="form-item">
          <label for="qq">QQ 号</label>
          <input id="qq" v-model="qq" class="input" type="text" inputmode="numeric" autocomplete="username" placeholder="你的 QQ 号，必须在群里" />
        </div>
        <div class="form-item">
          <label for="pwd">密码</label>
          <input id="pwd" v-model="password" class="input" type="password" autocomplete="current-password" placeholder="密码" />
        </div>

        <button class="btn btn-primary btn-block" type="submit" :disabled="submitting">
          {{ submitting ? "登录中…" : "登录" }}
        </button>

        <div class="links">
          <router-link to="/register">还没有账号？去注册</router-link>
          <span class="muted text-sm">验证码登录占位</span>
        </div>
      </form>
    </div>
  </main>
</template>

<style scoped>
.auth-page {
  min-height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
  background:
    radial-gradient(1200px 600px at 10% -20%, rgba(79, 70, 229, 0.12), transparent 60%),
    radial-gradient(900px 500px at 110% 110%, rgba(124, 58, 237, 0.1), transparent 60%),
    var(--color-bg);
}
.auth-card {
  width: 100%;
  max-width: 420px;
  box-shadow: var(--shadow-md);
}
.brand {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 22px;
}
.brand h1 {
  margin: 0;
  font-size: 22px;
}
.brand p {
  margin: 4px 0 0;
}
.logo {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  background: var(--accent-gradient);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 26px;
  box-shadow: 0 10px 24px rgba(79, 70, 229, 0.22);
}
.links {
  margin-top: 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
</style>
