<script setup lang="ts">
/**
 * AI 助手页面（类 ChatGPT 布局）：
 *  - 左侧：会话列表（网页会话 + 群内同步会话，各自独立）+ 新建 + 配置入口；
 *  - 右侧：对话区 + 输入框；
 *  - 远程配置走后端 SSE 流式；本地配置浏览器直连用户本地模型。
 */
import { computed, nextTick, onMounted, ref } from "vue";
import {
  aiClient,
  extractErrMsg,
  streamLocalChat,
  streamRemoteChat,
} from "@/api/http";
import type {
  AIConfig,
  AIConversation,
  AIMessage,
} from "@/types/api";
import AIConfigModal from "@/components/AIConfigModal.vue";

// ---------------- 数据状态 ----------------

const conversations = ref<AIConversation[]>([]);
const currentId = ref<number | null>(null);
const bubbles = ref<Array<{ role: string; content: string; pending?: boolean; error?: boolean }>>([]);

const configs = ref<AIConfig[]>([]);
const activeId = ref<number>(0);
const showConfig = ref(false);
const showBuiltinHint = ref(true);

const input = ref("");
const sending = ref(false);
const sidebarOpen = ref(false);

const currentConv = computed(
  () => conversations.value.find((c) => c.id === currentId.value) || null,
);
const isGroupConv = computed(() => currentConv.value?.source === "group");
const activeConfig = computed<AIConfig | null>(
  () => configs.value.find((c) => c.id === activeId.value) || null,
);
const builtinConfig = computed<AIConfig | null>(
  () => configs.value.find((c) => c.is_builtin) || null,
);

// ---------------- 初始化 ----------------

onMounted(async () => {
  await Promise.all([loadConversations(), loadConfigs()]);
});

async function loadConversations() {
  try {
    const data = await aiClient.listConversations();
    conversations.value = data.items;
  } catch (e) {
    // 401 已由拦截器处理
  }
}

async function loadConfigs() {
  try {
    const data = await aiClient.listConfigs();
    configs.value = data.items;
    activeId.value = data.active_id;
  } catch (e) {
    // 同上
  }
}

function onConfigChanged() {
  loadConfigs();
}

// ---------------- 会话切换/新建 ----------------

async function newConversation() {
  if (sending.value) return;
  try {
    const detail = await aiClient.createConversation();
    conversations.value.unshift(detail.conversation);
    selectConversationObject(detail.conversation, detail.messages);
    sidebarOpen.value = false;
  } catch (e) {
    // 错误提示在配置/网络问题时通过其他方式呈现
  }
}

async function pickConversation(conv: AIConversation) {
  if (sending.value || conv.id === currentId.value) {
    sidebarOpen.value = false;
    return;
  }
  try {
    const detail = await aiClient.getConversation(conv.id);
    selectConversationObject(detail.conversation, detail.messages);
  } catch (e) {
    bubbles.value = [
      { role: "assistant", content: extractErrMsg(e, "加载失败"), error: true },
    ];
  }
  sidebarOpen.value = false;
}

function selectConversationObject(conv: AIConversation, msgs: AIMessage[]) {
  currentId.value = conv.id;
  bubbles.value = msgs.map((m) => ({ role: m.role, content: m.content }));
  scrollToBottom();
}

async function deleteConversation(conv: AIConversation, event: Event) {
  event.stopPropagation();
  if (!confirm("确定删除这个会话？")) return;
  try {
    await aiClient.deleteConversation(conv.id);
    if (conv.id === currentId.value) {
      currentId.value = null;
      bubbles.value = [];
    }
    await loadConversations();
  } catch (e) {
    alert(extractErrMsg(e, "删除失败"));
  }
}

// ---------------- 发送消息 ----------------

async function send() {
  const content = input.value.trim();
  if (!content || sending.value) return;
  if (isGroupConv.value) return;

  // 必须先有一个网页会话
  if (!currentId.value) {
    try {
      const detail = await aiClient.createConversation();
      conversations.value.unshift(detail.conversation);
      selectConversationObject(detail.conversation, detail.messages);
    } catch (e) {
      alert(extractErrMsg(e, "创建会话失败"));
      return;
    }
  }

  const convId = currentId.value as number;
  const cfg = activeConfig.value;
  const isLocal = cfg?.kind === "local";

  input.value = "";
  bubbles.value.push({ role: "user", content });
  const assistantBubble: {
    role: string;
    content: string;
    pending: boolean;
    error?: boolean;
  } = {
    role: "assistant",
    content: "",
    pending: true,
  };
  bubbles.value.push(assistantBubble);
  sending.value = true;
  scrollToBottom();

  const onDelta = (piece: string) => {
    assistantBubble.content += piece;
    scrollToBottom();
  };

  try {
    if (isLocal) {
      await sendLocal(convId, cfg as AIConfig, content, onDelta);
    } else {
      await sendRemote(convId, onDelta);
    }
    assistantBubble.pending = false;
  } catch (e) {
    assistantBubble.pending = false;
    assistantBubble.content =
      assistantBubble.content || extractErrMsg(e, "出错了");
    assistantBubble.error = true;
  } finally {
    sending.value = false;
    await loadConversations();
    scrollToBottom();
  }
}

/** 远程：后端代理，服务端负责持久化两条消息 */
async function sendRemote(convId: number, onDelta: (t: string) => void) {
  // 最后一条 user bubble 即本次提问
  const question = bubbles.value[bubbles.value.length - 2].content;
  await streamRemoteChat(convId, question, onDelta);
}

/** 本地：浏览器直连，自行持久化 user / assistant 消息 */
async function sendLocal(
  convId: number,
  cfg: AIConfig,
  content: string,
  onDelta: (t: string) => void,
) {
  if (!cfg.api_base || !cfg.model) {
    throw new Error("本地配置不完整：请在「AI 配置」中补全端点与模型名。");
  }
  // 先落用户消息
  await aiClient.appendMessage(convId, "user", content);

  // 组装上下文：系统提示词（本配置自定义 > 内置默认）+ 此前全部消息
  const systemPrompt = cfg.system_prompt || builtinConfig.value?.system_prompt;
  const history: Array<{ role: string; content: string }> = [];
  if (systemPrompt) history.push({ role: "system", content: systemPrompt });
  // 排除刚 push 的 user 和 pending assistant（最后两个）
  bubbles.value.slice(0, -2).forEach((b) => {
    if (!b.error) history.push({ role: b.role, content: b.content });
  });
  history.push({ role: "user", content });

  let answer = "";
  await streamLocalChat(
    { api_base: cfg.api_base, model: cfg.model },
    history,
    (piece) => {
      answer += piece;
      onDelta(piece);
    },
  );
  await aiClient.appendMessage(convId, "assistant", answer);
}

// ---------------- 输入框 ----------------

function onKeydown(e: KeyboardEvent) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
}

// ---------------- 滚动 ----------------

const scrollEl = ref<HTMLElement | null>(null);
async function scrollToBottom() {
  await nextTick();
  const el = scrollEl.value;
  if (el) el.scrollTop = el.scrollHeight;
}

// ---------------- 时间显示 ----------------

function timeLabel(conv: AIConversation): string {
  const d = new Date(conv.updated_at);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  return sameDay
    ? d.toTimeString().slice(0, 5)
    : `${d.getMonth() + 1}/${d.getDate()}`;
}
</script>

<template>
  <div class="ai-page">
    <!-- 移动端遮罩 -->
    <div
      v-if="sidebarOpen"
      class="sidebar-mask"
      @click="sidebarOpen = false"
    />

    <!-- ============ 侧栏 ============ -->
    <aside class="sidebar" :class="{ open: sidebarOpen }">
      <button class="btn btn-primary new-btn" type="button" @click="newConversation">
        ＋ 新对话
      </button>

      <div class="conv-list">
        <button
          v-for="conv in conversations"
          :key="conv.id"
          class="conv-item"
          :class="{ active: conv.id === currentId }"
          type="button"
          @click="pickConversation(conv)"
        >
          <span class="conv-icon">{{ conv.source === "group" ? "👥" : "💬" }}</span>
          <span class="conv-text">
            <span class="conv-title">{{ conv.title || "新对话" }}</span>
            <span class="conv-time">{{ timeLabel(conv) }}</span>
          </span>
          <span
            v-if="conv.source === 'web'"
            class="conv-del"
            title="删除"
            @click="deleteConversation(conv, $event)"
          >×</span>
        </button>
      </div>

      <button class="cfg-entry" type="button" @click="showConfig = true">
        ⚙️ AI 配置
        <span class="cfg-entry-sub">
          {{ activeConfig ? activeConfig.name : "默认配置" }}
        </span>
      </button>
    </aside>

    <!-- ============ 主区域 ============ -->
    <section class="chat-main">
      <header class="chat-header">
        <button
          class="sidebar-toggle"
          type="button"
          @click="sidebarOpen = true"
        >☰</button>
        <div class="chat-title">
          {{ currentConv?.title || "AI 助手" }}
          <span v-if="isGroupConv" class="chat-source-tag">👥 群聊同步会话</span>
        </div>
        <button class="btn btn-ghost btn-sm" type="button" @click="showConfig = true">
          ⚙️ 配置
        </button>
      </header>

      <!-- 默认配置提示条 -->
      <div
        v-if="showBuiltinHint && activeId === 0 && !isGroupConv"
        class="builtin-hint"
      >
        <span>
          💡 当前使用默认配置 <b>agnes-3.0-flash</b>，免费但能力有限；需要更好体验可
          <a href="#" @click.prevent="showConfig = true">新建配置</a> 用你自己的 API。
        </span>
        <button type="button" class="hint-close" @click="showBuiltinHint = false">×</button>
      </div>

      <div ref="scrollEl" class="messages">
        <!-- 空状态 -->
        <div v-if="!bubbles.length" class="empty-state">
          <div class="empty-logo">👭</div>
          <h2>群资源站 AI 助手</h2>
          <p>有什么想聊的、想问的，直接在下方输入。</p>
          <div class="empty-tips">
            <button type="button" @click="input = '推荐几部好看的百合作品'">推荐百合作品</button>
            <button type="button" @click="input = '泰拉瑞亚新手开荒流程'">泰拉瑞亚开荒</button>
            <button type="button" @click="input = '帮我写一段春日樱花的文字'">写段文字</button>
          </div>
        </div>

        <!-- 消息气泡 -->
        <div
          v-for="(b, i) in bubbles"
          :key="i"
          class="msg-row"
          :class="b.role"
        >
          <div class="avatar">
            {{ b.role === "user" ? "🧑" : "🤖" }}
          </div>
          <div class="bubble" :class="{ error: b.error }">
            <span v-if="b.pending && !b.content" class="typing">
              <i></i><i></i><i></i>
            </span>
            <template v-else>{{ b.content }}</template>
          </div>
        </div>
      </div>

      <!-- 群会话：不允许网页端继续 -->
      <footer v-if="isGroupConv" class="composer disabled-composer">
        <span>👥 这是群内对话同步的会话，请在群里 @机器人 继续对话；群内可用 /ai 重置 开新会话。</span>
      </footer>

      <!-- 输入区 -->
      <footer v-else class="composer">
        <textarea
          v-model="input"
          rows="1"
          placeholder="输入消息，Enter 发送，Shift+Enter 换行"
          :disabled="sending"
          @keydown="onKeydown"
        />
        <button
          class="send-btn"
          type="button"
          :disabled="!input.trim() || sending"
          @click="send"
        >➤</button>
      </footer>
    </section>

    <!-- 配置弹窗 -->
    <AIConfigModal
      :visible="showConfig"
      @close="showConfig = false"
      @changed="onConfigChanged"
    />
  </div>
</template>

<style scoped>
.ai-page {
  /* 突破 MainLayout 的 container 限制，占满可用高度 */
  display: flex;
  gap: 0;
  height: calc(100vh - var(--navbar-h) - 70px);
  min-height: 480px;
  margin: -24px -20px -40px;
}

/* ---------- 侧栏 ---------- */
.sidebar {
  width: 264px;
  flex-shrink: 0;
  border-right: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  padding: 14px 12px;
  gap: 12px;
  background: var(--color-surface);
}
.new-btn {
  width: 100%;
}
.conv-list {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.conv-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 9px;
  border: none;
  border-radius: var(--radius-sm);
  background: none;
  cursor: pointer;
  text-align: left;
  width: 100%;
}
.conv-item:hover {
  background: rgba(219, 39, 119, 0.06);
}
.conv-item.active {
  background: rgba(219, 39, 119, 0.1);
}
.conv-icon {
  flex-shrink: 0;
}
.conv-text {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.conv-title {
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.conv-time {
  font-size: 11px;
  color: var(--color-muted);
}
.conv-del {
  color: var(--color-muted);
  font-size: 15px;
  opacity: 0;
  padding: 0 4px;
}
.conv-item:hover .conv-del {
  opacity: 1;
}
.conv-del:hover {
  color: var(--color-danger);
}
.cfg-entry {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: none;
  padding: 9px 12px;
  font-size: 13px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.cfg-entry:hover {
  border-color: var(--color-primary);
}
.cfg-entry-sub {
  color: var(--color-muted);
  font-size: 12px;
  max-width: 130px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ---------- 主区域 ---------- */
.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: var(--color-bg);
}
.chat-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 18px;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-surface);
}
.sidebar-toggle {
  display: none;
  border: none;
  background: none;
  font-size: 18px;
  cursor: pointer;
}
.chat-title {
  flex: 1;
  font-weight: 600;
  font-size: 15px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.chat-source-tag {
  font-size: 11px;
  font-weight: 500;
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(22, 163, 74, 0.1);
  color: var(--color-success);
}

/* 内置默认提示条 */
.builtin-hint {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 18px;
  font-size: 12.5px;
  background: rgba(236, 72, 153, 0.07);
  border-bottom: 1px solid rgba(236, 72, 153, 0.18);
}
.builtin-hint a {
  color: var(--color-primary);
}
.hint-close {
  border: none;
  background: none;
  color: var(--color-muted);
  font-size: 16px;
  cursor: pointer;
}

/* ---------- 消息区 ---------- */
.messages {
  flex: 1;
  overflow-y: auto;
  padding: 22px 0;
}
.msg-row {
  display: flex;
  gap: 12px;
  max-width: 760px;
  margin: 0 auto;
  padding: 8px 20px;
}
.avatar {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 17px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
}
.msg-row.assistant .avatar {
  background: var(--accent-gradient);
  border: none;
}
.bubble {
  padding: 9px 14px;
  border-radius: 12px;
  font-size: 14.5px;
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
}
.msg-row.user .bubble {
  background: rgba(219, 39, 119, 0.08);
}
.msg-row.assistant .bubble {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
}
.bubble.error {
  color: var(--color-danger);
  border-color: rgba(220, 38, 38, 0.35);
}

/* 打字动画 */
.typing {
  display: inline-flex;
  gap: 4px;
  padding: 4px 0;
}
.typing i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-muted);
  animation: blink 1.2s infinite ease-in-out;
}
.typing i:nth-child(2) { animation-delay: 0.2s; }
.typing i:nth-child(3) { animation-delay: 0.4s; }
@keyframes blink {
  0%, 80%, 100% { opacity: 0.25; }
  40% { opacity: 1; }
}

/* 空状态 */
.empty-state {
  text-align: center;
  padding: 60px 20px;
}
.empty-logo {
  font-size: 44px;
}
.empty-state h2 {
  margin: 12px 0 6px;
}
.empty-state p {
  color: var(--color-muted);
  font-size: 14px;
}
.empty-tips {
  margin-top: 22px;
  display: flex;
  gap: 10px;
  justify-content: center;
  flex-wrap: wrap;
}
.empty-tips button {
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  border-radius: 999px;
  padding: 7px 16px;
  font-size: 13px;
  cursor: pointer;
}
.empty-tips button:hover {
  border-color: var(--color-primary);
  color: var(--color-primary);
}

/* ---------- 输入区 ---------- */
.composer {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  padding: 12px 20px 18px;
  max-width: 760px;
  margin: 0 auto;
  width: 100%;
}
.composer textarea {
  flex: 1;
  resize: none;
  max-height: 180px;
  padding: 11px 14px;
  border-radius: var(--radius-md);
  font-family: inherit;
  font-size: 14px;
  line-height: 1.6;
}
.composer textarea:disabled {
  background: var(--color-bg);
}
.send-btn {
  width: 42px;
  height: 42px;
  border-radius: var(--radius-md);
  border: none;
  background: var(--accent-gradient);
  color: #fff;
  font-size: 16px;
  cursor: pointer;
  flex-shrink: 0;
}
.send-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.disabled-composer {
  justify-content: center;
  padding: 14px 20px 20px;
  color: var(--color-muted);
  font-size: 13px;
  text-align: center;
}

.sidebar-mask {
  display: none;
}

/* ---------- 移动端 ---------- */
@media (max-width: 760px) {
  .ai-page {
    height: calc(100vh - 54px - 90px);
    margin: -16px -20px -72px;
  }
  .sidebar-toggle {
    display: block;
  }
  .sidebar {
    position: fixed;
    top: 54px;
    left: 0;
    bottom: 0;
    z-index: 70;
    width: 270px;
    transform: translateX(-100%);
    transition: transform 0.2s ease;
  }
  .sidebar.open {
    transform: translateX(0);
  }
  .sidebar-mask {
    display: block;
    position: fixed;
    inset: 54px 0 0;
    z-index: 65;
    background: rgba(0, 0, 0, 0.35);
  }
}
</style>
