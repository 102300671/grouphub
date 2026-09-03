<script setup lang="ts">
/**
 * 文件预览组件：按扩展名/类型分策略渲染，根乱码。
 *
 * 核心策略（对应 Exp 488206 的反面教训）：
 * - 🖼 图片：直接 <img :src="url">（zfile 直链，无需 auth 头）
 * - 🎬 视频 / 🎵 音频：原生 <video> / <audio>，都是二进制流化，浏览器解码，不存在"乱码"
 * - 📄 PDF：<iframe :src="url"> 由浏览器内置 PDF 查看器渲染（二进制，不乱码）
 * - 📝 纯文本族（txt/md/ass/srt/lrc/json/yaml/...）：fetch 拿 Blob → 按 UTF-8 解码 → 贴到 <pre>，根治浏览器当二进制乱码
 * - 📚 EPUB：浏览器不原生支持，提示下载；不给 srcdoc 或 iframe 渲染
 * - 📦 其它（zip/docx/xls…）：提示下载 + 文件名，绝不把二进制直接塞进 <pre>/iframe，避免乱码屏
 *
 * Props.url 是 zfile 直链或同源可直接下载的 URL；公开可访问，不需要 Authorization 头。
 */
import { computed, onBeforeUnmount, ref, watch } from "vue";

const props = defineProps<{
  url: string;
  fileName?: string | null;
  mimeType?: string | null;
  /** 已取好的文本内容（如单文件章节切片）：传入后直接渲染，不再 fetch */
  textContent?: string | null;
  sizeBytes?: number | null;
}>();

const textContent = ref<string>("");
const textLoading = ref(false);
const textError = ref("");
const objUrls = ref<string[]>([]);

function extOf(name?: string | null, mime?: string | null) {
  if (name) {
    const i = name.lastIndexOf(".");
    if (i >= 0) return name.slice(i + 1).toLowerCase();
  }
  const m = (mime || "").split("/")[1]?.toLowerCase() || "";
  return m.replace(/x-/, "");
}

const TEXT_EXTS = new Set([
  "txt", "md", "markdown", "json", "xml", "yaml", "yml", "ini", "conf", "log",
  "srt", "vtt", "ass", "sub", "lrc",
  "html", "htm", "css", "js", "ts", "csv", "rtf",
]);

const ext = computed(() => extOf(props.fileName, props.mimeType));
const mime = computed(() => (props.mimeType || "").toLowerCase());

const kind = computed<
  | "image" | "video" | "audio" | "pdf" | "text" | "epub"
  | "download"
>(() => {
  if (mime.value.startsWith("image/")) return "image";
  if (mime.value.startsWith("video/")) return "video";
  if (mime.value.startsWith("audio/")) return "audio";
  if (mime.value === "application/pdf" || ext.value === "pdf") return "pdf";
  if (["epub", "mobi", "azw3", "kfx", "fb2", "ibooks"].includes(ext.value)) return "epub";
  if (TEXT_EXTS.has(ext.value)) return "text";
  // 扩展名无法识别 → 再用 mime 兜底
  if (mime.value.startsWith("text/")) return "text";
  if (["jpg", "jpeg", "png", "gif", "webp", "bmp", "avif", "tif", "tiff", "heic", "svg"].includes(ext.value)) return "image";
  if (["mp4", "mov", "webm", "mkv", "avi", "flv", "m4v"].includes(ext.value)) return "video";
  if (["mp3", "m4a", "wav", "flac", "ogg", "aac", "opus", "ape"].includes(ext.value)) return "audio";
  return "download";
});

// 文本：fetch → Blob → UTF-8 decode，避免浏览器用错误编码渲染导致乱码
async function loadText() {
  if (kind.value !== "text" || props.textContent != null) return;
  textContent.value = "";
  textError.value = "";
  textLoading.value = true;
  try {
    const res = await fetch(props.url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const buf = await res.arrayBuffer();
    const dec = new TextDecoder("utf-8", { fatal: false });
    textContent.value = dec.decode(new Uint8Array(buf));
  } catch (e: any) {
    textError.value = e?.message || "加载失败";
  } finally {
    textLoading.value = false;
  }
}

watch(() => [props.url, kind.value] as const, () => {
  if (kind.value === "text") void loadText();
}, { immediate: true, flush: "post" });

onBeforeUnmount(() => {
  objUrls.value.forEach((u) => { try { URL.revokeObjectURL(u); } catch {} });
});

function fmtSizeBytes(bytes?: number | null) {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
}
</script>

<template>
  <div class="preview">
    <!-- 已取好的文本内容（章节切片等），直接渲染 -->
    <div v-if="props.textContent != null" class="pv pv-text-wrap">
      <pre class="pv-text">{{ props.textContent }}</pre>
    </div>

    <!-- 图片 -->
    <img v-else-if="kind === 'image'" class="pv pv-image" :src="url" :alt="fileName || ''" loading="lazy" />

    <!-- 视频 -->
    <video v-else-if="kind === 'video'" class="pv pv-video" :src="url" controls preload="metadata" playsinline></video>

    <!-- 音频 -->
    <audio v-else-if="kind === 'audio'" class="pv pv-audio" :src="url" controls preload="metadata"></audio>

    <!-- PDF：浏览器 PDF 查看器，二进制，不乱码 -->
    <iframe v-else-if="kind === 'pdf'" class="pv pv-pdf" :src="url" title="PDF 预览" loading="lazy"></iframe>

    <!-- 文本：手动 UTF-8 decode → <pre>，避免浏览器二进制渲染变乱码 -->
    <div v-else-if="kind === 'text'" class="pv pv-text-wrap">
      <div v-if="textLoading" class="muted text-sm">文本加载中…</div>
      <div v-else-if="textError" class="alert alert-error text-sm">{{ textError }}</div>
      <pre v-else class="pv-text">{{ textContent }}</pre>
    </div>

    <!-- EPUB：浏览器不原生支持，只给下载按钮 -->
    <div v-else-if="kind === 'epub'" class="pv pv-download">
      <div class="badge badge-muted">电子书（.{{ ext }}）：浏览器不支持原生预览</div>
      <a :href="url" :download="fileName || undefined" class="btn btn-primary" target="_blank" rel="noreferrer">⬇ 下载到本地阅读</a>
    </div>

    <!-- 其它类型（zip/docx/ppt/xlx/...）：不预览，避免乱码 -->
    <div v-else class="pv pv-download">
      <div class="badge badge-muted">该类型不提供预览（.{{ ext }}）。</div>
      <a :href="url" :download="fileName || undefined" class="btn btn-primary" target="_blank" rel="noreferrer">⬇ 下载（{{ fmtSizeBytes(props.sizeBytes) }}）</a>
    </div>
  </div>
</template>

<style scoped>
.preview {
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}
.pv { width: 100%; }
.pv-image {
  max-height: 60vh;
  width: auto;
  max-width: 100%;
  border-radius: 10px;
  border: 1px solid var(--color-border);
}
.pv-video {
  max-height: 60vh;
  border-radius: 10px;
}
.pv-audio { max-width: 480px; }
.pv-pdf {
  height: 80vh;
  min-height: 480px;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  background: #fff;
}
.pv-text-wrap {
  width: 100%;
  max-height: 65vh;
  overflow: auto;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  background: #fff;
}
.pv-text {
  margin: 0;
  padding: 16px 18px;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 14px;
  line-height: 1.7;
}
.pv-download {
  padding: 22px;
  border-radius: 10px;
  border: 1px dashed var(--color-border);
  background: #fafafa;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  text-align: center;
}
</style>
