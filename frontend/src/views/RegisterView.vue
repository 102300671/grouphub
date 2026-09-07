<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { useUserStore } from "@/stores/user";
import { extractErrMsg } from "@/api/http";

const user = useUserStore();
const router = useRouter();

// 第一步：表单；第二步：展示绑定码并等待用户发给机器人验证
const step = ref<"form" | "wait" | "done">("form");

const qq = ref("");
const nickname = ref("");
const password = ref("");
const password2 = ref("");
const submitting = ref(false);
const errorMsg = ref("");

// 第二步状态
const bindCode = ref("");
const expiresMinutes = ref(10);
const waitSeconds = ref(0);
const verifying = ref(false);
const successMsg = ref("");
let pollTimer: ReturnType<typeof setInterval> | null = null;

const countdownText = computed(() => {
  const m = Math.floor(waitSeconds.value / 60);
  const s = waitSeconds.value % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
});

async function onSubmit() {
  errorMsg.value = "";
  const q = qq.value.trim();
  if (!/^\d{5,15}$/.test(q)) {
    errorMsg.value = "请输入正确的 QQ 号（5~15 位数字）";
    return;
  }
  if (password.value.length < 6) {
    errorMsg.value = "密码至少 6 位";
    return;
  }
  if (password.value !== password2.value) {
    errorMsg.value = "两次密码输入不一致";
    return;
  }
  submitting.value = true;
  try {
    const out = await user.register({
      qq: q,
      password: password.value,
      nickname: nickname.value.trim() || undefined,
    });
    bindCode.value = out.code;
    expiresMinutes.value = out.expires_in_minutes || 10;
    waitSeconds.value = (out.expires_in_minutes || 10) * 60;
    step.value = "wait";
    startPolling();
  } catch (e) {
    errorMsg.value = extractErrMsg(e, "注册失败");
  } finally {
    submitting.value = false;
  }
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(async () => {
    if (waitSeconds.value > 0) waitSeconds.value -= 1;
    if (verifying.value) return;
    verifying.value = true;
    try {
      const st = await user.registerStatus(qq.value.trim());
      if (!st.pending) {
        stopPolling();
        await autoLogin();
      }
    } catch {
      // 轮询失败忽略，下个周期重试
    } finally {
      verifying.value = false;
    }
  }, 3000);
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function autoLogin() {
  step.value = "done";
  try {
    await user.login(qq.value.trim(), password.value);
    router.replace("/");
  } catch (e) {
    // 自动登录失败（极少见）：提示去登录页手动登录
    errorMsg.value = `验证已完成，但自动登录失败：${extractErrMsg(e, "请手动登录")}`;
  }
}

function backToForm() {
  stopPolling();
  step.value = "form";
  errorMsg.value = "";
}

function copyCode() {
  navigator.clipboard?.writeText(bindCode.value).catch(() => {});
}

onMounted(() => {
  if (user.isLoggedIn) router.replace("/");
});

onBeforeUnmount(stopPolling);
</script>

<template>
  <main class="auth-page">
    <div class="auth-card card">
      <!-- 第一步：填写注册信息 -->
      <template v-if="step === 'form'">
        <div class="brand">
          <div class="logo">👭</div>
          <div>
            <h1>加入群资源站</h1>
            <p class="muted text-sm">注册后需要把验证码发给机器人完成验证。</p>
          </div>
        </div>

        <form @submit.prevent="onSubmit">
          <div v-if="errorMsg" class="alert alert-error">{{ errorMsg }}</div>

          <div class="form-item">
            <label for="qq">QQ 号</label>
            <input id="qq" v-model="qq" class="input" type="text" inputmode="numeric" placeholder="你的真实 QQ 号" />
          </div>
          <div class="form-item">
            <label for="nick">昵称（可选）</label>
            <input id="nick" v-model="nickname" class="input" type="text" placeholder="留空则使用 QQ 用户名" />
            <p class="field-hint muted text-sm">想使用群内名称（群名片）？请在此手动填写</p>
          </div>
          <div class="form-item">
            <label for="pwd">密码</label>
            <input id="pwd" v-model="password" class="input" type="password" placeholder="至少 6 位" />
          </div>
          <div class="form-item">
            <label for="pwd2">确认密码</label>
            <input id="pwd2" v-model="password2" class="input" type="password" />
          </div>

          <button class="btn btn-primary btn-block" type="submit" :disabled="submitting">
            {{ submitting ? "注册中…" : "获取验证码" }}
          </button>

          <div class="links">
            <router-link to="/login">已有账号？去登录</router-link>
          </div>
        </form>
      </template>

      <!-- 第二步：把验证码发给机器人 -->
      <template v-else-if="step === 'wait'">
        <div class="brand">
          <div class="logo">📮</div>
          <div>
            <h1>把验证码发给机器人</h1>
            <p class="muted text-sm">验证通过后自动进入站点</p>
          </div>
        </div>

        <div v-if="errorMsg" class="alert alert-error">{{ errorMsg }}</div>

        <div class="code-box">
          <span class="code-text">{{ bindCode }}</span>
          <button class="btn btn-ghost btn-sm" type="button" @click="copyCode">复制</button>
        </div>

        <ol class="steps muted">
          <li><b>推荐：在群里</b> @机器人 发送下方命令（主要方式）：</li>
        </ol>
        <div class="cmd-box">@机器人 绑定 {{ bindCode }}</div>
        <ol class="steps muted" start="2">
          <li>私聊发送需要机器人为你的好友（私聊名额有限，仅按需分配，优先群内操作）</li>
          <li>等待机器人回复「验证通过」…（本页会自动检测）</li>
        </ol>

        <div class="wait-foot">
          <span class="muted text-sm">有效期 {{ countdownText }}</span>
          <button class="btn btn-ghost btn-sm" type="button" :disabled="waitSeconds > 0" @click="backToForm">
            {{ waitSeconds > 0 ? "等待验证中…" : "验证码已过期，重新获取" }}
          </button>
        </div>
      </template>

      <!-- 第三步：验证完成 -->
      <template v-else>
        <div class="brand">
          <div class="logo">✅</div>
          <div>
            <h1>验证通过</h1>
            <p class="muted text-sm">正在自动登录…</p>
          </div>
        </div>
        <div v-if="errorMsg" class="alert alert-error">
          {{ errorMsg }}
          <router-link to="/login">去登录页</router-link>
        </div>
      </template>
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
  justify-content: flex-end;
}
.code-box {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  border-radius: 12px;
  background: rgba(79, 70, 229, 0.08);
  border: 1px dashed rgba(79, 70, 229, 0.4);
}
.code-text {
  font-size: 30px;
  font-weight: 700;
  letter-spacing: 6px;
  font-family: var(--font-mono, monospace);
}
.cmd-box {
  padding: 10px 14px;
  border-radius: 10px;
  background: var(--color-bg-soft, #f5f5f7);
  border: 1px solid var(--color-border, #e5e5ea);
  font-family: var(--font-mono, monospace);
  margin: 8px 0 12px;
  user-select: all;
}
.steps {
  margin: 6px 0;
  padding-left: 20px;
  line-height: 1.9;
}
.field-hint {
  margin-top: 4px;
}
.wait-foot {
  margin-top: 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
@media (max-width: 480px) {
  .auth-page {
    padding: 24px 14px;
  }
  .code-text {
    font-size: 24px;
    letter-spacing: 4px;
  }
  .wait-foot {
    flex-wrap: wrap;
    gap: 8px;
  }
}
</style>
