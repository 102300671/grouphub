<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { attachmentFromUrl, fanworksClient } from "@/api";
import { extractErrMsg, request } from "@/api/http";
import { useUserStore } from "@/stores/user";
import type { FanworkAttachment } from "@/types/api";

const route = useRoute();
const router = useRouter();
const user = useUserStore();

const editId = computed(() => (route.params.id ? Number(route.params.id) : null));
const isEdit = computed(() => editId.value !== null);

// 表单
const title = ref("");
const category = ref("");
const workIdInput = ref<string>("");
const workTitle = ref<string>("");
const workIdError = ref("");
const coverUrl = ref("");
const coverFile = ref<File | null>(null);
const coverFileInput = ref<HTMLInputElement | null>(null);
const coverUploading = ref(false);

function onCoverFileChange(e: Event) {
  coverFile.value = (e.target as HTMLInputElement).files?.[0] ?? null;
}
const body = ref("");
const attachments = ref<FanworkAttachment[]>([]);
const status = ref<"draft" | "published">("draft");

const loading = ref(false);
const submitting = ref(false);
const uploading = ref(false);
const errorMsg = ref("");
const forbidden = ref(false);

const workIdNum = computed(() => {
  const v = workIdInput.value.trim();
  if (!v) return null;
  const n = Number(v);
  return Number.isInteger(n) && n > 0 ? n : null;
});

async function lookupWork() {
  workIdError.value = "";
  workTitle.value = "";
  const id = workIdNum.value;
  if (id === null) {
    if (workIdInput.value.trim()) workIdError.value = "原作 ID 需为正整数";
    return;
  }
  try {
    const w = await request<{ title: string }>({ url: `/works/${id}`, method: "GET" });
    workTitle.value = w.title;
  } catch {
    workIdError.value = `作品 id=${id} 不存在`;
  }
}

function onAttachmentsSelect(e: Event) {
  const input = e.target as HTMLInputElement;
  const files = Array.from(input.files ?? []);
  if (files.length) void uploadFiles(files);
  input.value = "";
}

async function uploadFiles(files: File[]) {
  uploading.value = true;
  errorMsg.value = "";
  try {
    for (const f of files) {
      const att = await fanworksClient.uploadAttachment(f);
      attachments.value.push(att);
    }
  } catch (e) {
    errorMsg.value = extractErrMsg(e, "附件上传失败");
  } finally {
    uploading.value = false;
  }
}

function removeAttachment(idx: number) {
  attachments.value.splice(idx, 1);
}

// 外站直链附件（不上传本体，直接构造附件 dict）
const directAttUrl = ref("");
function addDirectAttachment() {
  const u = directAttUrl.value.trim();
  if (!u || attachments.value.some((a) => a.url === u)) {
    directAttUrl.value = "";
    return;
  }
  attachments.value.push(attachmentFromUrl(u));
  directAttUrl.value = "";
}

function attIcon(type: string) {
  return type === "image" ? "🖼" : type === "video" ? "🎬" : type === "audio" ? "🎵" : "📎";
}

async function submit(target: "draft" | "published") {
  if (!title.value.trim()) {
    errorMsg.value = "标题不能为空";
    return;
  }
  if (workIdInput.value.trim() && workIdNum.value === null) {
    errorMsg.value = "原作 ID 需为正整数（或留空表示自由创作）";
    return;
  }
  if (workIdError.value) {
    errorMsg.value = workIdError.value;
    return;
  }

  submitting.value = true;
  errorMsg.value = "";
  const payload = {
    title: title.value.trim(),
    category: category.value.trim() || null,
    work_id: workIdNum.value,
    cover_url: coverUrl.value.trim() || null,
    body: body.value.trim() || null,
    attachments: attachments.value,
    status: target,
  };

  try {
    const res = isEdit.value
      ? await fanworksClient.update(editId.value!, payload)
      : await fanworksClient.create(payload);

    // 上传封面（本地文件优先；创建/编辑后调用 fanworksClient.uploadCover 回写库中 cover_url）
    if (coverFile.value) {
      coverUploading.value = true;
      try {
        await fanworksClient.uploadCover(res.item.id, coverFile.value);
      } finally {
        coverUploading.value = false;
      }
    }

    router.push(`/fanworks/${res.item.id}`);
  } catch (e) {
    errorMsg.value = extractErrMsg(e, "保存失败");
  } finally {
    submitting.value = false;
  }
}

onMounted(async () => {
  // 从作品详情页「写同人」跳来：?work=ID 预填关联原作
  const qWork = route.query.work as string | undefined;
  if (qWork) {
    workIdInput.value = qWork;
    await lookupWork();
  }

  if (!isEdit.value) return;

  loading.value = true;
  try {
    const res = await fanworksClient.detail(editId.value!);
    const fw = res.item;
    if (user.current && !user.isAdmin && fw.author?.id !== user.current.id) {
      forbidden.value = true;
      return;
    }
    title.value = fw.title;
    category.value = fw.category ?? "";
    workIdInput.value = fw.work_id ? String(fw.work_id) : "";
    workTitle.value = fw.work_title ?? "";
    coverUrl.value = fw.cover_url ?? "";
    body.value = fw.body ?? "";
    attachments.value = fw.attachments ?? [];
    status.value = fw.status;
  } catch (e) {
    errorMsg.value = extractErrMsg(e, "加载同人作品失败");
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <section style="max-width: 760px">
    <RouterLink to="/fanworks" class="back-link">← 返回同人创作</RouterLink>
    <h2 style="margin: 8px 0 16px">{{ isEdit ? "编辑同人作品" : "发布同人作品" }}</h2>

    <div v-if="loading" class="muted text-sm">加载中…</div>
    <div v-else-if="forbidden" class="alert alert-error">你只能编辑自己的同人作品。</div>

    <div v-else class="card form-card">
      <div v-if="errorMsg" class="alert alert-error">{{ errorMsg }}</div>

      <div class="form-item">
        <label>标题 *</label>
        <input v-model="title" class="input" type="text" maxlength="255" placeholder="给你的同人作品起个名字" />
      </div>

      <div class="form-item">
        <label>分类（可选）</label>
        <input v-model="category" class="input" type="text" maxlength="50" placeholder="如：同人文 / 插画 / 手书 / 二创视频" />
      </div>

      <div class="form-item">
        <label>关联原作 ID（可选）</label>
        <input
          v-model="workIdInput"
          class="input"
          type="text"
          inputmode="numeric"
          placeholder="馆内作品 ID，留空表示自由创作"
          @change="lookupWork"
        />
        <div v-if="workTitle" class="text-sm" style="color: var(--color-primary)">✓ 原作：{{ workTitle }}</div>
        <div v-else-if="workIdError" class="text-sm" style="color: #dc2626">{{ workIdError }}</div>
      </div>

      <div class="form-item">
        <label>封面图（可选；上传本地图片，不再支持填 URL）</label>
        <input ref="coverFileInput" type="file" accept="image/*" @change="onCoverFileChange" />
        <div v-if="coverUrl || coverFile" style="margin-top: 6px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap;">
          <img v-if="coverUrl" :src="coverUrl" :alt="title" style="max-height: 64px; border-radius: 6px; border: 1px solid var(--color-border);" />
          <span v-if="coverFile" class="text-sm muted">
            新封面：{{ coverFile.name }}（{{ (coverFile.size / 1024 / 1024).toFixed(1) }} MB）{{ coverUploading ? "，上传中…" : "" }}
          </span>
        </div>
        <p class="muted text-sm" style="margin-top: 4px;">仅允许常见位图格式（jpg/png/webp 等）。</p>
      </div>

      <div class="form-item">
        <label>正文（可选）</label>
        <textarea v-model="body" class="input" rows="10" placeholder="写下你的创作…（支持纯文本换行）"></textarea>
      </div>

      <div class="form-item">
        <label>附件（图片 / 视频 / 音频 / 文档，可选，可多选）</label>
        <input type="file" multiple :disabled="uploading" @change="onAttachmentsSelect" />
        <div v-if="uploading" class="muted text-sm">附件上传中…</div>
        <div style="display: flex; gap: 0.5rem; margin-top: 8px;">
          <input v-model="directAttUrl" class="input" type="text" placeholder="或直接粘贴附件直链 URL（图片/视频/文档）" @keyup.enter.prevent="addDirectAttachment" style="flex: 1;" />
          <button type="button" class="btn btn-ghost" :disabled="!directAttUrl.trim()" @click="addDirectAttachment">添加</button>
        </div>
        <ul v-if="attachments.length" class="att-list">
          <li v-for="(a, idx) in attachments" :key="a.url">
            <span>{{ attIcon(a.type) }} {{ a.file_name }}</span>
            <button type="button" class="att-remove" @click="removeAttachment(idx)">移除</button>
          </li>
        </ul>
      </div>

      <div class="form-actions">
        <button class="btn" type="button" :disabled="submitting || uploading" @click="submit('draft')">
          {{ submitting && status === "draft" ? "保存中…" : "存草稿" }}
        </button>
        <button class="btn btn-primary" type="button" :disabled="submitting || uploading" @click="submit('published')">
          {{ submitting ? "提交中…" : isEdit ? "保存并发布" : "发布" }}
        </button>
      </div>
      <p class="muted text-sm" style="margin: 8px 0 0">
        草稿仅自己可见；发布后全体群友可见。附件单文件上限 32MB，仅允许常见图片/视频/音频/文档类型。
      </p>
    </div>
  </section>
</template>

<style scoped>
.back-link { color: var(--color-primary); font-size: 14px; }
.form-card { padding: 22px; display: flex; flex-direction: column; gap: 4px; }
.att-list { list-style: none; margin: 10px 0 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.att-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 6px 10px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  font-size: 13px;
}
.att-remove {
  border: none;
  background: none;
  color: #dc2626;
  cursor: pointer;
  font-size: 13px;
}
.form-actions { display: flex; gap: 10px; margin-top: 16px; }
@media (max-width: 720px) {
  .form-card { padding: 14px; }
  .form-actions { flex-wrap: wrap; }
  .form-actions .btn { flex: 1; }
}
</style>
