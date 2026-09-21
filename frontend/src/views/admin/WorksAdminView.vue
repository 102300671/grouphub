<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { extractErrMsg } from "@/api/http";
import { adminClient, worksClient } from "@/api";
import type { Work, WorkStatus } from "@/types/api";

type Tab = "" | "pending" | "published" | "draft";

const works = ref<Work[]>([]);
const loading = ref(false);
const errorMsg = ref("");
const deletingId = ref<number | null>(null);
const statusBusyId = ref<number | null>(null);
const tab = ref<Tab>("pending");
const page = ref(1);
const pageSize = ref(50);
const total = ref(0);
const pendingTotal = ref(0);
const reviewEnabled = ref(false);

const tabs = computed(() => [
  { key: "pending" as Tab, label: `待审核${pendingTotal.value ? `（${pendingTotal.value}）` : ""}` },
  { key: "published" as Tab, label: "已发布" },
  { key: "draft" as Tab, label: "草稿/驳回" },
  { key: "" as Tab, label: "全部" },
]);

async function loadWorks() {
  loading.value = true;
  errorMsg.value = "";
  try {
    const res = await adminClient.listWorks({
      status: tab.value || undefined,
      page: page.value,
      page_size: pageSize.value,
    });
    works.value = res.items;
    total.value = res.total;
    pendingTotal.value = res.pending_total;
    reviewEnabled.value = res.works_require_review;
  } catch (e) {
    errorMsg.value = extractErrMsg(e, "加载作品失败");
  } finally {
    loading.value = false;
  }
}

function switchTab(t: Tab) {
  tab.value = t;
  page.value = 1;
  loadWorks();
}

async function setStatus(w: Work, status: WorkStatus, confirmText?: string) {
  if (confirmText && !window.confirm(confirmText)) return;
  statusBusyId.value = w.id;
  try {
    await adminClient.setWorkStatus(w.id, status);
    await loadWorks();
  } catch (e: any) {
    window.alert(extractErrMsg(e, "操作失败"));
  } finally {
    statusBusyId.value = null;
  }
}

async function removeWork(w: Work) {
  const ok = window.confirm(`确定删除作品「${w.title}」（ID ${w.id}）？文件、链接、评论等将一并级联清理。此操作不可恢复。`);
  if (!ok) return;
  deletingId.value = w.id;
  try {
    await worksClient.remove(w.id);
    works.value = works.value.filter((x) => x.id !== w.id);
  } catch (e: any) {
    window.alert(extractErrMsg(e, "删除失败"));
  } finally {
    deletingId.value = null;
  }
}

function fmtDate(s?: string) {
  return s ? s.slice(0, 16).replace("T", " ") : "—";
}

onMounted(loadWorks);
</script>

<template>
  <section>
    <div class="row-between">
      <div>
        <h2 style="margin: 0">📚 作品管理</h2>
        <p class="muted text-sm" style="margin: 6px 0 0">
          <template v-if="reviewEnabled">审核开关已开启：新作品先进入待审核，通过后公开。</template>
          <template v-else>审核开关已关闭：新提交的作品直接公开发布；历史待审核作品仍可在此处理。</template>
        </p>
      </div>
      <RouterLink class="btn btn-primary" to="/works/new">➕ 新建作品</RouterLink>
    </div>

    <div class="tabs mt-4">
      <button
        v-for="t in tabs"
        :key="t.key || 'all'"
        class="tab-btn"
        :class="{ active: tab === t.key }"
        @click="switchTab(t.key)"
      >
        {{ t.label }}
      </button>
    </div>

    <div v-if="errorMsg" class="alert alert-error mt-4">{{ errorMsg }}</div>
    <div v-if="loading" class="mt-8 muted text-sm">加载中…</div>
    <div v-else class="card table-wrap mt-4">
      <table class="simple">
        <thead>
          <tr><th>ID</th><th>标题</th><th>状态</th><th>类型</th><th>作者</th><th>上传者</th><th>时间</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="w in works" :key="w.id">
            <td class="muted">#{{ w.id }}</td>
            <td><strong>{{ w.title }}</strong></td>
            <td>
              <span :class="['badge', `badge-${w.status || 'published'}`]">
                {{ w.status === "pending" ? "待审核" : w.status === "draft" ? "草稿" : "已发布" }}
              </span>
            </td>
            <td><span class="badge badge-muted">{{ w.type }}</span></td>
            <td>{{ w.author || "—" }}</td>
            <td class="text-sm">{{ w.uploader?.nickname || "—" }}</td>
            <td class="muted text-sm">{{ fmtDate(w.updated_at || w.created_at) }}</td>
            <td>
              <div class="row" style="gap: 4px; justify-content: flex-start; flex-wrap: wrap;">
                <RouterLink class="btn btn-ghost text-sm" :to="`/works/${w.id}`">查看</RouterLink>
                <button
                  v-if="w.status !== 'published'"
                  class="btn btn-primary text-sm"
                  :disabled="statusBusyId === w.id"
                  @click="setStatus(w, 'published')"
                >
                  {{ statusBusyId === w.id ? "处理中…" : "通过" }}
                </button>
                <button
                  v-if="w.status === 'pending'"
                  class="btn btn-warn text-sm"
                  :disabled="statusBusyId === w.id"
                  @click="setStatus(w, 'draft', `驳回「${w.title}」？驳回后作品转为草稿，不再公开展示，上传者仍可在「我的」中看到。`)"
                >
                  驳回
                </button>
                <button
                  v-if="w.status === 'published'"
                  class="btn btn-ghost text-sm"
                  :disabled="statusBusyId === w.id"
                  @click="setStatus(w, 'pending', `把「${w.title}」重新挂起为待审核？挂起后将立即从公开列表隐藏。`)"
                >
                  挂起
                </button>
                <button class="btn btn-danger text-sm" :disabled="deletingId === w.id" @click="removeWork(w)">
                  {{ deletingId === w.id ? "删除中…" : "删除" }}
                </button>
              </div>
            </td>
          </tr>
          <tr v-if="works.length === 0">
            <td colspan="8" class="muted" style="text-align: center">
              {{ tab === "pending" ? "没有待审核的作品 🎉" : "暂无作品" }}
            </td>
          </tr>
        </tbody>
      </table>
      <div class="pagination row-between mt-4">
        <button class="btn" :disabled="page <= 1 || loading" @click="page--; loadWorks()">上一页</button>
        <span class="muted text-sm">第 {{ page }} 页 · 共 {{ total }} 条</span>
        <button class="btn" :disabled="works.length < pageSize || loading" @click="page++; loadWorks()">下一页</button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.table-wrap { overflow-x: auto; }
.tabs {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.tab-btn {
  padding: 6px 14px;
  border: 1px solid var(--border-color, #d1d5db);
  border-radius: 999px;
  background: transparent;
  cursor: pointer;
  font-size: 13px;
  color: inherit;
}
.tab-btn.active {
  background: #2563eb;
  border-color: #2563eb;
  color: #fff;
}
.badge-pending { background: #fef3c7; color: #92400e; }
.badge-published { background: #dcfce7; color: #166534; }
.badge-draft { background: #e5e7eb; color: #374151; }
.btn-danger { background: #dc2626 !important; color: #fff !important; border-color: #dc2626 !important; }
.btn-danger:hover { background: #b91c1c !important; }
.btn-warn { background: #d97706 !important; color: #fff !important; border-color: #d97706 !important; }
.btn-warn:hover { background: #b45309 !important; }
.btn-danger[disabled], .btn-warn[disabled], .btn-primary[disabled], .btn-ghost[disabled] { opacity: 0.6; cursor: not-allowed; }
.text-sm { font-size: 12px !important; }
@media (max-width: 720px) {
  table.simple { min-width: 720px; }
}
</style>
