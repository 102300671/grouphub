<script setup lang="ts">
import { ref, watch } from "vue";
import { useUserStore } from "@/stores/user";
import { authClient, extractErrMsg } from "@/api/http";

const props = defineProps<{
  visible: boolean;
  /** 验证码登录自动注册时后端返回的一次性随机密码（仅当次可见，用于直接展示并引导修改） */
  generatedPassword?: string | null;
}>();

const emit = defineEmits<{ (e: "close"): void }>();

const user = useUserStore();

const mode = ref<"password" | "code">("password");
const oldPassword = ref("");
const code = ref("");
const newPassword = ref("");
const confirmPassword = ref("");
const codeSent = ref(false);
const sendingCode = ref(false);
const codeHint = ref("");
const submitting = ref(false);
const errorMsg = ref("");
const successMsg = ref("");

watch(
  () => props.visible,
  (v) => {
    if (v) {
      // 每次打开重置表单
      mode.value = "password";
      oldPassword.value = "";
      code.value = "";
      newPassword.value = "";
      confirmPassword.value = "";
      codeSent.value = false;
      sendingCode.value = false;
      codeHint.value = "";
      submitting.value = false;
      errorMsg.value = "";
      successMsg.value = "";
    }
  },
);

function copyGeneratedPwd() {
  if (props.generatedPassword) {
    navigator.clipboard?.writeText(props.generatedPassword).catch(() => {});
  }
}

async function onSendCode() {
  errorMsg.value = "";
  if (!user.current?.qq) return;
  sendingCode.value = true;
  codeHint.value = "";
  try {
    const res = await authClient.sendCode(user.current.qq);
    const details = res.details as Record<string, unknown> | undefined;
    const debugCode = details?.code as string | undefined;
    codeSent.value = true;
    if (debugCode) {
      code.value = debugCode;
      codeHint.value = `⚠️ 机器人不在线（调试模式），验证码已自动填入：${debugCode}`;
    } else {
      codeHint.value = "验证码已私聊发送到你 QQ，请到 QQ 里查看后填写";
    }
  } catch (e) {
    errorMsg.value = extractErrMsg(e, "发送失败");
  } finally {
    sendingCode.value = false;
  }
}

async function onSubmit() {
  errorMsg.value = "";
  successMsg.value = "";

  if (newPassword.value.length < 6) {
    errorMsg.value = "新密码至少 6 位";
    return;
  }
  if (newPassword.value !== confirmPassword.value) {
    errorMsg.value = "两次输入的新密码不一致";
    return;
  }
  if (mode.value === "code" && !code.value.trim()) {
    errorMsg.value = "请先获取并填写验证码";
    return;
  }
  if (mode.value === "password" && !oldPassword.value && !props.generatedPassword) {
    errorMsg.value = "请填写当前密码";
    return;
  }

  submitting.value = true;
  try {
    const payload: { old_password?: string; code?: string; new_password: string } = {
      new_password: newPassword.value,
    };
    if (mode.value === "code") {
      payload.code = code.value.trim();
    } else {
      // 自动注册当次：直接用后端返回的随机密码作为旧密码校验
      payload.old_password = oldPassword.value || props.generatedPassword || undefined;
    }
    const res = await user.changePassword(payload);
    successMsg.value = res.message || "密码已修改";
    oldPassword.value = "";
    code.value = "";
    newPassword.value = "";
    confirmPassword.value = "";
  } catch (e) {
    errorMsg.value = extractErrMsg(e, "修改失败");
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <div v-if="visible" class="modal-mask" @click.self="emit('close')">
    <div class="modal card">
      <div class="modal-head">
        <h3>🔑 设置密码</h3>
        <button class="modal-close" type="button" @click="emit('close')">✕</button>
      </div>

      <!-- 验证码自动注册：展示一次性随机密码并提示修改/记住 -->
      <div v-if="generatedPassword" class="alert alert-warning">
        <p class="text-sm" style="margin: 0 0 8px;">
          你的账号刚通过<strong>验证码自动创建</strong>，系统生成了一个随机密码：
        </p>
        <div class="pwd-box">
          <span class="pwd-text">{{ generatedPassword }}</span>
          <button class="btn btn-ghost btn-sm" type="button" @click="copyGeneratedPwd">复制</button>
        </div>
        <p class="text-sm" style="margin: 8px 0 0;">
          该密码<strong>仅本次登录显示一次</strong>。若要使用密码登录，请<strong>修改</strong>为你自己的密码，或<strong>记住</strong>这个随机密码。
        </p>
      </div>

      <div v-if="errorMsg" class="alert alert-error">{{ errorMsg }}</div>
      <div v-if="successMsg" class="alert alert-success">{{ successMsg }}</div>

      <!-- 校验方式：仅在无随机密码时显示（「我的」页面入口），自动注册场景直接改 -->
      <template v-if="!generatedPassword">
        <div class="tabs">
          <button type="button" class="tab" :class="{ active: mode === 'password' }" @click="mode = 'password'">当前密码</button>
          <button type="button" class="tab" :class="{ active: mode === 'code' }" @click="mode = 'code'">验证码（忘记密码）</button>
        </div>

        <div v-if="mode === 'password'" class="form-item">
          <label for="cp-old">当前密码</label>
          <input id="cp-old" v-model="oldPassword" type="password" class="input" autocomplete="current-password" placeholder="输入当前密码" />
        </div>

        <div v-else class="form-item">
          <label for="cp-code">验证码（发送到你的 QQ 私聊）</label>
          <div class="code-row">
            <input id="cp-code" v-model="code" type="text" inputmode="numeric" maxlength="6" class="input" placeholder="6 位验证码" style="flex: 1;" />
            <button type="button" class="btn btn-ghost" :disabled="sendingCode" @click="onSendCode">
              {{ sendingCode ? "发送中…" : codeSent ? "重新发送" : "发送验证码" }}
            </button>
          </div>
          <p v-if="codeHint" class="text-sm muted" style="margin: 6px 0 0;">{{ codeHint }}</p>
        </div>
      </template>

      <div class="form-item">
        <label for="cp-new">新密码</label>
        <input id="cp-new" v-model="newPassword" type="password" class="input" autocomplete="new-password" placeholder="至少 6 位" />
      </div>
      <div class="form-item">
        <label for="cp-confirm">确认新密码</label>
        <input id="cp-confirm" v-model="confirmPassword" type="password" class="input" autocomplete="new-password" placeholder="再次输入新密码" />
      </div>

      <div class="modal-foot">
        <button type="button" class="btn btn-ghost" @click="emit('close')">取消</button>
        <button type="button" class="btn btn-primary" :disabled="submitting" @click="onSubmit">
          {{ submitting ? "提交中…" : "保存新密码" }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-mask {
  position: fixed;
  inset: 0;
  z-index: 60;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.modal {
  width: 100%;
  max-width: 440px;
  box-shadow: var(--shadow-md);
}
.modal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}
.modal-head h3 {
  margin: 0;
}
.modal-close {
  border: none;
  background: transparent;
  color: var(--color-muted);
  font-size: 16px;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 6px;
}
.modal-close:hover {
  background: var(--color-bg-soft, #f5f5f7);
}
.modal-foot {
  margin-top: 14px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
}
.tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}
.tab {
  flex: 1;
  padding: 7px 10px;
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
.pwd-box {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  border-radius: 10px;
  background: rgba(217, 119, 6, 0.08);
  border: 1px dashed rgba(217, 119, 6, 0.4);
}
.pwd-text {
  font-size: 18px;
  font-weight: 700;
  letter-spacing: 1px;
  font-family: var(--font-mono, monospace);
  word-break: break-all;
}
.alert-warning {
  background: #fffbeb;
  color: #b45309;
  border: 1px solid #fde68a;
}
</style>
