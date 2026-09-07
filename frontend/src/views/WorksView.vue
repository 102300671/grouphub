<script setup lang="ts">
import { onMounted, ref } from "vue";
import { RouterLink, useRoute } from "vue-router";
import { extractErrMsg, request } from "@/api/http";
import type { Work, WorkListResponse } from "@/types/api";

const route = useRoute();
const works = ref<Work[]>([]);
const loading = ref(false);
const errorMsg = ref("");
const typeFilter = ref("");
const keyword = ref("");

const TYPE_OPTIONS = [
  { value: "", label: "全部" },
  { value: "novel", label: "小说" },
  { value: "anime", label: "番剧/动漫" },
  { value: "movie", label: "电影" },
  { value: "gallery", label: "图集" },
  { value: "fanwork", label: "同人文" },
  { value: "other", label: "其他" },
];

function typeLabel(t: string) {
  return TYPE_OPTIONS.find((o) => o.value === t)?.label || t;
}

function applyFilter() {
  loadWorks();
}

async function loadWorks() {
  loading.value = true;
  errorMsg.value = "";
  try {
    const params: Record<string, string> = {};
    if (typeFilter.value) params.type = typeFilter.value;
    const kw = keyword.value.trim();
    if (kw) params.keyword = kw;
    const url = `/works/?${new URLSearchParams(params).toString()}`;
    const res = await request<WorkListResponse>({ url, method: "GET" });
    works.value = res.items;
  } catch (e) {
    errorMsg.value = extractErrMsg(e, "加载作品失败");
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  // 支持从 URL query 读取搜索词（机器人消息里的链接跳转）
  const kw = route.query.keyword;
  if (typeof kw === "string" && kw.trim()) {
    keyword.value = kw.trim();
  }
  loadWorks();
});
</script>

<template>
  <section>
    <div class="row-between">
      <div>
        <h2 style="margin: 0">📚 作品库</h2>
        <p class="muted text-sm" style="margin: 6px 0 0">群友共建，按贡献展示支持者 / 推荐者。</p>
      </div>
      <RouterLink to="/works/new" class="btn btn-primary">➕ 上传作品</RouterLink>
    </div>

    <div class="filter-bar mt-4">
      <div class="chips">
        <button
          v-for="opt in TYPE_OPTIONS"
          :key="opt.value"
          class="chip"
          :class="{ 'chip-active': typeFilter === opt.value }"
          type="button"
          @click="typeFilter = opt.value; applyFilter()"
        >
          {{ opt.label }}
        </button>
      </div>
      <input
        v-model="keyword"
        class="input search-input"
        type="search"
        placeholder="搜索标题 / 作者 / 标签…"
        @keyup.enter="applyFilter"
      />
      <button class="btn" type="button" @click="applyFilter">搜索</button>
    </div>

    <div v-if="errorMsg" class="alert alert-error mt-4">{{ errorMsg }}</div>
    <div v-if="loading" class="mt-8 muted text-sm">加载中…</div>
    <div v-else-if="works.length === 0" class="mt-8 card" style="text-align: center">
      <div class="muted">{{ typeFilter || keyword ? "没有符合条件的作品。换个筛选试试～" : "当前还没有作品。等第一个小伙伴上传吧 🥳" }}</div>
    </div>

    <div v-else class="grid-cards mt-4">
      <RouterLink
        v-for="w in works"
        :key="w.id"
        :to="`/works/${w.id}`"
        class="work-card card work-card-link"
      >
        <img v-if="w.cover_url" class="cover cover-img" :src="w.cover_url" :alt="w.title" loading="lazy" />
        <div v-else class="cover" :title="w.title">{{ w.title.slice(0, 1) }}</div>
        <div class="info">
          <div class="row-between">
            <h3 class="title">{{ w.title }}</h3>
            <span class="badge badge-muted">{{ typeLabel(w.type) }}</span>
          </div>
          <p class="muted text-sm synopsis">{{ w.summary || "（暂无简介）" }}</p>
          <div class="meta muted text-sm"><span v-if="w.author">作者：{{ w.author }}</span></div>
        </div>
      </RouterLink>
    </div>
  </section>
</template>

<style scoped>
.filter-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.search-input { width: 260px; max-width: 100%; }
.work-card { display: flex; gap: 14px; padding: 16px; transition: transform 0.15s ease, box-shadow 0.15s ease; }
.work-card:hover { transform: translateY(-2px); box-shadow: var(--shadow-md); }
.work-card-link { color: inherit; text-decoration: none; cursor: pointer; }
.cover { width: 72px; height: 96px; flex-shrink: 0; border-radius: 8px; background: var(--accent-gradient); color: #fff; display: flex; align-items: center; justify-content: center; font-size: 28px; font-weight: 700; box-shadow: 0 8px 20px rgba(79, 70, 229, 0.2); }
.cover.cover-img { object-fit: cover; border: 1px solid var(--color-border); background: #f3f4f6; }
.info { display: flex; flex-direction: column; gap: 6px; min-width: 0; flex: 1; }
.title { margin: 0; font-size: 15px; line-height: 1.4; }
.synopsis { margin: 0; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.meta { margin-top: auto; }
</style>
