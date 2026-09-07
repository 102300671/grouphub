<script setup lang="ts">
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useUserStore } from "@/stores/user";
import { authClient, extractErrMsg } from "@/api/http";

const user = useUserStore();
const router = useRouter();
const route = useRoute();

const redirect = typeof route.query.redirect === "string" ? route.query.redirect : "/";

/** 密码登录 或 验证码（QQ 私聊）登录 */
const mode = ref<"password" | "code">("password");

// ------- 密码登录 -------
const qq = ref("");
const password = ref("");

// ------- 验证码登录 -------
const codeQQ = ref("");
const code = ref("");
const codeSent = ref(false);
const sendingCode = ref(false);
const codeHint = ref("");

const submitting = ref(false);
const errorMsg = ref("");

async function onSubmit() {
  errorMsg.value = "";
  if (!qq.value.trim() || !password.value) {
    errorMsg.value = "QQ 和密码都不能为空";
    return;
  }
  submitting.value = true;
  try {
    await user.login(qq.value.trim(), password.value);
    router.replace(redirect);
  } catch (e) {
    errorMsg.value = extractErrMsg(e, "登录失败");
  } finally {
    submitting.value = false;
  }
}

async function onSendCode() {
  errorMsg.value = "";
  const q = codeQQ.value.trim();
  if (!q) {
    errorMsg.value = "请先填写 QQ 号";
    return;
  }
  sendingCode.value = true;
  codeHint.value = "";
  try {
    const res = await authClient.sendCode(q);
    const details = res.details as Record<string, unknown> | undefined;
    const debugCode = details?.code as string | undefined;
    codeSent.value = true;
    if (debugCode) {
      // 机器人离线时的调试降级：后端把 code 直接返回在 details 里
      code.value = debugCode;
      codeHint.value = `⚠️ 机器人不在线（调试模式），验证码已在下方自动填入：${debugCode}`;
    } else {
      codeHint.value = "验证码已私聊发送到你 QQ，请到 QQ 里查看后填写";
    }
  } catch (e) {
    errorMsg.value = extractErrMsg(e, "发送失败");
  } finally {
    sendingCode.value = false;
  }
}

async function onCodeSubmit() {
  errorMsg.value = "";
  const q = codeQQ.value.trim();
  const c = code.value.trim();
  if (!q || !c) {
    errorMsg.value = "QQ 和验证码都不能为空";
    return;
  }
  submitting.value = true;
  try {
    await user.loginByCode(q, c);
    router.replace(redirect);
  } catch (e) {
    errorMsg.value = extractErrMsg(e, "登录失败");
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <main class="auth-page">
    <div class="auth-card card">
      <div class="brand">
        <div class="logo">👭</div>
        <div>
          <h1>群资源站</h1>
          <p class="muted text-sm">百合（GL）群友实名登录 · 与 QQ 群成员白名单联动</p>
        </div>
      </div>

      <!-- 模式切换 -->
      <div class="tabs">
        <button type="button" class="tab" :class="{ active: mode === 'password' }" @click="mode = 'password'">密码登录</button>
        <button type="button" class="tab" :class="{ active: mode === 'code' }" @click="mode = 'code'">验证码登录</button>
      </div>

      <div v-if="errorMsg" class="alert alert-error">{{ errorMsg }}</div>

      <!-- 密码登录 -->
      <form v-if="mode === 'password'" @submit.prevent="onSubmit">
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
      </form>

      <!-- 验证码登录：QQ 私聊收码，未注册的 QQ 首次登录会自动建号 -->
      <form v-else @submit.prevent="onCodeSubmit">
        <div class="form-item">
          <label for="code-qq">QQ 号</label>
          <input id="code-qq" v-model="codeQQ" class="input" type="text" inputmode="numeric" placeholder="你的 QQ 号，必须在群里" />
        </div>
        <div class="form-item">
          <label for="code">验证码</label>
          <div class="code-row">
            <input id="code" v-model="code" class="input" type="text" inputmode="numeric" maxlength="6" placeholder="6 位验证码" style="flex: 1;" />
            <button type="button" class="btn btn-ghost" :disabled="sendingCode || !codeQQ.trim()" @click="onSendCode">
              {{ sendingCode ? "发送中…" : codeSent ? "重新发送" : "发送验证码" }}
            </button>
          </div>
        </div>
        <p v-if="codeHint" class="text-sm muted" style="margin: 0 0 10px;">{{ codeHint }}</p>
        <button class="btn btn-primary btn-block" type="submit" :disabled="submitting">
          {{ submitting ? "登录中…" : "用验证码登录" }}
        </button>
      </form>

      <div class="links">
        <router-link to="/register">还没有账号？去注册</router-link>
        <span class="muted text-sm">验证码 = QQ 私聊接收，自动注册</span>
      </div>
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
    radial-gradient(1200px 600px at 10% -20%, rgba(219, 39, 119, 0.14), transparent 60%),
    radial-gradient(900px 500px at 110% 110%, rgba(168, 85, 247, 0.16), transparent 60%),
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
  margin-bottom: 18px;
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
  box-shadow: 0 10px 24px rgba(219, 39, 119, 0.22);
}
.tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}
.tab {
  flex: 1;
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid var(--color-border);
  background: transparent;
  color: var(--color-muted);
  font-size: 13px;
  cursor: pointer;
}
.tab.active {
  background: var(--accent-gradient);
  color: #fff;
  border-color: transparent;
}
.code-row {
  display: flex;
  gap: 8px;
  align-items: center;
}
.links {
  margin-top: 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
@media (max-width: 480px) {
  .auth-page {
    padding: 24px 14px;
  }
  .links {
    flex-wrap: wrap;
    justify-content: center;
  }
}
</style>