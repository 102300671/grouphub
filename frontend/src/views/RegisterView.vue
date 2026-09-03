<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { useUserStore } from "@/stores/user";
import { extractErrMsg } from "@/api/http";

const user = useUserStore();
const router = useRouter();

const qq = ref("");
const nickname = ref("");
const password = ref("");
const password2 = ref("");
const submitting = ref(false);
const errorMsg = ref("");

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
    await user.register({
      qq: q,
      password: password.value,
      nickname: nickname.value.trim() || undefined,
    });
    router.replace("/");
  } catch (e) {
    errorMsg.value = extractErrMsg(e, "注册失败");
  } finally {
    submitting.value = false;
  }
}

onMounted(() => {
  if (user.isLoggedIn) router.replace("/");
});
</script>

<template>
  <main class="auth-page">
    <div class="auth-card card">
      <div class="brand">
        <div class="logo">👭</div>
        <div>
          <h1>加入群资源站</h1>
          <p class="muted text-sm">你的 QQ 必须已经在群里，否则无法注册。</p>
        </div>
      </div>

      <form @submit.prevent="onSubmit">
        <div v-if="errorMsg" class="alert alert-error">{{ errorMsg }}</div>

        <div class="form-item">
          <label for="qq">QQ 号</label>
          <input id="qq" v-model="qq" class="input" type="text" inputmode="numeric" placeholder="用于校验白名单" />
        </div>
        <div class="form-item">
          <label for="nick">昵称（可选）</label>
          <input id="nick" v-model="nickname" class="input" type="text" placeholder="不填则使用群名片" />
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
          {{ submitting ? "注册中…" : "注册并登录" }}
        </button>

        <div class="links">
          <router-link to="/login">已有账号？去登录</router-link>
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
  justify-content: flex-end;
}
</style>
