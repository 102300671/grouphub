<script setup lang="ts">
import { onMounted, ref } from "vue";
import { extractErrMsg, request } from "@/api/http";
import type { AdminSummary } from "@/types/api";

const summary = ref<AdminSummary | null>(null);
const loading = ref(false);
const err = ref("");
const syncTip = ref("");
const syncLoading = ref(false);

async function loadSummary() {
  loading.value = true;
  err.value = "";
  try {
    summary.value = await request<AdminSummary>({ url: "/admin/summary", method: "GET" });
  } catch (e) {
    err.value = extractErrMsg(e, "获取仪表盘数据失败");
  } finally {
    loading.value = false;
  }
}

async function triggerSync() {
  syncLoading.value = true;
  syncTip.value = "";
  try {
    const res = await request<{ ok: boolean; message?: string; note?: string }>({
      url: "/admin/trigger_member_sync",
      method: "POST",
    });
    syncTip.value = [res.message, res.note].filter(Boolean).join(" — ");
  } catch (e) {
    syncTip.value = extractErrMsg(e, "触发失败");
  } finally {
    syncLoading.value = false;
  }
}

onMounted(loadSummary);
</script>

<template>
  <section>
    <div class="row-between">
      <div>
        <h2 style="margin: 0">📊 仪表盘</h2>
        <p class="muted text-sm" style="margin: 6px 0 0">站点核心指标概览。</p>
      </div>
      <button class="btn btn-primary" :disabled="syncLoading" @click="triggerSync">
        {{ syncLoading ? "请求中…" : "🔄 同步群成员" }}
      </button>
    </div>
    <div v-if="syncTip" class="alert alert-info mt-4">{{ syncTip }}</div>

    <div v-if="loading" class="mt-8 muted text-sm">加载中…</div>
    <div v-else-if="err" class="mt-8 alert alert-error">{{ err }}</div>
    <template v-else-if="summary">
      <div class="grid-cards mt-4">
        <div class="stat-card card">
          <div class="label muted text-sm">注册用户</div>
          <div class="value">{{ summary.counts.users }}</div>
          <div class="note muted text-sm">白名单活跃 {{ summary.counts.group_members_active }} 人</div>
        </div>
        <div class="stat-card card">
          <div class="label muted text-sm">作品数</div>
          <div class="value">{{ summary.counts.works }}</div>
          <div class="note muted text-sm">当前「关系人数阈值」：{{ summary.show_relation_threshold }}</div>
        </div>
        <div class="stat-card card">
          <div class="label muted text-sm">评论 / 话题 / 同人</div>
          <div class="value">{{ summary.counts.reviews }} / {{ summary.counts.topics }} / {{ summary.counts.fanworks }}</div>
          <div class="note muted text-sm">后续插件 #5/#6/#7 会陆续注入数据</div>
        </div>
        <div class="stat-card card">
          <div class="label muted text-sm">管理员来源（.env ADMIN_QQS）</div>
          <div class="value">{{ summary.admin_count_in_env }} 人</div>
          <div class="note muted text-sm" style="overflow-wrap: anywhere">
            {{ summary.current_admin_qqs.join(", ") || "未配置（请用 scripts/make_admin.py 兜底）" }}
          </div>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.stat-card .label {
  margin-bottom: 6px;
}
.stat-card .value {
  font-size: 30px;
  font-weight: 800;
  background: var(--accent-gradient);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  line-height: 1.2;
}
.stat-card .note {
  margin-top: 10px;
}
</style>
