<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { useRouter, useRoute } from "vue-router";
import { extractErrMsg, request } from "@/api/http";
import { worksClient } from "@/api";

const router = useRouter();
const route = useRoute();

// 表单字段
const title = ref("");
const author = ref("");
const type = ref("novel");
const summary = ref("");
const tagsInput = ref("");
const sourceWorkId = ref<number | null>(null);
const sourceWorkTitle = ref("");
const linkList = ref<{ site_name: string | null; url: string }[]>([]);
const newLinkSite = ref("");
const newLinkUrl = ref("");

// 封面图片上传
const coverFile = ref<File | null>(null);
const coverFileInput = ref<HTMLInputElement | null>(null);
const coverUrl = ref<string>("");
const coverUploading = ref(false);
const coverMsg = ref("");

// 文件上传
const file = ref<File | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);

// 批量连载文件上传
const batchFiles = ref<File[]>([]);
const batchFileInput = ref<HTMLInputElement | null>(null);

// 提交状态
const submitting = ref(false);
const error = ref("");
const success = ref("");

function onCoverFileChange(e: Event) {
  const input = e.target as HTMLInputElement;
  const f = input.files?.[0] ?? null;
  coverFile.value = f;
  coverMsg.value = "";
}

async function onCoverUploadNow() {
  // 用户先选文件时：不提前上传，等作品 POST 成功后再上传封面并 PATCH。此函数仅做手动预览上传占位
  coverMsg.value = "作品创建后将自动上传封面。";
}

function onBatchSelect(e: Event) {
  const input = e.target as HTMLInputElement;
  const fs = Array.from(input.files ?? []);
  if (fs.length) batchFiles.value.push(...fs);
}
function removeBatch(idx: number) {
  batchFiles.value.splice(idx, 1);
}

// 外站直链（提交时逐个添加为作品文件，不上传本体）
const directUrls = ref<string[]>([]);
const newDirectUrl = ref("");
function addDirectUrlItem() {
  const u = newDirectUrl.value.trim();
  if (!u || directUrls.value.includes(u)) {
    newDirectUrl.value = "";
    return;
  }
  directUrls.value.push(u);
  newDirectUrl.value = "";
}
function removeDirectUrlItem(idx: number) {
  directUrls.value.splice(idx, 1);
}

const TYPE_OPTIONS = [
  { value: "novel", label: "小说" },
  { value: "anime", label: "番剧/动漫" },
  { value: "movie", label: "电影" },
  { value: "gallery", label: "图集" },
  { value: "fanwork", label: "同人文" },
  { value: "other", label: "其他" },
];

const typeLabel = computed(() => TYPE_OPTIONS.find(t => t.value === type.value)?.label ?? "其他");

// 直链示例随类型变化：小说给 txt，番剧/电影给视频，其它给文档
const directUrlPlaceholder = computed(() => {
  if (type.value === "novel") return "https://.../example.txt";
  if (type.value === "anime" || type.value === "movie") return "https://.../example.mp4";
  if (type.value === "gallery") return "https://.../example.jpg";
  return "https://.../example.pdf";
});

// 从 query 参数预填（从作品详情页「写同人文」跳转）
onMounted(() => {
  const qType = route.query.type as string;
  const qSource = route.query.source as string;
  if (qType && TYPE_OPTIONS.some(t => t.value === qType)) {
    type.value = qType;
  }
  if (qSource) {
    sourceWorkId.value = Number(qSource);
    // 拉原作标题
    request<any>({ url: `/works/${qSource}` }).then(w => { sourceWorkTitle.value = w.title; }).catch(() => {});
  }
});

function addLink() {
  const url = newLinkUrl.value.trim();
  if (!url) return;
  linkList.value.push({ site_name: newLinkSite.value.trim() || null, url });
  newLinkSite.value = "";
  newLinkUrl.value = "";
}

function removeLink(idx: number) {
  linkList.value.splice(idx, 1);
}

function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement;
  file.value = input.files?.[0] ?? null;
}

async function submit() {
  if (!title.value.trim()) {
    error.value = "标题不能为空";
    return;
  }
  error.value = "";
  submitting.value = true;

  try {
    const tags = tagsInput.value
      .split(/[,，]/)
      .map(t => t.trim())
      .filter(Boolean);

    // 1) 创建作品（不带 cover_url）
    const work = await request<{ id: number }>({
      url: "/works/",
      method: "POST",
      data: {
        title: title.value.trim(),
        author: author.value.trim() || null,
        type: type.value,
        source_work_id: sourceWorkId.value || undefined,
        summary: summary.value.trim() || null,
        tags,
        links: linkList.value,
      },
    });

    // 2) 封面图 → 上传并回写 cover_url
    if (coverFile.value) {
      coverUploading.value = true;
      try {
        coverUrl.value = await worksClient.uploadCover(work.id, coverFile.value);
      } finally {
        coverUploading.value = false;
      }
    }

    // 3) 如果有单文件，上传到作品
    if (file.value) {
      await worksClient.uploadFile(work.id, file.value);
    }

    // 4) 批量连载文件（一次请求全部入库）
    if (batchFiles.value.length) {
      await worksClient.uploadFilesBatch(work.id, batchFiles.value);
    }

    // 5) 跳转详情
    router.push(`/works/${work.id}`);
  } catch (e: any) {
    error.value = extractErrMsg(e, "提交失败");
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <div class="container" style="max-width: 640px; padding-top: 2rem;">
    <h1 style="margin-bottom: 1.5rem;">上传作品</h1>

    <div v-if="success" class="alert alert-success">{{ success }}</div>
    <div v-if="error" class="alert alert-error">{{ error }}</div>

    <form @submit.prevent="submit" class="card" style="display: flex; flex-direction: column; gap: 1rem; padding: 1.5rem;">
      <!-- 标题 -->
      <div class="form-item">
        <label>标题 <span style="color: #e53e3e;">*</span></label>
        <input v-model="title" class="input" placeholder="作品名称" required />
      </div>

      <!-- 作者 -->
      <div class="form-item">
        <label>作者</label>
        <input v-model="author" class="input" placeholder="作者名（可选）" />
      </div>

      <!-- 类型 -->
      <div class="form-item">
        <label>类型</label>
        <select v-model="type" class="input">
          <option v-for="t in TYPE_OPTIONS" :key="t.value" :value="t.value">{{ t.label }}</option>
        </select>
      </div>

      <!-- 关联原作（同人时显示） -->
      <div v-if="type === 'fanwork'" class="form-item">
        <label>关联原作</label>
        <div v-if="sourceWorkId" class="alert alert-info" style="margin-bottom: 0.5rem;">
          原作：{{ sourceWorkTitle || `ID ${sourceWorkId}` }}
          <button type="button" class="btn btn-ghost" style="padding: 0.1rem 0.4rem; margin-left: 0.5rem;" @click="sourceWorkId = null; sourceWorkTitle = ''">移除</button>
        </div>
        <input v-else v-model.number="sourceWorkId" class="input" type="number" placeholder="原作 ID（0 = 独立原创）" min="0" />
        <p class="muted text-sm" style="margin-top: 0.25rem;">填 0 或不填表示独立原创同人文</p>
      </div>

      <!-- 简介 -->
      <div class="form-item">
        <label>简介</label>
        <textarea v-model="summary" class="input" rows="3" placeholder="简短介绍（可选）"></textarea>
      </div>

      <!-- 标签 -->
      <div class="form-item">
        <label>标签</label>
        <input v-model="tagsInput" class="input" placeholder="用逗号分隔，如：科幻, 长篇, 经典" />
      </div>

      <!-- 封面图（上传本地图片，不再让用户填 URL） -->
      <div class="form-item">
        <label>封面图</label>
        <input ref="coverFileInput" type="file" accept="image/*" @change="onCoverFileChange" style="width: 100%;" />
        <p v-if="coverFile" class="muted text-sm" style="margin-top: 0.25rem;">
          已选择：{{ coverFile.name }}（{{ (coverFile.size / 1024 / 1024).toFixed(1) }} MB）{{ coverUploading ? "上传中…" : "" }}
        </p>
        <p v-if="coverMsg" class="muted text-sm" style="margin-top: 0.25rem;">{{ coverMsg }}</p>
        <p class="muted text-sm" style="margin-top: 0.25rem;">
          上传的图片将保存为作品封面，列表页和详情页都会展示。仅允许常见位图格式（jpg/png/webp 等）。
        </p>
      </div>

      <!-- 外部链接 -->
      <div class="form-item">
        <label>外部链接</label>
        <div class="link-row">
          <input v-model="newLinkSite" class="input" placeholder="站点名（可选）" />
          <input v-model="newLinkUrl" class="input input-grow" placeholder="https://..." />
          <button type="button" class="btn btn-ghost" @click="addLink">+</button>
        </div>
        <div v-if="linkList.length" style="display: flex; flex-direction: column; gap: 0.25rem;">
          <div v-for="(l, i) in linkList" :key="i" class="row row-between" style="font-size: 0.85rem;">
            <span>{{ l.site_name }}: {{ l.url }}</span>
            <button type="button" class="btn btn-ghost" style="padding: 0.1rem 0.4rem;" @click="removeLink(i)">×</button>
          </div>
        </div>
      </div>

      <!-- 单文件上传（可选，主本/视频/音频等） -->
      <div class="form-item">
        <label>主文件（可选）</label>
        <input ref="fileInput" type="file" @change="onFileChange" style="width: 100%;" />
        <p v-if="file" class="muted text-sm" style="margin-top: 0.25rem;">
          已选择：{{ file.name }}（{{ (file.size / 1024 / 1024).toFixed(1) }} MB）
        </p>
      </div>

      <!-- 批量连载文件上传（作者分章陆续上传，或一次性批量上传） -->
      <div class="form-item">
        <label>
          {{ type === "anime"
            ? "连载剧集批量上传（可选，可多选；上传后按顺序作为第 1~N 集在线观看）"
            : type === "movie"
              ? "电影文件批量上传（可选，可多选；上传后按顺序作为第 1~N 部/个视频在线观看）"
              : type === "gallery"
                ? "图集图片批量上传（可选，可多选；上传后按顺序作为第 1~N 张在线浏览；第一张自动作为封面）"
                : "连载章节批量上传（可选，可多选；上传后按顺序作为第 1~N 章在线阅读）" }}
        </label>
        <input ref="batchFileInput" type="file" multiple @change="onBatchSelect" style="width: 100%;" />
        <div v-if="batchFiles.length" style="margin-top: 0.5rem; display: flex; flex-direction: column; gap: 4px;">
          <div v-for="(f, i) in batchFiles" :key="`${i}-${f.name}`" class="row row-between" style="font-size: 0.85rem;">
            <span>#{{ i + 1 }} {{ f.name }}（{{ (f.size / 1024 / 1024).toFixed(1) }} MB）</span>
            <button type="button" class="btn btn-ghost" style="padding: 0.1rem 0.4rem;" @click="removeBatch(i)">×</button>
          </div>
        </div>
      </div>

      <!-- 外站直链（可选，可多条；不上传本体，站内观看/下载经后端代理拉取） -->
      <div class="form-item">
        <label>外站直链（可选，可多条；不上传本体，站内观看/下载经后端代理拉取）</label>
        <div class="link-row">
          <input v-model="newDirectUrl" class="input input-grow" :placeholder="directUrlPlaceholder" @keyup.enter.prevent="addDirectUrlItem" />
          <button type="button" class="btn btn-ghost" @click="addDirectUrlItem">+</button>
        </div>
        <div v-if="directUrls.length" style="display: flex; flex-direction: column; gap: 0.25rem;">
          <div v-for="(u, i) in directUrls" :key="i" class="row row-between" style="font-size: 0.85rem; word-break: break-all;">
            <span>{{ u }}</span>
            <button type="button" class="btn btn-ghost" style="padding: 0.1rem 0.4rem;" @click="removeDirectUrlItem(i)">×</button>
          </div>
        </div>
      </div>

      <!-- 提交 -->
      <button type="submit" class="btn btn-primary" :disabled="submitting" style="width: 100%; padding: 0.75rem;">
        {{ submitting ? "提交中..." : "发布作品" }}
      </button>
    </form>
  </div>
</template>

<style scoped>
/* 链接输入行：窄屏时整体换行堆叠，避免挤压 */
.link-row {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
  flex-wrap: wrap;
}
.link-row .input {
  flex: 1;
  min-width: 0;
}
.link-row .input.input-grow {
  flex: 2;
}
@media (max-width: 480px) {
  .link-row {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
