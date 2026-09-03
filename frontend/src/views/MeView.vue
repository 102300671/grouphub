<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useUserStore } from "@/stores/user";
import { extractErrMsg, request } from "@/api/http";

const user = useUserStore();
const tip = ref("");

/**
 * MVP 后端目前没有「/auth/me 详情（含我收藏/关系的作品）」接口，
 * 这里先用 /auth/me + 占位作品列表的形式，后续可无缝扩展。
 */
async function ensure() {
  try {
    const me = await user.ensureMe();
    tip.value = me?.role === "admin"
      ? "你是管理员：右上角可随时切换到「管理模式」做站点管理。"
      : "欢迎回来！这里会逐步汇总你标记过的作品 / 评论 / 同人。";
  } catch (e) {
    tip.value = extractErrMsg(e, "获取个人资料失败");
  }
}
onMounted(ensure);
</script>

<template>
  <section class="me">
    <div class="card profile-card">
      <div class="avatar">{{ user.current?.nickname?.[0] ?? "?" }}</div>
      <div class="pinfo">
        <div class="row">
          <h2 style="margin: 0">{{ user.current?.nickname }}</h2>
          <span v-if="user.role === 'admin'" class="badge badge-admin">管理员</span>
          <span v-else class="badge badge-member">群友</span>
        </div>
        <div class="muted text-sm">QQ：{{ user.current?.qq }} · 注册时间 {{ user.current?.created_at?.slice(0, 10) ?? "—" }}</div>
        <div class="mt-4">
          <div class="alert alert-info" v-if="tip">{{ tip }}</div>
        </div>
      </div>
    </div>

    <section class="mt-8">
      <h3>🎯 我参与的作品（占位，后续接入 /user-works 聚合）</h3>
      <div class="card muted text-sm">暂无数据。</div>
    </section>
  </section>
</template>

<style scoped>
.profile-card {
  display: flex;
  gap: 18px;
  align-items: center;
}
.avatar {
  width: 64px;
  height: 64px;
  border-radius: 50%;
  background: var(--accent-gradient);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  font-weight: 700;
  flex-shrink: 0;
  box-shadow: 0 10px 20px rgba(79, 70, 229, 0.18);
}
.pinfo {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
}
</style>
