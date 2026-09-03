<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink, useRouter } from "vue-router";
import { extractErrMsg, request } from "@/api/http";
import { attachmentFromUrl, worksClient } from "@/api";
import { useUserStore } from "@/stores/user";
import type { WorkDetail, WorkFile, WorkChapter } from "@/types/api";
import Avatar from "@/components/Avatar.vue";
import FilePreview from "@/components/FilePreview.vue";

const props = defineProps<{ id: string | number }>();
const router = useRouter();
const user = useUserStore();

const work = ref<WorkDetail | null>(null);
const loading = ref(false);
const err = ref("");

// ------------------- 权限：上传者本人 或 管理员 -------------------
const canManage = computed(() => {
  if (!work.value || !user.current) return false;
  return user.isAdmin || work.value.uploader?.id === user.current.id;
});

// ------------------- 编辑模式：标题/作者/简介/标签/链接整体替换 -------------------
const editMode = ref(false);
const eTitle = ref("");
const eAuthor = ref("");
const eSummary = ref("");
const eTags = ref("");
const eLinks = ref<{ id?: number; site_name: string | null; url: string }[]>([]);
const editing = ref(false);
const editErr = ref("");

function openEdit() {
  if (!work.value) return;
  editMode.value = true;
  eTitle.value = work.value.title;
  eAuthor.value = work.value.author ?? "";
  eSummary.value = work.value.summary ?? "";
  eTags.value = (work.value.tags || []).join(", ");
  eLinks.value = (work.value.links || []).map((l) => ({ id: l.id, site_name: l.site_name, url: l.url }));
}
function closeEdit() {
  editMode.value = false;
  editErr.value = "";
}
function addLink() {
  eLinks.value.push({ site_name: null, url: "" });
}
function removeLink(i: number) {
  eLinks.value.splice(i, 1);
}
async function saveEdit() {
  if (!work.value) return;
  if (!eTitle.value.trim()) {
    editErr.value = "标题不能为空";
    return;
  }
  editing.value = true;
  editErr.value = "";
  try {
    const tags = eTags.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean);
    const links = eLinks.value
      .filter((l) => (l.url || "").trim())
      .map((l) => ({ site_name: l.site_name || null, url: l.url.trim() }));
    const updated = await worksClient.update(work.value.id, {
      title: eTitle.value.trim(),
      author: eAuthor.value.trim() || null,
      summary: eSummary.value.trim() || null,
      tags,
      links,
    });
    work.value = updated;
    editMode.value = false;
  } catch (e) {
    editErr.value = extractErrMsg(e, "保存失败");
  } finally {
    editing.value = false;
  }
}

// ------------------- 封面图上传（仅限 canManage） -------------------
const coverFileInput = ref<HTMLInputElement | null>(null);
const coverUploading = ref(false);
const coverMsg = ref("");
function onCoverSelect(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0];
  if (f) void uploadCover(f);
}
async function uploadCover(file: File) {
  if (!work.value) return;
  coverUploading.value = true;
  coverMsg.value = "";
  try {
    const url = await worksClient.uploadCover(work.value.id, file);
    work.value = { ...work.value, cover_url: url };
  } catch (e) {
    coverMsg.value = extractErrMsg(e, "封面上传失败");
  } finally {
    coverUploading.value = false;
  }
}

// ------------------- 作品文件：单一上传入口（可单可多）+ 删除 -------------------
const pendingFiles = ref<File[]>([]);
const fileInput = ref<HTMLInputElement | null>(null);
const uploading = ref(false);
const uploadMsg = ref("");

function onFilesSelect(e: Event) {
  const fs = Array.from((e.target as HTMLInputElement).files ?? []);
  if (fs.length) pendingFiles.value.push(...fs);
  if (fileInput.value) fileInput.value.value = "";
}
function removePending(i: number) {
  pendingFiles.value.splice(i, 1);
}
async function upload() {
  if (!pendingFiles.value.length || !work.value) return;
  uploading.value = true;
  uploadMsg.value = "";
  try {
    await worksClient.uploadFilesBatch(work.value.id, pendingFiles.value);
    uploadMsg.value = `已成功上传 ${pendingFiles.value.length} 个文件`;
    pendingFiles.value = [];
    await load();
  } catch (e) {
    uploadMsg.value = extractErrMsg(e, "上传失败");
  } finally {
    uploading.value = false;
  }
}

// ------------------- 外站直链添加（不上传本体，站内阅读/下载走后端代理） -------------------
const directUrl = ref("");
const directName = ref("");
const addingDirect = ref(false);
async function addDirectUrl() {
  const url = directUrl.value.trim();
  if (!url || !work.value || addingDirect.value) return;
  addingDirect.value = true;
  uploadMsg.value = "";
  try {
    await worksClient.fileByUrl(work.value.id, url, directName.value.trim() || undefined);
    uploadMsg.value = "已成功添加直链文件";
    directUrl.value = "";
    directName.value = "";
    await load();
  } catch (e) {
    uploadMsg.value = extractErrMsg(e, "直链添加失败");
  } finally {
    addingDirect.value = false;
  }
}

async function removeFile(f: WorkFile) {
  if (!work.value || !window.confirm(`确定删除文件「${f.file_name}」？`)) return;
  try {
    await worksClient.removeFile(work.value.id, f.id);
    await load();
  } catch (e) {
    window.alert(extractErrMsg(e, "删除失败"));
  }
}

// ------------------- 章节化：作品页只预览第一章，完整阅读跳新标签页 -------------------
const chapterMode = ref<"split" | "file">("file");
const chapters = ref<WorkChapter[]>([]);

/** 番剧/动漫：文案用「集」替代「章」 */
const isAnime = computed(() => work.value?.type === "anime");
/** 电影：文案用「观看 / 下载」替代章节 */
const isMovie = computed(() => (work.value?.type ?? "") === "movie");

const firstChap = computed<WorkChapter | null>(() => chapters.value[0] ?? null);
const activeFile = computed<WorkFile | null>(() => {
  if (!firstChap.value || !work.value) return null;
  return work.value.external_files.find((f) => f.id === firstChap.value!.file_id) || null;
});

/** 单文件文内切章时，第一章切片文本（走 /raw 代理，UTF-8 无乱码） */
const chapterText = ref("");
const chapterTextLoading = ref(false);
const chapterTextErr = ref("");
watch(firstChap, async (c) => {
  if (!c) return;
  if (chapterMode.value === "split") {
    chapterTextLoading.value = true;
    chapterTextErr.value = "";
    try {
      const res = await fetch(
        worksClient.rawUrl(props.id, c.file_id, c.start, c.end ?? undefined),
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const buf = await res.arrayBuffer();
      chapterText.value = new TextDecoder("utf-8", { fatal: false }).decode(new Uint8Array(buf));
    } catch (e: any) {
      chapterTextErr.value = e?.message || "章节加载失败";
      chapterText.value = "";
    } finally {
      chapterTextLoading.value = false;
    }
  }
}, { immediate: true });

async function loadChapters() {
  try {
    const res = await worksClient.chapters(props.id);
    chapterMode.value = res.mode;
    chapters.value = res.chapters;
  } catch {
    chapterMode.value = "file";
    chapters.value = [];
  }
}

/** 第一章：新标签页站内阅读地址（完整阅读） */
const readerUrl = computed(() => {
  if (!firstChap.value) return "";
  return `/read/${props.id}?file=${firstChap.value.file_id}&chapter=${firstChap.value.index}`;
});

// ------------------- 评论 -------------------
const reviews = ref<any[]>([]);
const reviewTotal = ref(0);
const reviewText = ref("");
const reviewRating = ref<number | null>(null);
const reviewFile = ref<File | null>(null);
const reviewFileInput = ref<HTMLInputElement | null>(null);
const reviewDirectUrl = ref("");
const reviewSubmitting = ref(false);
const reviewMsg = ref("");

function onReviewFileSelect(e: Event) {
  reviewFile.value = (e.target as HTMLInputElement).files?.[0] ?? null;
}

// ------------------- 同人 -------------------
const fanworks = ref<any[]>([]);
const fanworkTotal = ref(0);

// ------------------- 管理员：删除作品 -------------------
const deleting = ref(false);
async function deleteWork() {
  if (!work.value) return;
  const msg = `确定删除作品「${work.value.title}」？链接、文件、评论将一并级联清理。此操作不可恢复。`;
  if (!window.confirm(msg)) return;
  if (!user.isAdmin) return;
  deleting.value = true;
  try {
    await worksClient.remove(work.value.id);
    router.push("/works");
  } catch (e) {
    window.alert(extractErrMsg(e, "删除失败"));
  } finally {
    deleting.value = false;
  }
}

const TYPE_LABELS: Record<string, string> = {
  novel: "小说",
  anime: "动画",
  fanwork: "同人",
  comic: "漫画",
  game: "游戏",
  other: "其他",
};
function typeLabel(t: string) {
  return TYPE_LABELS[t] ?? t;
}

function fmtBytes(b?: number | null) {
  if (!b) return "";
  if (b < 1024) return `${b}B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)}KB`;
  return `${(b / 1024 / 1024).toFixed(1)}MB`;
}
function fmtDate(s?: string | null) {
  if (!s) return "";
  return s.replace("T", " ").slice(0, 16);
}

async function loadReviews() {
  try {
    const res = await request<{ total: number; items: any[] }>({
      url: `/works/${props.id}/reviews`,
      params: { page: 1, page_size: 50 },
    });
    reviews.value = res.items;
    reviewTotal.value = res.total;
  } catch {
    reviews.value = [];
  }
}

async function loadFanworks() {
  try {
    const res = await request<{ total: number; items: any[] }>({
      url: `/fanworks/`,
      params: { work_id: Number(props.id), page_size: 20 },
    });
    fanworks.value = res.items;
    fanworkTotal.value = res.total;
  } catch {
    fanworks.value = [];
  }
}

async function submitReview() {
  if (!reviewText.value.trim()) return;
  reviewSubmitting.value = true;
  reviewMsg.value = "";
  try {
    let attachments: any[] = [];
    if (reviewFile.value) {
      const fd = new FormData();
      fd.append("file", reviewFile.value);
      const upRes = await request<{ attachment: any }>({
        url: `/works/${props.id}/reviews/attachments`,
        method: "POST",
        data: fd,
        headers: { "Content-Type": "multipart/form-data" },
      });
      attachments = [upRes.attachment];
    }
    const direct = reviewDirectUrl.value.trim();
    if (direct) attachments.push(attachmentFromUrl(direct));
    await request({
      url: `/works/${props.id}/reviews`,
      method: "POST",
      data: {
        rating: reviewRating.value,
        content: reviewText.value.trim(),
        attachments,
      },
    });
    reviewText.value = "";
    reviewRating.value = null;
    reviewFile.value = null;
    if (reviewFileInput.value) reviewFileInput.value.value = "";
    await loadReviews();
  } catch (e) {
    reviewMsg.value = extractErrMsg(e, "评论失败");
  } finally {
    reviewSubmitting.value = false;
  }
}

async function load() {
  loading.value = true;
  err.value = "";
  try {
    work.value = await worksClient.detail(props.id);
    await loadChapters();
    await loadReviews();
    await loadFanworks();
  } catch (e) {
    err.value = extractErrMsg(e, "获取作品详情失败");
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <section class="detail card">
    <div v-if="loading" class="muted">加载中…</div>
    <div v-else-if="err" class="alert alert-error">{{ err }}</div>
    <template v-else-if="work">
      <header class="head">
        <!-- 封面图：有封面 URL 用真实图，否则退回首字母色块 -->
        <div class="cover-wrap">
          <img v-if="work.cover_url" class="cover cover-img" :src="work.cover_url" :alt="work.title" />
          <div v-else class="cover">{{ work.title.slice(0, 1) }}</div>
          <div v-if="canManage" style="margin-top: 8px;">
            <label class="btn btn-ghost btn-sm" style="width: 100%; display: inline-block; text-align: center; cursor: pointer;">
              换封面
              <input ref="coverFileInput" type="file" accept="image/*" @change="onCoverSelect" style="display: none;" />
            </label>
            <p v-if="coverMsg" class="text-sm alert alert-error" style="margin-top: 4px; padding: 2px 6px;">{{ coverMsg }}</p>
            <p v-if="coverUploading" class="text-sm muted" style="margin-top: 4px;">封面上传中…</p>
          </div>
        </div>

        <div class="meta">
          <div class="row-between">
            <h1 style="margin: 0">
              {{ work.title }}
            </h1>
            <div style="display: flex; gap: 6px; align-items: center; flex-wrap: wrap;">
              <span class="badge" :class="work.type === 'fanwork' ? 'badge-admin' : 'badge-muted'">{{ typeLabel(work.type) }}</span>
              <button v-if="canManage && !editMode" type="button" class="btn btn-ghost btn-sm" @click="openEdit">编辑资料</button>
              <button v-if="user.isAdmin" type="button" class="btn btn-danger btn-sm" :disabled="deleting" @click="deleteWork">
                {{ deleting ? "删除中…" : "删除作品" }}
              </button>
            </div>
          </div>

          <!-- 编辑模式 -->
          <div v-if="editMode" class="edit-card">
            <div class="form-item">
              <label>标题 *</label>
              <input v-model="eTitle" class="input" />
            </div>
            <div class="form-item">
              <label>作者</label>
              <input v-model="eAuthor" class="input" placeholder="作者（可选）" />
            </div>
            <div class="form-item">
              <label>简介</label>
              <textarea v-model="eSummary" class="input" rows="3"></textarea>
            </div>
            <div class="form-item">
              <label>标签（逗号分隔）</label>
              <input v-model="eTags" class="input" placeholder="科幻, 长篇, 经典" />
            </div>
            <div class="form-item">
              <label>外部链接（新增 / 编辑 / 删除后整体保存）</label>
              <div v-for="(l, i) in eLinks" :key="`el-${i}`" class="row" style="gap: 6px; margin-bottom: 6px;">
                <input v-model="l.site_name" class="input" style="flex: 1;" placeholder="站点名" />
                <input v-model="l.url" class="input" style="flex: 2;" placeholder="https://…" />
                <button type="button" class="btn btn-ghost btn-sm" @click="removeLink(i)">×</button>
              </div>
              <button type="button" class="btn btn-ghost btn-sm" @click="addLink">+ 添加一行</button>
            </div>
            <div class="row" style="gap: 6px; justify-content: flex-end; margin-top: 6px;">
              <button type="button" class="btn btn-ghost" @click="closeEdit">取消</button>
              <button type="button" class="btn btn-primary" :disabled="editing" @click="saveEdit">
                {{ editing ? "保存中…" : "保存修改" }}
              </button>
            </div>
            <p v-if="editErr" class="alert alert-error text-sm" style="margin-top: 6px;">{{ editErr }}</p>
          </div>

          <!-- 只读展示 -->
          <template v-else>
            <p class="muted text-sm" v-if="work.author">作者：{{ work.author }}</p>
            <p class="muted text-sm" v-if="work.source_work_title">
              原作：<RouterLink :to="`/works/${work.source_work_id}`">{{ work.source_work_title }}</RouterLink>
            </p>
            <p v-if="work.summary" class="synopsis">{{ work.summary }}</p>
            <p v-if="work.uploader" class="muted text-sm">
              上传者：{{ work.uploader.nickname }}（QQ {{ work.uploader.qq }}）
            </p>
          </template>
        </div>
      </header>

      <!-- 阅读/观看关系 -->
      <section v-if="work.relations" class="rels">
        <h3>{{ isAnime || isMovie ? "观看关系" : "阅读关系" }}</h3>
        <div class="rel-stat">
          <div>
            <div class="stat-num">{{ work.relations.supporter_count }}</div>
            <div class="muted text-sm">支持者{{ work.relations.show_supporters ? "（名单已展开）" : "（达到阈值后展开）" }}</div>
          </div>
          <div>
            <div class="stat-num">{{ work.relations.recommender_count }}</div>
            <div class="muted text-sm">推荐者{{ work.relations.show_recommenders ? "（名单已展开）" : "（达到阈值后展开）" }}</div>
          </div>
          <div>
            <div class="muted text-sm">{{ isAnime || isMovie ? "观看分布" : "阅读分布" }}</div>
            <ul class="stat-list">
              <li v-for="(v, k) in work.relations.reading_stats" :key="k">
                <span>{{ k }}</span><b>{{ v }}</b>
              </li>
              <li v-if="Object.keys(work.relations.reading_stats).length === 0" class="muted">暂无</li>
            </ul>
          </div>
        </div>
      </section>

      <!-- 在线链接 -->
      <section v-if="(work.links?.length || editMode)" class="block">
        <div class="row-between" style="margin-bottom: 8px;">
          <h3 style="margin: 0;">📎 在线链接</h3>
          <button v-if="canManage && !editMode" class="btn btn-ghost btn-sm" @click="openEdit">编辑链接</button>
        </div>
        <div v-if="!editMode && !work.links?.length" class="muted text-sm">暂无外链；上传者或管理员可点右上「编辑资料」添加。</div>
        <ul v-if="!editMode && work.links?.length" class="link-list">
          <li v-for="l in work.links" :key="`wl-${l.id ?? l.url}`">
            <span class="badge badge-muted">{{ l.site_name || "链接" }}</span>
            <a :href="l.url" target="_blank" rel="noreferrer">{{ l.url }}</a>
          </li>
        </ul>
      </section>

      <!-- 文件：只预览第一章，完整阅读跳新标签页（站内阅读 / 站外链接） -->
      <section v-if="work.external_files?.length || canManage" class="block">
        <div class="row-between" style="margin-bottom: 8px; align-items: center; flex-wrap: wrap; gap: 8px;">
          <h3 style="margin: 0;">📁 文件（共 {{ work.external_files?.length ?? 0 }} 个）</h3>
          <div v-if="work.external_files?.length" class="row" style="gap: 6px; align-items: center; flex-wrap: wrap;">
            <a :href="worksClient.downloadUrl(work.id)" class="btn btn-primary btn-sm">⬇ 下载整本</a>
          </div>
        </div>

        <div v-if="!work.external_files?.length" class="muted text-sm">
          暂无文件；上传者或管理员可在下方「上传文件」区块添加。
        </div>

        <template v-else>
          <!-- 内联预览：只预览第一章 -->
          <div v-if="firstChap" class="preview-block">
            <div class="row-between" style="margin-bottom: 6px; flex-wrap: wrap; gap: 6px;">
              <h4 style="margin: 0; font-size: 14px;">
                <template v-if="chapterMode === 'split'">📖 预览：第 {{ firstChap.index + 1 }} 章 · {{ firstChap.title }}</template>
                <template v-else-if="isAnime">📺 预览：第 {{ firstChap.index + 1 }} 集 · {{ firstChap.file_name }}</template>
                <template v-else-if="isMovie">🎬 预览：{{ firstChap.file_name }}</template>
                <template v-else>📖 预览：第一文件 · {{ firstChap.file_name }}</template>
              </h4>
              <div class="row" style="gap: 6px; flex-wrap: wrap;">
                <a :href="readerUrl" target="_blank" rel="noreferrer" class="btn btn-ghost btn-sm">
                  {{ isAnime ? "观看 / 选集下载" : isMovie ? "观看 / 下载" : "阅读 / 选章下载" }}
                </a>
                <a
                  v-for="l in work.links ?? []"
                  :key="`rl-${l.id ?? l.url}`"
                  :href="l.url"
                  target="_blank"
                  rel="noreferrer"
                  class="btn btn-ghost btn-sm"
                >{{ isAnime || isMovie ? "站外观看" : "站外阅读" }}：{{ l.site_name || l.url }}</a>
              </div>
            </div>
            <template v-if="chapterMode === 'split'">
              <div v-if="chapterTextLoading" class="muted text-sm">章节加载中…</div>
              <div v-else-if="chapterTextErr" class="alert alert-error text-sm">{{ chapterTextErr }}</div>
              <FilePreview v-else :url="worksClient.rawUrl(work.id, firstChap.file_id, firstChap.start, firstChap.end ?? undefined)" :file-name="firstChap.file_name" :mime-type="'text/plain; charset=utf-8'" :text-content="chapterText" />
            </template>
            <FilePreview
              v-else-if="activeFile"
              :url="activeFile.url"
              :file-name="activeFile.file_name"
              :mime-type="activeFile.mime_type"
              :size-bytes="activeFile.size_bytes"
            />
          </div>
        </template>

        <!-- 文件管理（上传者/管理员：直链下载 + 删除） -->
        <div v-if="canManage" class="file-mgmt">
          <h4 style="margin: 0 0 6px; font-size: 13px; color: #6b7280;">文件管理（上传者 / 管理员）</h4>
          <ul class="file-list" style="margin-bottom: 0;">
            <li v-for="f in work.external_files" :key="`wm-${f.id}`" class="file-row">
              <div class="file-row-main" style="cursor: default;">
                <span v-if="f.mime_type" class="muted text-sm">{{ f.mime_type }}</span>
                <span class="muted text-sm">{{ fmtBytes(f.size_bytes) }}</span>
                <span class="muted text-sm">
                  上传：{{ fmtDate(f.created_at) }} · {{ f.uploader?.nickname || `QQ${f.uploader?.qq ?? ""}` }}
                </span>
              </div>
              <div class="row" style="gap: 6px;">
                <a class="btn btn-ghost btn-sm" :href="f.url" :download="f.file_name || undefined" target="_blank" rel="noreferrer">⬇ 直链</a>
                <button class="btn btn-danger btn-sm" @click="removeFile(f)">删除</button>
              </div>
            </li>
          </ul>
        </div>
      </section>

      <!-- 上传文件：单一入口，可传单个也可传多个（连载按文件先后顺序作为章节，可分次追加） -->
      <section v-if="canManage" class="block">
        <h3>
          ⬆️ 上传文件（可单个，可多个；{{
            isAnime
              ? "每文件 = 一集，上传顺序即集数顺序"
              : isMovie
                ? "每文件 = 一部/个视频，上传先后即排列顺序"
                : "连载每文件 = 一章，上传顺序即章节顺序"
          }}）
        </h3>
        <div class="row" style="gap: 0.75rem; align-items: center; flex-wrap: wrap;">
          <input ref="fileInput" type="file" multiple @change="onFilesSelect" style="flex: 1; min-width: 220px;" />
          <button class="btn btn-primary" :disabled="!pendingFiles.length || uploading" @click="upload">
            {{ uploading ? "上传中..." : `上传${pendingFiles.length ? `（${pendingFiles.length} 个）` : ""}` }}
          </button>
        </div>
        <div v-if="pendingFiles.length" style="margin-top: 8px; display: flex; flex-direction: column; gap: 4px;">
          <div v-for="(f, i) in pendingFiles" :key="`p-${i}-${f.name}`" class="row row-between" style="font-size: 0.85rem;">
            <span>#{{ i + 1 }}：{{ f.name }}（{{ (f.size / 1024 / 1024).toFixed(1) }} MB）</span>
            <button type="button" class="btn btn-ghost btn-sm" @click="removePending(i)">×</button>
          </div>
        </div>
        <p v-if="uploadMsg" class="text-sm" :class="uploadMsg.startsWith('已成功') ? 'alert alert-success' : 'alert alert-error'" style="margin-top: 0.4rem;">
          {{ uploadMsg }}
        </p>
        <div class="row" style="gap: 0.5rem; margin-top: 10px; align-items: center; flex-wrap: wrap;">
          <input v-model="directUrl" class="input" placeholder="或粘贴外站直链（视频/图片/文档 URL，不上传本体）" style="flex: 2; min-width: 260px;" />
          <input v-model="directName" class="input" placeholder="文件名（可选）" style="flex: 1; min-width: 140px;" />
          <button class="btn btn-ghost" :disabled="!directUrl.trim() || addingDirect" @click="addDirectUrl">
            {{ addingDirect ? "添加中..." : "直链添加" }}
          </button>
        </div>
        <p v-if="isAnime" class="muted text-sm" style="margin-top: 12px;">
          <b>剧集说明：</b>每个文件一集，按上传先后排集序（第一集 = 第一个文件），可分次追加上传。下载整本：单文件 = 原文件；多文件 = 打包为一个 ZIP。选集下载（在观看页勾选）= 把所选集打包下载。
        </p>
        <p v-else-if="isMovie" class="muted text-sm" style="margin-top: 12px;">
          <b>电影说明：</b>每个文件一部/一个视频（如分上下部、多版本），按上传先后排列，可分次追加。下载整本：单文件 = 原文件；多文件 = 打包为一个 ZIP。多选下载（在观看页勾选）= 把所选文件打包下载。
        </p>
        <p v-else class="muted text-sm" style="margin-top: 12px;">
          <b>连载说明：</b>可分次追加上传，文件按先后顺序作为章节。下载整本：单文件 = 原文件；多文件 = 合并为一个完整 TXT（含二进制则打包 ZIP）。选章下载（在阅读页勾选）= 把所选章节按序提取合并为一个文件。
        </p>
      </section>

      <!-- 同人创作（F8） -->
      <section class="block">
        <h3>✍️ 同人创作（{{ fanworkTotal }}）</h3>
        <div v-if="fanworks.length === 0" class="muted text-sm">暂无同人创作</div>
        <ul v-else class="fanwork-list">
          <li v-for="fw in fanworks" :key="fw.id">
            <RouterLink :to="`/fanworks/${fw.id}`">{{ fw.title }}</RouterLink>
            <span class="muted text-sm">by {{ fw.author?.nickname || `群友${fw.author?.qq ?? ""}` }}</span>
          </li>
        </ul>
        <RouterLink :to="`/fanworks/new?work=${work.id}`" class="btn btn-ghost" style="margin-top: 0.5rem;">
          ✍️ 写同人
        </RouterLink>
      </section>

      <!-- 评论 -->
      <section class="block">
        <h3>💬 评论（{{ reviewTotal }}）</h3>

        <!-- 评论列表 -->
        <div v-if="reviews.length === 0" class="muted text-sm">暂无评论</div>
        <div v-else class="review-list">
          <div v-for="r in reviews" :key="r.id" class="review-item">
            <div class="review-head">
              <Avatar :src="r.avatar_url" :name="r.nickname" :size="26" />
              <span class="review-nick">{{ r.nickname }}</span>
              <span v-if="r.rating" class="review-rating">{{ "★".repeat(r.rating) }}{{ "☆".repeat(5 - r.rating) }}</span>
              <span class="muted text-sm">{{ r.created_at?.slice(0, 10) }}</span>
            </div>
            <p class="review-body">{{ r.content }}</p>
            <div v-if="r.attachments?.length" class="review-atts">
              <a v-for="a in r.attachments" :key="a.url" :href="a.url" target="_blank" rel="noreferrer" class="badge badge-muted">
                {{ a.type === "image" ? "🖼" : a.type === "video" ? "🎬" : "📎" }} {{ a.file_name }}
              </a>
            </div>
          </div>
        </div>

        <!-- 发表评论 -->
        <div class="review-form">
          <div class="row" style="gap: 0.5rem; margin-bottom: 0.5rem;">
            <select v-model="reviewRating" class="input" style="width: auto;">
              <option :value="null">不评分</option>
              <option :value="5">★★★★★</option>
              <option :value="4">★★★★</option>
              <option :value="3">★★★</option>
              <option :value="2">★★</option>
              <option :value="1">★</option>
            </select>
            <input ref="reviewFileInput" type="file" @change="onReviewFileSelect" style="flex: 1;" />
          </div>
          <input v-model="reviewDirectUrl" class="input" placeholder="或粘贴直链 URL 作为附件（图片/视频/文档）" style="margin-bottom: 0.5rem; width: 100%;" />
          <textarea v-model="reviewText" class="input" rows="3" placeholder="写评论...（可附带文件或直链）"></textarea>
          <div class="row" style="justify-content: flex-end; margin-top: 0.5rem;">
            <button class="btn btn-primary" :disabled="!reviewText.trim() || reviewSubmitting" @click="submitReview">
              {{ reviewSubmitting ? "提交中..." : "发表评论" }}
            </button>
          </div>
          <p v-if="reviewMsg" class="alert alert-error text-sm">{{ reviewMsg }}</p>
        </div>
      </section>

      <!-- 标签 -->
      <section v-if="work.tags?.length" class="block">
        <h3>🏷 标签</h3>
        <div class="row" style="flex-wrap: wrap">
          <span v-for="t in work.tags" :key="t" class="badge badge-muted">{{ t }}</span>
        </div>
      </section>
    </template>
  </section>
</template>

<style scoped>
.detail { padding: 24px; }
.head { display: flex; gap: 20px; align-items: flex-start; }
.cover-wrap { flex-shrink: 0; display: flex; flex-direction: column; align-items: center; width: 120px; }
.cover { width: 96px; height: 128px; border-radius: 10px; background: var(--accent-gradient); color: #fff; display: flex; align-items: center; justify-content: center; font-size: 40px; font-weight: 700; }
.cover.cover-img { object-fit: cover; background: #f3f4f6; border: 1px solid var(--color-border); }
.meta { flex: 1; display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.synopsis { margin: 0; line-height: 1.7; }
h3 { margin: 0 0 12px; font-size: 15px; }
h4 { font-weight: 600; }
.rels, .block { margin-top: 22px; padding-top: 18px; border-top: 1px dashed var(--color-border); }
.rel-stat { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }
.stat-num { font-size: 26px; font-weight: 800; color: var(--color-primary); }
.stat-list { list-style: none; padding: 0; margin: 8px 0 0; display: flex; flex-direction: column; gap: 4px; font-size: 14px; }
.stat-list li { display: flex; justify-content: space-between; padding: 4px 10px; background: #f9fafb; border-radius: 6px; }
.link-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 8px; font-size: 14px; }
.link-list li { display: flex; align-items: center; gap: 10px; }
.fanwork-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 6px; font-size: 14px; }
.fanwork-list li { display: flex; align-items: center; gap: 8px; }
.review-list { display: flex; flex-direction: column; gap: 12px; margin-bottom: 16px; }
.review-item { padding: 12px; background: #f9fafb; border-radius: 8px; }
.review-head { display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }
.review-nick { font-weight: 600; font-size: 14px; }
.review-rating { color: #f59e0b; font-size: 13px; }
.review-body { margin: 0; font-size: 14px; line-height: 1.6; }
.review-atts { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.review-form { margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--color-border); display: flex; flex-direction: column; gap: 8px; }

.edit-card {
  padding: 12px 14px;
  border-radius: 10px;
  border: 1px solid var(--color-border);
  background: #fafbfc;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.form-item { display: flex; flex-direction: column; gap: 4px; }
.form-item > label { font-size: 13px; color: #374151; font-weight: 500; }

.file-list {
  list-style: none;
  padding: 0;
  margin: 0 0 16px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 14px;
}
.file-row {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: center;
  padding: 8px 10px;
  border-radius: 8px;
  background: #f9fafb;
  border: 1px solid transparent;
}
.file-row-main {
  flex: 1;
  min-width: 0;
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}

.preview-block {
  padding: 14px;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  background: #fff;
}

.file-mgmt { margin-top: 14px; padding-top: 12px; border-top: 1px dashed var(--color-border); }
.file-mgmt .file-row { background: transparent; border: 1px solid var(--color-border); }

.btn-sm { padding: 2px 10px !important; font-size: 12px !important; }
.btn-danger { background: #dc2626 !important; color: #fff !important; border-color: #dc2626 !important; }
.btn-danger:hover { background: #b91c1c !important; }
.btn-secondary { background: #6b7280 !important; color: #fff !important; border-color: #6b7280 !important; }

@media (max-width: 820px) {
  .head { flex-direction: column; align-items: stretch; }
  .cover-wrap { width: 100%; align-items: flex-start; }
  .rel-stat { grid-template-columns: 1fr; }
  .file-row { flex-direction: column; align-items: flex-start; }
}
</style>
