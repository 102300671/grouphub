<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { RouterLink, useRouter } from "vue-router";
import { fanworksClient } from "@/api";
import { extractErrMsg } from "@/api/http";
import { useUserStore } from "@/stores/user";
import type { Fanwork } from "@/types/api";

const props = defineProps<{ id: string | number }>();

const router = useRouter();
const user = useUserStore();

const fw = ref<Fanwork | null>(null);
const loading = ref(false);
const errorMsg = ref("");
const deleting = ref(false);

const canManage = computed(() => {
  if (!fw.value || !user.current) return false;
  return user.isAdmin || fw.value.author?.id === user.current.id;
});

async function load() {
  loading.value = true;
  errorMsg.value = "";
  try {
    const res = await fanworksClient.detail(props.id);
    fw.value = res.item;
  } catch (e) {
    errorMsg.value = extractErrMsg(e, "加载同人作品失败");
  } finally {
    loading.value = false;
  }
}

async function onDelete() {
  if (!fw.value) return;
  if (!window.confirm(`确定删除「${fw.value.title}」？此操作不可恢复。`)) return;
  deleting.value = true;
  try {
    await fanworksClient.remove(fw.value.id);
    router.push("/fanworks");
  } catch (e) {
    window.alert(extractErrMsg(e, "删除失败"));
  } finally {
    deleting.value = false;
  }
}

function fmtDate(s: string) {
  if (!s) return "";
  return s.replace("T", " ").slice(0, 16);
}

function fmtSize(n?: number) {
  if (!n) return "";
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}KB`;
  return `${(n / 1024 / 1024).toFixed(1)}MB`;
}

function attIcon(type: string) {
  return type === "image" ? "🖼" : type === "video" ? "🎬" : type === "audio" ? "🎵" : "📎";
}

onMounted(load);
</script>

<template>
  <section>
    <div v-if="loading" class="muted text-sm">加载中…</div>
    <div v-else-if="errorMsg" class="alert alert-error">{{ errorMsg }}</div>
    <template v-else-if="fw">
      <RouterLink to="/fanworks" class="back-link">← 返回同人创作</RouterLink>

      <article class="fw-main card mt-2">
        <div class="fw-head">
          <img v-if="fw.cover_url" class="fw-cover" :src="fw.cover_url" :alt="fw.title" />
          <div class="fw-headinfo">
            <h1 style="margin: 0 0 8px; font-size: 22px">
              {{ fw.title }}
              <span v-if="fw.status === 'draft'" class="badge badge-draft">草稿</span>
            </h1>
            <div class="muted text-sm" style="display: flex; flex-wrap: wrap; gap: 6px; align-items: center;">
              <span>{{ fw.author?.nickname || `群友${fw.author?.qq ?? ""}` }}</span>
              <span>· {{ fmtDate(fw.created_at) }}</span>
              <span v-if="fw.category" class="badge badge-muted">{{ fw.category }}</span>
            </div>
            <RouterLink v-if="fw.work_id" :to="`/works/${fw.work_id}`" class="badge badge-work">
              📖 原作：{{ fw.work_title || `#${fw.work_id}` }}
            </RouterLink>
          </div>
        </div>

        <p v-if="fw.body" class="fw-body">{{ fw.body }}</p>

        <!-- 附件：图片/视频/音频内联展示，其余为下载链接 -->
        <div v-if="fw.attachments?.length" class="atts">
          <template v-for="a in fw.attachments" :key="a.url">
            <a v-if="a.type === 'image'" :href="a.url" target="_blank" rel="noreferrer" class="att-image">
              <img :src="a.url" :alt="a.file_name" loading="lazy" />
            </a>
            <video v-else-if="a.type === 'video'" class="att-video" :src="a.url" controls preload="metadata"></video>
            <audio v-else-if="a.type === 'audio'" class="att-audio" :src="a.url" controls preload="metadata"></audio>
            <a v-else :href="a.url" target="_blank" rel="noreferrer" class="badge badge-muted att-file">
              {{ attIcon(a.type) }} {{ a.file_name }} <span v-if="fmtSize(a.size_bytes)" class="muted">（{{ fmtSize(a.size_bytes) }}）</span>
            </a>
          </template>
        </div>

        <div v-if="canManage" class="fw-actions">
          <RouterLink :to="`/fanworks/${fw.id}/edit`" class="btn">✏️ 编辑</RouterLink>
          <button class="btn btn-danger" type="button" :disabled="deleting" @click="onDelete">
            {{ deleting ? "删除中…" : "🗑 删除" }}
          </button>
        </div>
      </article>
    </template>
  </section>
</template>

<style scoped>
.back-link { color: var(--color-primary); font-size: 14px; }
.fw-main { padding: 22px; }
.fw-head { display: flex; gap: 18px; align-items: flex-start; flex-wrap: wrap; }
.fw-cover {
  width: 180px;
  max-width: 40vw;
  border-radius: 10px;
  object-fit: cover;
  border: 1px solid var(--color-border);
}
.fw-headinfo { flex: 1; min-width: 240px; display: flex; flex-direction: column; gap: 8px; }
.badge-draft { background: #f59e0b; color: #fff; font-size: 12px; vertical-align: middle; }
.badge-work { background: var(--color-primary-soft, #eef2ff); color: var(--color-primary); align-self: flex-start; }
.fw-body {
  margin: 16px 0 0;
  line-height: 1.8;
  font-size: 15px;
  white-space: pre-wrap;
  word-break: break-word;
}
.atts { margin-top: 18px; display: flex; flex-direction: column; gap: 12px; }
.att-image img {
  max-width: 100%;
  max-height: 480px;
  border-radius: 8px;
  border: 1px solid var(--color-border);
  display: block;
}
.att-video { max-width: 100%; width: 480px; max-width: 100%; border-radius: 8px; }
.att-audio { width: 100%; max-width: 480px; }
.att-file { align-self: flex-start; padding: 6px 10px; }
.fw-actions {
  margin-top: 22px;
  padding-top: 16px;
  border-top: 1px solid var(--color-border);
  display: flex;
  gap: 10px;
}
.btn-danger { color: #dc2626; border-color: #fecaca; }
.btn-danger:hover { background: #fef2f2; }
</style>
