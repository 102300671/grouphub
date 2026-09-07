<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { attachmentFromUrl } from "@/api";
import { extractErrMsg, request } from "@/api/http";
import Avatar from "@/components/Avatar.vue";
import type { Topic, TopicListResponse } from "@/types/api";

const router = useRouter();
const topics = ref<Topic[]>([]);
const loading = ref(false);
const errorMsg = ref("");
const keyword = ref("");

// 发帖表单
const showForm = ref(false);
const formTitle = ref("");
const formBody = ref("");
const formFile = ref<File | null>(null);
const formFileInput = ref<HTMLInputElement | null>(null);
const formDirectUrl = ref("");
const formSubmitting = ref(false);
const formMsg = ref("");

async function loadTopics() {
  loading.value = true;
  errorMsg.value = "";
  try {
    const params: Record<string, string> = {};
    const kw = keyword.value.trim();
    if (kw) params.keyword = kw;
    const url = `/topics/?${new URLSearchParams(params).toString()}`;
    const res = await request<TopicListResponse>({ url, method: "GET" });
    topics.value = res.items;
  } catch (e) {
    errorMsg.value = extractErrMsg(e, "加载主题失败");
  } finally {
    loading.value = false;
  }
}

function onFormFileSelect(e: Event) {
  const input = e.target as HTMLInputElement;
  formFile.value = input.files?.[0] ?? null;
}

async function submitTopic() {
  if (!formTitle.value.trim()) {
    formMsg.value = "请填写标题";
    return;
  }
  formSubmitting.value = true;
  formMsg.value = "";
  try {
    let attachments: any[] = [];
    if (formFile.value) {
      const fd = new FormData();
      fd.append("file", formFile.value);
      const upRes = await request<{ attachment: any }>({
        url: "/topics/attachments",
        method: "POST",
        data: fd,
        headers: { "Content-Type": "multipart/form-data" },
      });
      attachments = [upRes.attachment];
    }
    const created = await request<{ id: number }>({
      url: "/topics/",
      method: "POST",
      data: {
        title: formTitle.value.trim(),
        body: formBody.value.trim() || null,
        attachments,
      },
    });
    router.push(`/forum/${created.id}`);
  } catch (e) {
    formMsg.value = extractErrMsg(e, "发布失败");
  } finally {
    formSubmitting.value = false;
  }
}

function fmtDate(s: string) {
  if (!s) return "";
  return s.replace("T", " ").slice(0, 16);
}

onMounted(loadTopics);
</script>

<template>
  <section>
    <div class="row-between">
      <div>
        <h2 style="margin: 0">💬 论坛</h2>
        <p class="muted text-sm" style="margin: 6px 0 0">群友交流区：聊作品、聊同人、聊一切。</p>
      </div>
      <button v-if="!showForm" class="btn btn-primary" type="button" @click="showForm = true">✍️ 发新主题</button>
    </div>

    <!-- 发帖表单 -->
    <div v-if="showForm" class="card mt-4">
      <h3 style="margin-top: 0">发新主题</h3>
      <div v-if="formMsg" class="alert alert-error">{{ formMsg }}</div>
      <div class="form-item">
        <label>标题 *</label>
        <input v-model="formTitle" class="input" type="text" maxlength="255" placeholder="一句话概括你想聊什么" />
      </div>
      <div class="form-item">
        <label>内容（可选）</label>
        <textarea v-model="formBody" class="input" rows="5" placeholder="补充一些细节…"></textarea>
      </div>
      <div class="form-item">
        <label>附件（可选）</label>
        <input ref="formFileInput" type="file" @change="onFormFileSelect" />
        <span v-if="formFile" class="muted text-sm">已选：{{ formFile.name }}</span>
        <input v-model="formDirectUrl" class="input" type="text" placeholder="或直接粘贴附件直链 URL（图片/视频/文档）" style="margin-top: 8px; width: 100%;" />
      </div>
      <div class="row-between" style="margin-top: 12px">
        <button class="btn" type="button" @click="showForm = false">取消</button>
        <button class="btn btn-primary" type="button" :disabled="formSubmitting" @click="submitTopic">
          {{ formSubmitting ? "发布中…" : "发布" }}
        </button>
      </div>
    </div>

    <!-- 搜索 -->
    <div class="filter-bar mt-4">
      <input
        v-model="keyword"
        class="input search-input"
        type="search"
        placeholder="搜索主题标题…"
        @keyup.enter="loadTopics"
      />
      <button class="btn" type="button" @click="loadTopics">搜索</button>
    </div>

    <div v-if="errorMsg" class="alert alert-error mt-4">{{ errorMsg }}</div>
    <div v-if="loading" class="mt-8 muted text-sm">加载中…</div>
    <div v-else-if="topics.length === 0" class="mt-8 card" style="text-align: center">
      <div class="muted">{{ keyword ? "没有符合条件的主题。" : "还没有主题，来发第一个吧 🚀" }}</div>
    </div>

    <div v-else class="topic-list">
      <RouterLink
        v-for="t in topics"
        :key="t.id"
        :to="`/forum/${t.id}`"
        class="topic-item card"
      >
        <div class="topic-main">
          <h3 class="topic-title">{{ t.title }}</h3>
          <p v-if="t.body" class="muted text-sm topic-body">{{ t.body.slice(0, 80) }}{{ t.body.length > 80 ? "…" : "" }}</p>
          <div class="muted text-sm topic-meta" style="display: flex; align-items: center; gap: 8px;">
            <Avatar v-if="t.creator" :src="t.creator.avatar_url" :name="t.creator.nickname" :size="22" />
            <span v-if="t.creator">{{ t.creator.nickname }}（{{ t.creator.qq }}）</span>
            <span> · {{ fmtDate(t.created_at) }}</span>
          </div>
        </div>
        <div class="topic-count">{{ t.post_count }} 回复</div>
      </RouterLink>
    </div>
  </section>
</template>

<style scoped>
.filter-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.search-input { width: 260px; max-width: 100%; }
.topic-list { display: flex; flex-direction: column; gap: 10px; margin-top: 14px; }
.topic-item { display: flex; gap: 16px; padding: 16px; transition: transform 0.15s ease, box-shadow 0.15s ease; }
.topic-item:hover { transform: translateY(-2px); box-shadow: var(--shadow-md); }
.topic-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 5px; }
.topic-title { margin: 0; font-size: 15px; }
.topic-body { margin: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.topic-count { flex-shrink: 0; color: var(--color-primary); font-weight: 600; font-size: 13px; }
@media (max-width: 720px) {
  .search-input { width: 100%; }
  .topic-item { flex-direction: column; gap: 8px; padding: 14px; }
  .topic-count { align-self: flex-end; }
}
</style>
