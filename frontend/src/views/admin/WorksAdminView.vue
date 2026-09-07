<script setup lang="ts">
import { onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { extractErrMsg, request } from "@/api/http";
import { worksClient } from "@/api";
import type { Work, WorkListResponse } from "@/types/api";

const works = ref<Work[]>([]);
const loading = ref(false);
const errorMsg = ref("");
const deletingId = ref<number | null>(null);

async function loadWorks() {
  loading.value = true;
  errorMsg.value = "";
  try {
    const res = await request<WorkListResponse>({ url: "/works/", method: "GET", params: { page: 1, page_size: 100 } });
    works.value = res.items;
  } catch (e) {
    errorMsg.value = extractErrMsg(e, "加载作品失败");
  } finally {
    loading.value = false;
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

onMounted(loadWorks);
</script>

<template>
  <section>
    <div class="row-between">
      <div>
        <h2 style="margin: 0">📚 作品管理</h2>
        <p class="muted text-sm" style="margin: 6px 0 0">作品审核、标签和外链管理将在此扩展。</p>
      </div>
      <button class="btn btn-primary" disabled>➕ 新建作品（占位）</button>
    </div>

    <div v-if="errorMsg" class="alert alert-error mt-4">{{ errorMsg }}</div>
    <div v-if="loading" class="mt-8 muted text-sm">加载中…</div>
    <div v-else class="card table-wrap mt-4">
      <table class="simple">
        <thead><tr><th>ID</th><th>标题</th><th>类型</th><th>作者</th><th>更新时间</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="w in works" :key="w.id">
            <td class="muted">#{{ w.id }}</td>
            <td><strong>{{ w.title }}</strong></td>
            <td><span class="badge badge-muted">{{ w.type }}</span></td>
            <td>{{ w.author || "—" }}</td>
            <td class="muted text-sm">{{ w.updated_at?.slice(0, 16).replace("T", " ") }}</td>
            <td>
              <div class="row" style="gap: 4px; justify-content: flex-start;">
                <RouterLink class="btn btn-ghost text-sm" :to="`/works/${w.id}`">查看</RouterLink>
                <button class="btn btn-danger text-sm" :disabled="deletingId === w.id" @click="removeWork(w)">
                  {{ deletingId === w.id ? "删除中…" : "删除" }}
                </button>
              </div>
            </td>
          </tr>
          <tr v-if="works.length === 0"><td colspan="6" class="muted" style="text-align: center">暂无作品</td></tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.table-wrap { overflow-x: auto; }
.btn-danger { background: #dc2626 !important; color: #fff !important; border-color: #dc2626 !important; }
.btn-danger:hover { background: #b91c1c !important; }
.btn-danger[disabled] { opacity: 0.6; cursor: not-allowed; }
.text-sm { font-size: 12px !important; }
@media (max-width: 720px) {
  table.simple { min-width: 560px; }
}
</style>
