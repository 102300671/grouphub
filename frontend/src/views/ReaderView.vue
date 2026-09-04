<script setup lang="ts">
/**
 * 站内阅读页（独立全屏，供作品页「阅读 / 选章下载」入口使用）。
 *
 * - 单文件文本：按后端切分章节，章节内容走 /raw 代理（text/plain; charset=utf-8，无乱码）
 * - 多文件 / 非文本：每文件一章，FilePreview 按类型渲染（图片/视频/音频/PDF/文本/下载）
 * - 布局：顶栏固定；章节侧栏可收起/展开，展开时整体固定不随页面滚动（仅章节列表内部滚动，头部固定）；
 *   正文区整块固定在视口内：章节标题固定、中间正文独立滚动、底部翻页固定
 * - 下载：整本（单文件 = 原样下载；多文件 = 合并 TXT 或 ZIP）+ 当前章节 + 勾选章节下载（按序合并为一个文件）
 */
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { extractErrMsg } from "@/api/http";
import { worksClient } from "@/api";
import type { WorkDetail, WorkChapter, WorkFile } from "@/types/api";
import FilePreview from "@/components/FilePreview.vue";

const props = defineProps<{ id: string | number }>();
const route = useRoute();
const router = useRouter();

const work = ref<WorkDetail | null>(null);
const mode = ref<"split" | "file">("file");
const chapters = ref<WorkChapter[]>([]);
const cur = ref(0);
const loading = ref(true);
const err = ref("");

/** 章节侧栏收起 / 展开 */
const sideOpen = ref(true);
/** 正文独立滚动容器 */
const scrollRef = ref<HTMLElement | null>(null);

/** 番剧/动漫：文案用「集」替代「章」；电影：用「部」 */
const isAnime = computed(() => work.value?.type === "anime");
const isMovie = computed(() => work.value?.type === "movie");
/** 图集：以「张」计，逐张浏览图片 */
const isGallery = computed(() => work.value?.type === "gallery");
const unit = computed(() => (isGallery.value ? "张" : isAnime.value ? "集" : isMovie.value ? "部" : "章"));
const unitPlural = computed(() => (isGallery.value ? "图片" : isAnime.value ? "剧集" : isMovie.value ? "影片" : "章节"));

const textContent = ref("");
const textLoading = ref(false);
const textErr = ref("");

const current = computed<WorkChapter | null>(() => chapters.value[cur.value] ?? null);
const currentFile = computed<WorkFile | null>(() => {
  const c = current.value;
  if (!c || !work.value) return null;
  return work.value.external_files.find((f) => f.id === c.file_id) ?? null;
});

/** 当前章节的下载直链：split 模式 = 抽取该章；file 模式 = 下载该文件 */
const chapterDownloadUrl = computed(() => {
  if (!current.value) return "";
  if (mode.value === "split") {
    return worksClient.downloadUrl(props.id, { files: [current.value.file_id], chapters: [current.value.index] });
  }
  return worksClient.downloadUrl(props.id, { files: [current.value.file_id] });
});

// ------------------- 选章下载（勾选章节 → 按序合并为一个文件） -------------------
const selected = ref<Set<number>>(new Set());
function toggleSel(i: number) {
  const s = new Set(selected.value);
  if (s.has(i)) s.delete(i);
  else s.add(i);
  selected.value = s;
}
const selectedDownloadUrl = computed(() => {
  if (!chapters.value.length || selected.value.size === 0) return "";
  if (mode.value === "split") {
    const first = chapters.value.find((x) => selected.value.has(x.index));
    if (!first) return "";
    const idxs = chapters.value
      .filter((x) => selected.value.has(x.index))
      .map((x) => x.index)
      .sort((a, b) => a - b);
    return worksClient.downloadUrl(props.id, { files: [first.file_id], chapters: idxs });
  }
  const ids = chapters.value
    .filter((x) => selected.value.has(x.index))
    .map((x) => x.file_id);
  return worksClient.downloadUrl(props.id, { files: ids });
});

function syncQuery() {
  if (!current.value) return;
  router.replace({
    query: { file: String(current.value.file_id), chapter: String(current.value.index) },
  });
}

async function loadChapterText() {
  if (!current.value) return;
  if (mode.value !== "split") {
    textContent.value = "";
    return;
  }
  textLoading.value = true;
  textErr.value = "";
  try {
    const res = await fetch(
      worksClient.rawUrl(props.id, current.value.file_id, current.value.start, current.value.end ?? undefined),
    );
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const buf = await res.arrayBuffer();
    textContent.value = new TextDecoder("utf-8", { fatal: false }).decode(new Uint8Array(buf));
  } catch (e: any) {
    textErr.value = e?.message || "章节加载失败";
    textContent.value = "";
  } finally {
    textLoading.value = false;
  }
}

function go(delta: number) {
  const n = cur.value + delta;
  if (n >= 0 && n < chapters.value.length) cur.value = n;
}

async function load() {
  loading.value = true;
  err.value = "";
  try {
    work.value = await worksClient.detail(props.id);
    const res = await worksClient.chapters(props.id);
    mode.value = res.mode;
    chapters.value = res.chapters;
    // 支持 URL 参数定位章节（?file=&chapter=）
    if (chapters.value.length) {
      const chQ = Number(route.query.chapter);
      const fileQ = Number(route.query.file);
      if (Number.isInteger(chQ) && chQ >= 0 && chQ < chapters.value.length) {
        cur.value = chQ;
      } else if (fileQ) {
        const i = chapters.value.findIndex((c) => c.file_id === fileQ);
        if (i >= 0) cur.value = i;
      }
    }
  } catch (e) {
    err.value = extractErrMsg(e, "作品加载失败");
  } finally {
    loading.value = false;
  }
}

watch(current, () => {
  syncQuery();
  scrollRef.value?.scrollTo({ top: 0 });
  void loadChapterText();
});

onMounted(async () => {
  await load();
  void loadChapterText();
});
</script>

<template>
  <div class="reader">
    <!-- 顶栏 -->
    <header class="topbar">
      <RouterLink :to="`/works/${work?.id ?? id}`" class="back">← 返回作品页</RouterLink>
      <button
        v-if="chapters.length"
        type="button"
        class="btn btn-ghost btn-sm"
        @click="sideOpen = !sideOpen"
      >{{ sideOpen ? "📑 收起章节" : `📑 章节（${chapters.length}）` }}</button>
      <div class="title-block">
        <h1>{{ work?.title ?? "加载中…" }}</h1>
        <span v-if="work?.author" class="muted">作者：{{ work.author }}</span>
      </div>
      <div class="top-actions">
        <a
          v-if="work?.external_files?.length"
          :href="worksClient.downloadUrl(id)"
          class="btn btn-primary btn-sm"
        >⬇ 下载整本</a>
        <a
          v-for="l in work?.links ?? []"
          :key="l.id ?? l.url"
          :href="l.url"
          target="_blank"
          rel="noreferrer"
          class="btn btn-ghost btn-sm"
        >{{ unit === "章" ? "站外阅读" : "站外观看" }}：{{ l.site_name || l.url }}</a>
      </div>
    </header>

    <div v-if="loading" class="muted center">加载中…</div>
    <div v-else-if="err" class="alert alert-error center">{{ err }}</div>
    <div v-else-if="!work || chapters.length === 0" class="muted center">
      该作品暂无可阅读的文件。
      <RouterLink :to="`/works/${id}`" class="btn btn-ghost" style="margin-left: 8px;">返回作品页</RouterLink>
    </div>

    <div v-else class="body">
      <!-- 章节侧栏：可收起/展开；展开时整体固定，仅列表内部滚动，头部（标题 + 选章下载）固定 -->
      <aside v-show="sideOpen" class="side">
        <div class="side-head">
          <h3>{{ unitPlural }}（{{ chapters.length }}）</h3>
          <a
            v-if="selectedDownloadUrl"
            :href="selectedDownloadUrl"
            class="btn btn-primary btn-sm sel-dl"
          >⬇ 下载{{ unit === "张" ? "选图" : "选" + unit }}（{{ selected.size }}）</a>
        </div>
        <ul class="chap-list">
          <li
            v-for="c in chapters"
            :key="`${c.file_id}-${c.index}`"
            class="chap-item"
            :class="{ active: cur === c.index }"
            @click="cur = c.index"
          >
            <input
              type="checkbox"
              class="chap-check"
              :checked="selected.has(c.index)"
              @click.stop
              @change="toggleSel(c.index)"
            />
            <span class="chap-no">{{ c.index + 1 }}</span>
            <span class="chap-title">{{ c.title }}</span>
          </li>
        </ul>
      </aside>

      <!-- 正文：整块固定在视口内；标题固定、中间独立滚动、底部翻页固定 -->
      <main class="content">
        <div v-if="current" class="chap-head">
          <h2>{{ current.title }}</h2>
          <span v-if="mode === 'split'" class="muted text-sm">（单文件 · 第 {{ current.index + 1 }} / {{ chapters.length }} 章）</span>
          <span v-else class="muted text-sm">（{{ current.file_name }}）</span>
        </div>

        <div ref="scrollRef" class="content-scroll">
          <!-- 单文件文内切章：代理切片 + UTF-8 渲染，无乱码 -->
          <template v-if="mode === 'split'">
            <div v-if="textLoading" class="muted">章节加载中…</div>
            <div v-else-if="textErr" class="alert alert-error">{{ textErr }}</div>
            <pre v-else class="reading-text">{{ textContent }}</pre>
          </template>

          <!-- 每文件一章：按类型渲染（图片/视频/音频/PDF/文本/下载） -->
          <FilePreview
            v-else-if="currentFile"
            :url="currentFile.url"
            :file-name="currentFile.file_name"
            :mime-type="currentFile.mime_type"
            :size-bytes="currentFile.size_bytes"
          />
        </div>

        <!-- 翻页 + 本章下载 -->
        <div v-if="current" class="pager">
          <button class="btn btn-ghost" :disabled="cur === 0" @click="go(-1)">← 上一章</button>
          <a :href="chapterDownloadUrl" class="btn btn-secondary">⬇ 下载本章</a>
          <button class="btn btn-ghost" :disabled="cur === chapters.length - 1" @click="go(1)">下一章 →</button>
        </div>
      </main>
    </div>
  </div>
</template>

<style scoped>
.reader { min-height: 100vh; background: var(--color-bg, #f6f7f9); display: flex; flex-direction: column; }
.topbar {
  display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
  padding: 12px 20px; background: #fff; border-bottom: 1px solid var(--color-border);
  position: sticky; top: 0; z-index: 10;
}
.back { text-decoration: none; color: var(--color-primary); font-size: 14px; white-space: nowrap; }
.title-block { flex: 1; min-width: 0; }
.title-block h1 { margin: 0; font-size: 18px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.top-actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.center { text-align: center; padding: 60px 16px; }

.body { flex: 1; display: flex; gap: 16px; padding: 16px 20px; align-items: flex-start; max-width: 1200px; width: 100%; margin: 0 auto; }

/* 侧栏：整体固定不随页面滚动；头部固定，仅章节列表内部滚动 */
.side {
  width: 260px; flex-shrink: 0; background: #fff; border: 1px solid var(--color-border);
  border-radius: 10px; display: flex; flex-direction: column; overflow: hidden;
  position: sticky; top: 70px;
  height: calc(100vh - 102px);
}
.side-head {
  flex-shrink: 0; padding: 12px 12px 10px; border-bottom: 1px solid var(--color-border);
}
.side-head h3 { margin: 0; font-size: 14px; }
.sel-dl { display: block; width: 100%; text-align: center; margin-top: 8px; }
.chap-list {
  list-style: none; margin: 0; padding: 6px; flex: 1; min-height: 0;
  overflow-y: auto; display: flex; flex-direction: column; gap: 2px;
}
.chap-item {
  display: flex; gap: 8px; align-items: center; padding: 6px 8px; border-radius: 6px;
  cursor: pointer; font-size: 13px; color: #374151; flex-shrink: 0;
}
.chap-item:hover { background: #f3f4f6; }
.chap-item.active { background: var(--color-primary, #2563eb); color: #fff; }
.chap-no {
  flex-shrink: 0; width: 22px; height: 22px; border-radius: 999px; background: rgba(0,0,0,0.08);
  display: inline-flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 700;
}
.chap-item.active .chap-no { background: rgba(255,255,255,0.25); }
.chap-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chap-check { flex-shrink: 0; width: 14px; height: 14px; cursor: pointer; }

/* 正文：整块固定于视口；标题/翻页固定，仅中间 content-scroll 滚动 */
.content {
  flex: 1; min-width: 0; background: #fff; border: 1px solid var(--color-border); border-radius: 10px;
  display: flex; flex-direction: column; overflow: hidden;
  position: sticky; top: 70px;
  height: calc(100vh - 102px);
}
.chap-head { flex-shrink: 0; border-bottom: 1px dashed var(--color-border); padding: 14px 24px 10px; }
.chap-head h2 { margin: 0 0 4px; font-size: 18px; }
.content-scroll { flex: 1; min-height: 0; overflow-y: auto; padding: 16px 24px; }
.reading-text {
  margin: 0; white-space: pre-wrap; word-break: break-word;
  font-size: 15px; line-height: 1.9; color: #1f2937;
}
.pager {
  flex-shrink: 0; display: flex; justify-content: center; gap: 12px;
  padding: 12px 16px; border-top: 1px dashed var(--color-border);
}
.btn-sm { padding: 4px 12px; font-size: 13px; }
.btn-secondary { background: #6b7280 !important; color: #fff !important; border-color: #6b7280 !important; }

@media (max-width: 860px) {
  .body { flex-direction: column; }
  .side { width: 100%; position: static; height: auto; max-height: 280px; }
  .content { position: static; height: auto; }
  .content-scroll { max-height: 65vh; }
}
</style>
