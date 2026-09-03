<script setup lang="ts">
import { onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { extractErrMsg, request } from "@/api/http";
import type { Work, WorkListResponse } from "@/types/api";

const works = ref<Work[]>([]);
const loading = ref(false);
const err = ref("");
const siteName = (import.meta.env.VITE_SITE_NAME || "群资源站");

async function load() {
  loading.value = true;
  err.value = "";
  try {
    const res = await request<WorkListResponse>({ url: "/works/", method: "GET", params: { page: 1, page_size: 6 } });
    works.value = res.items.slice(0, 6);
  } catch (e) {
    err.value = extractErrMsg(e, "获取最新作品失败");
  } finally {
    loading.value = false;
  }
}
onMounted(load);
</script>

<template>
  <section>
    <div class="hero card">
      <div>
        <div class="eyebrow">{{ siteName }} · 私域共享书库</div>
        <h1 class="hero-title">一起整理，一起阅读。</h1>
        <p class="hero-sub">
          仅限群友加入：上传作品、点赞支持、写下推荐语、发起话题。
          <br />
          站点权限由「QQ 群成员白名单」联动，退群即失去访问资格。
        </p>
        <div class="row mt-4">
          <router-link to="/works" class="btn btn-primary">进入作品库 →</router-link>
          <router-link to="/me" class="btn">查看我的</router-link>
        </div>
      </div>
      <div class="hero-illu" aria-hidden>📖✨</div>
    </div>

    <div class="mt-8 row-between">
      <h2 style="margin: 0">💜 最近更新</h2>
      <router-link to="/works">查看全部 →</router-link>
    </div>

    <div v-if="err" class="alert alert-error mt-4">{{ err }}</div>
    <div v-if="loading" class="mt-4 muted text-sm">加载中…</div>
    <div v-else-if="works.length === 0" class="mt-4 card muted text-sm">还没有作品，去做第一个上传者吧 ✨</div>
    <div v-else class="grid-cards mt-4">
      <article v-for="w in works" :key="w.id" class="item card">
        <div class="row-between">
          <h3 style="margin: 0; font-size: 15px">
            <RouterLink :to="`/works/${w.id}`" class="title-link">{{ w.title }}</RouterLink>
          </h3>
          <span class="badge badge-muted">{{ w.type }}</span>
        </div>
        <p class="muted text-sm" style="margin: 8px 0 0">{{ w.summary || "（暂无简介）" }}</p>
      </article>
    </div>
  </section>
</template>

<style scoped>
.hero {
  display: grid;
  grid-template-columns: 1fr 180px;
  gap: 24px;
  align-items: center;
  padding: 28px;
  background:
    radial-gradient(800px 300px at 0% 0%, rgba(79, 70, 229, 0.14), transparent 60%),
    radial-gradient(600px 240px at 100% 100%, rgba(124, 58, 237, 0.14), transparent 60%),
    #fff;
}
.eyebrow {
  text-transform: uppercase;
  letter-spacing: 2px;
  font-size: 12px;
  color: var(--color-primary);
  font-weight: 700;
}
.hero-title {
  margin: 6px 0 10px;
  font-size: 32px;
  background: var(--accent-gradient);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
.hero-sub {
  margin: 0;
  line-height: 1.8;
  color: var(--color-muted);
}
.hero-illu {
  font-size: 96px;
  display: flex;
  align-items: center;
  justify-content: center;
  filter: drop-shadow(0 20px 30px rgba(236, 72, 153, 0.18));
}
.title-link {
  color: inherit;
}
.item {
  padding: 16px;
}
@media (max-width: 720px) {
  .hero {
    grid-template-columns: 1fr;
  }
  .hero-illu {
    font-size: 64px;
    justify-content: flex-start;
  }
}
</style>
