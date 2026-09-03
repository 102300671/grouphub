<script setup lang="ts">
import { onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { attachmentFromUrl } from "@/api";
import { extractErrMsg, request } from "@/api/http";
import type { Topic, TopicDetailResponse, TopicPost } from "@/types/api";
import Avatar from "@/components/Avatar.vue";

const props = defineProps<{ id: string | number }>();

const topic = ref<Topic | null>(null);
const posts = ref<TopicPost[]>([]);
const loading = ref(false);
const errorMsg = ref("");

const replyText = ref("");
const replyFile = ref<File | null>(null);
const replyFileInput = ref<HTMLInputElement | null>(null);
const replyDirectUrl = ref("");
const replySubmitting = ref(false);
const replyMsg = ref("");

async function load() {
  loading.value = true;
  errorMsg.value = "";
  try {
    const res = await request<TopicDetailResponse>({ url: `/topics/${props.id}`, method: "GET" });
    topic.value = res.topic;
    posts.value = res.posts.items;
  } catch (e) {
    errorMsg.value = extractErrMsg(e, "加载主题失败");
  } finally {
    loading.value = false;
  }
}

function onReplyFileSelect(e: Event) {
  const input = e.target as HTMLInputElement;
  replyFile.value = input.files?.[0] ?? null;
}

async function submitReply() {
  if (!replyText.value.trim()) return;
  replySubmitting.value = true;
  replyMsg.value = "";
  try {
    let attachments: any[] = [];
    if (replyFile.value) {
      const fd = new FormData();
      fd.append("file", replyFile.value);
      const upRes = await request<{ attachment: any }>({
        url: "/topics/attachments",
        method: "POST",
        data: fd,
        headers: { "Content-Type": "multipart/form-data" },
      });
      attachments = [upRes.attachment];
    }
    await request({
      url: `/topics/${props.id}/posts`,
      method: "POST",
      data: {
        content: replyText.value.trim(),
        attachments,
      },
    });
    replyText.value = "";
    replyFile.value = null;
    if (replyFileInput.value) replyFileInput.value.value = "";
    await load();
  } catch (e) {
    replyMsg.value = extractErrMsg(e, "回帖失败");
  } finally {
    replySubmitting.value = false;
  }
}

function fmtDate(s: string) {
  if (!s) return "";
  return s.replace("T", " ").slice(0, 16);
}

onMounted(load);
</script>

<template>
  <section>
    <div v-if="loading" class="muted text-sm">加载中…</div>
    <div v-else-if="errorMsg" class="alert alert-error">{{ errorMsg }}</div>
    <template v-else-if="topic">
      <RouterLink to="/forum" class="back-link">← 返回论坛</RouterLink>

      <!-- 主题正文 -->
      <article class="topic-main card mt-2">
        <h1 style="margin: 0 0 8px; font-size: 20px">{{ topic.title }}</h1>
        <div class="muted text-sm" style="display: flex; align-items: center; gap: 8px;">
          <Avatar v-if="topic.creator" :src="topic.creator.avatar_url" :name="topic.creator.nickname" :size="24" />
          <span v-if="topic.creator">{{ topic.creator.nickname }}（QQ {{ topic.creator.qq }}）</span>
          <span> · {{ fmtDate(topic.created_at) }}</span>
        </div>
        <p v-if="topic.body" class="topic-body">{{ topic.body }}</p>
        <div v-if="topic.attachments?.length" class="atts">
          <a v-for="a in topic.attachments" :key="a.url" :href="a.url" target="_blank" rel="noreferrer" class="badge badge-muted">
            {{ a.type === "image" ? "🖼" : a.type === "video" ? "🎬" : "📎" }} {{ a.file_name }}
          </a>
        </div>
      </article>

      <!-- 回帖列表 -->
      <section class="mt-4">
        <h3 style="margin-bottom: 12px">📮 回帖（{{ posts.length }}）</h3>
        <div v-if="posts.length === 0" class="card muted text-sm" style="text-align: center">还没有回帖，来抢沙发～</div>
        <div v-else class="post-list">
          <div v-for="(p, idx) in posts" :key="p.id" class="post-item card">
            <div class="post-head">
              <Avatar :src="p.avatar_url" :name="p.nickname" :size="26" />
              <span class="post-idx">#{{ idx + 1 }}</span>
              <span class="post-nick">{{ p.nickname || "匿名" }}</span>
              <span class="muted text-sm">QQ {{ p.qq }}</span>
              <span class="muted text-sm">{{ fmtDate(p.created_at) }}</span>
            </div>
            <p class="post-body">{{ p.content }}</p>
            <div v-if="p.attachments?.length" class="atts">
              <a v-for="a in p.attachments" :key="a.url" :href="a.url" target="_blank" rel="noreferrer" class="badge badge-muted">
                {{ a.type === "image" ? "🖼" : a.type === "video" ? "🎬" : "📎" }} {{ a.file_name }}
              </a>
            </div>
          </div>
        </div>

        <!-- 回帖表单 -->
        <div class="card mt-4 reply-form">
          <h3 style="margin-top: 0">写回帖</h3>
          <input ref="replyFileInput" type="file" style="margin-bottom: 8px" @change="onReplyFileSelect" />
          <input v-model="replyDirectUrl" class="input" type="text" placeholder="或直接粘贴附件直链 URL（图片/视频/文档）" style="margin-bottom: 8px; width: 100%;" />
          <textarea v-model="replyText" class="input" rows="4" placeholder="写下你的想法…（可附带文件或直链）"></textarea>
          <div style="display: flex; justify-content: flex-end; margin-top: 10px">
            <button class="btn btn-primary" :disabled="!replyText.trim() || replySubmitting" @click="submitReply">
              {{ replySubmitting ? "提交中…" : "发表回帖" }}
            </button>
          </div>
          <p v-if="replyMsg" class="alert alert-error text-sm">{{ replyMsg }}</p>
        </div>
      </section>
    </template>
  </section>
</template>

<style scoped>
.back-link { color: var(--color-primary); font-size: 14px; }
.topic-main { padding: 22px; }
.topic-body { margin: 12px 0 0; line-height: 1.75; font-size: 15px; white-space: pre-wrap; word-break: break-word; }
.atts { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
.post-list { display: flex; flex-direction: column; gap: 10px; }
.post-item { padding: 14px 16px; }
.post-head { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
.post-idx { color: var(--color-muted); font-size: 13px; font-weight: 700; }
.post-nick { font-weight: 600; font-size: 14px; }
.post-body { margin: 0; font-size: 14px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; }
.reply-form { display: flex; flex-direction: column; }
</style>
