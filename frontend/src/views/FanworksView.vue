<script setup lang="ts">
import { onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { fanworksClient } from "@/api";
import { extractErrMsg } from "@/api/http";
import type { Fanwork } from "@/types/api";

const fanworks = ref<Fanwork[]>([]);
const loading = ref(false);
const errorMsg = ref("");
const keyword = ref("");
const mineOnly = ref(false);

async function load() {
  loading.value = true;
  errorMsg.value = "";
  try {
    const res = await fanworksClient.list({
      keyword: keyword.value.trim() || undefined,
      mine: mineOnly.value || undefined,
      page_size: 50,
    });
    fanworks.value = res.items;
  } catch (e) {
    errorMsg.value = extractErrMsg(e, "加载同人作品失败");
  } finally {
    loading.value = false;
  }
}

function toggleMine() {
  mineOnly.value = !mineOnly.value;
  load();
}

function fmtDate(s: string) {
  if (!s) return "";
  return s.replace("T", " ").slice(0, 16);
}

onMounted(load);
</script>

<template>
  <section>
    <div class="row-between">
      <div>
        <h2 style="margin: 0">🎨 同人创作</h2>
        <p class="muted text-sm" style="margin: 6px 0 0">群友二创区：同人文、图、影音……基于馆内作品或自由创作。</p>
      </div>
      <RouterLink to="/fanworks/new" class="btn btn-primary">✍️ 发布同人</RouterLink>
    </div>

    <!-- 搜索 / 筛选 -->
    <div class="filter-bar mt-4">
      <input
        v-model="keyword"
        class="input search-input"
        type="search"
        placeholder="搜索同人作品标题…"
        @keyup.enter="load"
      />
      <button class="btn" type="button" @click="load">搜索</button>
      <button
        class="btn"
        type="button"
        :class="{ 'btn-primary': mineOnly }"
        @click="toggleMine"
      >
        {{ mineOnly ? "✓ 我的创作" : "我的创作（含草稿）" }}
      </button>
    </div>

    <div v-if="errorMsg" class="alert alert-error mt-4">{{ errorMsg }}</div>
    <div v-if="loading" class="mt-8 muted text-sm">加载中…</div>
    <div v-else-if="fanworks.length === 0" class="mt-8 card" style="text-align: center">
      <div class="muted">
        {{ keyword ? "没有符合条件的同人作品。" : mineOnly ? "你还没有发布过同人作品，来发第一个吧 🚀" : "还没有同人作品，来做第一个创作者吧 🚀" }}
      </div>
    </div>

    <div v-else class="fw-grid">
      <RouterLink
        v-for="fw in fanworks"
        :key="fw.id"
        :to="`/fanworks/${fw.id}`"
        class="fw-card card"
      >
        <div class="fw-cover">
          <img v-if="fw.cover_url" :src="fw.cover_url" :alt="fw.title" />
          <span v-else class="fw-cover-ph">🎨</span>
          <span v-if="fw.status === 'draft'" class="badge badge-draft">草稿</span>
        </div>
        <div class="fw-body">
          <h3 class="fw-title">{{ fw.title }}</h3>
          <div class="fw-tags">
            <span v-if="fw.category" class="badge badge-muted">{{ fw.category }}</span>
            <span v-if="fw.work_title" class="badge badge-muted">原作：{{ fw.work_title }}</span>
          </div>
          <div class="muted text-sm fw-meta">
            <span>{{ fw.author?.nickname || `群友${fw.author?.qq ?? ""}` }}</span>
            <span> · {{ fmtDate(fw.created_at) }}</span>
          </div>
        </div>
      </RouterLink>
    </div>
  </section>
</template>

<style scoped>
.filter-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.search-input { width: 240px; max-width: 100%; }
.fw-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 14px;
  margin-top: 16px;
}
.fw-card {
  display: flex;
  flex-direction: column;
  padding: 0;
  overflow: hidden;
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.fw-card:hover { transform: translateY(-2px); box-shadow: var(--shadow-md); }
.fw-cover {
  position: relative;
  height: 130px;
  background: var(--accent-gradient);
  display: flex;
  align-items: center;
  justify-content: center;
}
.fw-cover img { width: 100%; height: 100%; object-fit: cover; }
.fw-cover-ph { font-size: 40px; opacity: 0.85; }
.badge-draft {
  position: absolute;
  top: 8px;
  right: 8px;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
}
.fw-body { padding: 12px 14px 14px; display: flex; flex-direction: column; gap: 8px; }
.fw-title { margin: 0; font-size: 15px; line-height: 1.4; }
.fw-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.fw-meta { display: flex; flex-wrap: wrap; gap: 4px; }
@media (max-width: 720px) {
  .search-input { width: 100%; }
  .fw-grid {
    grid-template-columns: repeat(auto-fill, minmax(min(200px, 100%), 1fr));
  }
  .fw-cover { height: 110px; }
}
</style>
