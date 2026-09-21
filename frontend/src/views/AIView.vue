<script setup lang="ts">
/**
 * AI 助手页面（会话层级版）：
 *  - 左侧：大组（QQ 大组 / 前端大组）→ 组 → 会话 树；支持建组/建会话/改名/移动/设默认/取消分组/删除；
 *  - 右侧：对话区 + 输入框；QQ 大组内的会话也允许在前端继续；
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
  AIGroupTree,
  AIMessage,
} from "@/types/api";
import AIConfigModal from "@/components/AIConfigModal.vue";

// ---------------- 数据状态 ----------------

const groups = ref<AIGroupTree[]>([]);
const currentId = ref<number | null>(null);
const bubbles = ref<Array<{ role: string; content: string; pending?: boolean; error?: boolean }>>([]);

const configs = ref<AIConfig[]>([]);
const activeId = ref<number>(0);
const showConfig = ref(false);
const showBuiltinHint = ref(true);

const input = ref("");
const sending = ref(false);
const sidebarOpen = ref(false);

// 树 UI 状态
const collapsedGroups = ref<Set<number>>(new Set());
const collapsedFolders = ref<Set<number>>(new Set());
const menu = ref<{ kind: "group" | "folder" | "conv"; id: number } | null>(null);
const moveTarget = ref<AIConversation | null>(null);
const moveGroupId = ref<number | null>(null);
const moveFolderId = ref<string>("");

function findConv(id: number | null): AIConversation | null {
  if (id === null) return null;
  for (const g of groups.value) {
    for (const c of g.conversations) if (c.id === id) return c;
    for (const f of g.folders) {
      for (const c of f.conversations) if (c.id === id) return c;
    }
  }
  return null;
}

const currentConv = computed(() => findConv(currentId.value));
const isGroupConv = computed(() => currentConv.value?.source === "group");
const activeConfig = computed<AIConfig | null>(
  () => configs.value.find((c) => c.id === activeId.value) || null,
);
const builtinConfig = computed<AIConfig | null>(
  () => configs.value.find((c) => c.is_builtin) || null,
);

// ---------------- 初始化 ----------------

onMounted(async () => {
  await Promise.all([loadGroups(), loadConfigs()]);
});

async function loadGroups() {
  try {
    const data = await aiClient.listGroups();
    groups.value = data.groups;
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

// ---------------- 树：折叠 ----------------

function toggleGroup(g: AIGroupTree) {
  const s = new Set(collapsedGroups.value);
  s.has(g.id) ? s.delete(g.id) : s.add(g.id);
  collapsedGroups.value = s;
}

function isGroupCollapsed(id: number) {
  return collapsedGroups.value.has(id);
}

function toggleFolder(f: { id: number }) {
  const s = new Set(collapsedFolders.value);
  s.has(f.id) ? s.delete(f.id) : s.add(f.id);
  collapsedFolders.value = s;
}

function isFolderCollapsed(id: number) {
  return collapsedFolders.value.has(id);
}

// ---------------- 会话：新建 / 选择 / 删除 ----------------

async function newConversation(groupId?: number, folderId?: number | null) {
  if (sending.value) return;
  const webGroup = groups.value.find((g) => g.kind === "web");
  const gid = groupId ?? webGroup?.id;
  if (!gid) {
    alert("还没有可用的会话空间，请刷新后重试。");
    return;
  }
  try {
    const detail = await aiClient.createConversation({
      ai_group_id: gid,
      folder_id: folderId ?? null,
    });
    await loadGroups();
    selectConversationObject(detail.conversation, detail.messages);
    sidebarOpen.value = false;
  } catch (e) {
    alert(extractErrMsg(e, "创建会话失败"));
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
    await loadGroups();
  } catch (e) {
    alert(extractErrMsg(e, "删除失败"));
  }
}

// ---------------- 会话：改名 / 默认 / 移动 / 取消分组 ----------------

async function renameConv(conv: AIConversation) {
  const name = prompt("新的会话名：", conv.title || "");
  if (name === null) return;
  try {
    await aiClient.patchConversation(conv.id, { title: name.trim() || null });
    await loadGroups();
  } catch (e) {
    alert(extractErrMsg(e, "改名失败"));
  }
}

async function setDefault(conv: AIConversation) {
  try {
    await aiClient.setDefaultConversation(conv.id);
    await loadGroups();
  } catch (e) {
    alert(extractErrMsg(e, "操作失败"));
  }
}

function openMove(conv: AIConversation) {
  moveTarget.value = conv;
  moveGroupId.value = conv.ai_group_id ?? null;
  moveFolderId.value = conv.folder_id ? String(conv.folder_id) : "";
}

async function doMove() {
  const conv = moveTarget.value;
  if (!conv || !moveGroupId.value) return;
  try {
    await aiClient.patchConversation(conv.id, {
      ai_group_id: moveGroupId.value,
      folder_id: moveFolderId.value ? Number(moveFolderId.value) : null,
    });
    moveTarget.value = null;
    await loadGroups();
  } catch (e) {
    alert(extractErrMsg(e, "移动失败"));
  }
}

async function ungroupConv(conv: AIConversation) {
  try {
    await aiClient.patchConversation(conv.id, { folder_id: null });
    await loadGroups();
  } catch (e) {
    alert(extractErrMsg(e, "操作失败"));
  }
}

// ---------------- 组 / 大组 ----------------

async function createFolderIn(group: AIGroupTree) {
  const name = prompt("新分组名称：");
  if (!name || !name.trim()) return;
  try {
    await aiClient.createFolder(group.id, name.trim());
    collapsedGroups.value = new Set(
      [...collapsedGroups.value].filter((id) => id !== group.id),
    );
    await loadGroups();
  } catch (e) {
    alert(extractErrMsg(e, "创建分组失败"));
  }
}

async function renameFolder(folder: { id: number; name: string }) {
  const name = prompt("新的分组名称：", folder.name);
  if (name === null || !name.trim()) return;
  try {
    await aiClient.renameFolder(folder.id, name.trim());
    await loadGroups();
  } catch (e) {
    alert(extractErrMsg(e, "改名失败"));
  }
}

async function deleteFolder(folder: { id: number; name: string }) {
  if (!confirm(`删除分组「${folder.name}」？组内会话将保留并变为未分组。`)) return;
  try {
    await aiClient.deleteFolder(folder.id);
    await loadGroups();
  } catch (e) {
    alert(extractErrMsg(e, "删除失败"));
  }
}

async function renameGroup(group: AIGroupTree) {
  const name = prompt("新的大组名称：", group.name);
  if (name === null || !name.trim()) return;
  try {
    await aiClient.renameGroup(group.id, name.trim());
    await loadGroups();
  } catch (e) {
    alert(extractErrMsg(e, "改名失败"));
  }
}

// ---------------- 发送消息 ----------------

async function send() {
  const content = input.value.trim();
  if (!content || sending.value) return;

  // 没有当前会话 → 在 web 大组新建
  if (!currentId.value) {
    const webGroup = groups.value.find((g) => g.kind === "web");
    if (!webGroup) {
      alert("请先刷新页面创建会话空间。");
      return;
    }
    try {
      const detail = await aiClient.createConversation({ ai_group_id: webGroup.id });
      await loadGroups();
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
    await loadGroups();
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
      <button class="btn btn-primary new-btn" type="button" @click="newConversation()">
        ＋ 新对话
      </button>

      <div class="tree">
        <div v-for="g in groups" :key="g.id" class="tree-group">
          <div class="tree-group-head" @click="toggleGroup(g)">
            <span class="caret">{{ isGroupCollapsed(g.id) ? "▸" : "▾" }}</span>
            <span class="tree-icon">{{ g.kind === "qq" ? "👥" : "💬" }}</span>
            <span class="tree-label">{{ g.name }}</span>
            <span class="tree-actions" @click.stop>
              <button
                class="menu-btn"
                type="button"
                title="操作"
                @click="menu = (menu?.kind === 'group' && menu.id === g.id) ? null : { kind: 'group', id: g.id }"
              >⋯</button>
              <span v-if="menu?.kind === 'group' && menu.id === g.id" class="menu-pop">
                <button type="button" @click="createFolderIn(g); menu = null">新建分组</button>
                <button type="button" @click="newConversation(g.id, null); menu = null">新建会话</button>
                <button type="button" @click="renameGroup(g); menu = null">重命名大组</button>
              </span>
            </span>
          </div>

          <div v-if="!isGroupCollapsed(g.id)" class="tree-body">
            <!-- 组 -->
            <div v-for="f in g.folders" :key="f.id" class="tree-folder">
              <div class="tree-folder-head" @click="toggleFolder(f)">
                <span class="caret">{{ isFolderCollapsed(f.id) ? "▸" : "▾" }}</span>
                <span class="tree-icon">📁</span>
                <span class="tree-label">{{ f.name }}</span>
                <span class="tree-actions" @click.stop>
                  <button
                    class="menu-btn"
                    type="button"
                    title="操作"
                    @click="menu = (menu?.kind === 'folder' && menu.id === f.id) ? null : { kind: 'folder', id: f.id }"
                  >⋯</button>
                  <span v-if="menu?.kind === 'folder' && menu.id === f.id" class="menu-pop">
                    <button type="button" @click="newConversation(g.id, f.id); menu = null">新建会话</button>
                    <button type="button" @click="renameFolder(f); menu = null">重命名分组</button>
                    <button type="button" @click="deleteFolder(f); menu = null">删除分组</button>
                  </span>
                </span>
              </div>
              <div v-if="!isFolderCollapsed(f.id)" class="tree-convs">
                <button
                  v-for="conv in f.conversations"
                  :key="conv.id"
                  class="conv-item"
                  :class="{ active: conv.id === currentId }"
                  type="button"
                  @click="pickConversation(conv)"
                >
                  <span class="conv-icon">{{ conv.is_default ? "⭐" : conv.source === "group" ? "👥" : "💬" }}</span>
                  <span class="conv-text">
                    <span class="conv-title">{{ conv.title || "新对话" }}</span>
                    <span class="conv-time">{{ timeLabel(conv) }}</span>
                  </span>
                  <span class="tree-actions" @click.stop>
                    <button
                      class="menu-btn"
                      type="button"
                      title="操作"
                      @click="menu = (menu?.kind === 'conv' && menu.id === conv.id) ? null : { kind: 'conv', id: conv.id }"
                    >⋯</button>
                    <span v-if="menu?.kind === 'conv' && menu.id === conv.id" class="menu-pop">
                      <button type="button" @click="renameConv(conv); menu = null">重命名</button>
                      <button type="button" @click="setDefault(conv); menu = null">设为默认</button>
                      <button type="button" @click="openMove(conv); menu = null">移动分组/大组</button>
                      <button v-if="conv.folder_id" type="button" @click="ungroupConv(conv); menu = null">取消分组</button>
                      <button type="button" @click="deleteConversation(conv, $event); menu = null">删除</button>
                    </span>
                  </span>
                </button>
              </div>
            </div>

            <!-- 未分组会话 -->
            <button
              v-for="conv in g.conversations"
              :key="conv.id"
              class="conv-item"
              :class="{ active: conv.id === currentId }"
              type="button"
              @click="pickConversation(conv)"
            >
              <span class="conv-icon">{{ conv.is_default ? "⭐" : conv.source === "group" ? "👥" : "💬" }}</span>
              <span class="conv-text">
                <span class="conv-title">{{ conv.title || "新对话" }}</span>
                <span class="conv-time">{{ timeLabel(conv) }}</span>
              </span>
              <span class="tree-actions" @click.stop>
                <button
                  class="menu-btn"
                  type="button"
                  title="操作"
                  @click="menu = (menu?.kind === 'conv' && menu.id === conv.id) ? null : { kind: 'conv', id: conv.id }"
                >⋯</button>
                <span v-if="menu?.kind === 'conv' && menu.id === conv.id" class="menu-pop">
                  <button type="button" @click="renameConv(conv); menu = null">重命名</button>
                  <button type="button" @click="setDefault(conv); menu = null">设为默认</button>
                  <button type="button" @click="openMove(conv); menu = null">移动分组/大组</button>
                  <button v-if="conv.folder_id" type="button" @click="ungroupConv(conv); menu = null">取消分组</button>
                  <button type="button" @click="deleteConversation(conv, $event); menu = null">删除</button>
                </span>
              </span>
            </button>
          </div>
        </div>
      </div>

      <button class="cfg-entry" type="button" @click="showConfig = true">
        ⚙️ AI 配置
        <span class="cfg-entry-sub">
          {{ activeConfig ? activeConfig.name : "默认配置" }}
        </span>
      </button>
    </aside>

    <!-- ============ 移动对话框 ============ -->
    <div v-if="moveTarget" class="modal-mask" @click.self="moveTarget = null">
      <div class="move-dialog">
        <h3>移动会话</h3>
        <p class="move-title">{{ moveTarget.title || "新对话" }}</p>
        <label>目标大组
          <select v-model.number="moveGroupId">
            <option v-for="g in groups" :key="g.id" :value="g.id">{{ g.name }}</option>
          </select>
        </label>
        <label>目标分组
          <select v-model="moveFolderId">
            <option value="">（不分组）</option>
            <option
              v-for="f in (groups.find((g) => g.id === moveGroupId)?.folders || [])"
              :key="f.id"
              :value="String(f.id)"
            >{{ f.name }}</option>
          </select>
        </label>
        <div class="move-actions">
          <button class="btn btn-ghost btn-sm" type="button" @click="moveTarget = null">取消</button>
          <button class="btn btn-primary btn-sm" type="button" @click="doMove">移动</button>
        </div>
      </div>
    </div>

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
          <span v-if="isGroupConv" class="chat-source-tag">👥 QQ 会话</span>
        </div>
        <button class="btn btn-ghost btn-sm" type="button" @click="showConfig = true">
          ⚙️ 配置
        </button>
      </header>

      <!-- 默认配置提示条 -->
      <div
        v-if="showBuiltinHint && activeId === 0"
        class="builtin-hint"
      >
        <span>
          💡 当前使用默认配置 <b>{{ builtinConfig?.model || "默认模型" }}</b>，免费但能力有限；需要更好体验可
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

      <!-- 输入区（QQ 大组会话也可继续） -->
      <footer class="composer">
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
  position: relative;
}
.new-btn {
  width: 100%;
}
.tree {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.tree-group-head,
.tree-folder-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 8px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  user-select: none;
  width: 100%;
  box-sizing: border-box;
}
.tree-group-head {
  font-weight: 600;
}
.tree-group-head:hover,
.tree-folder-head:hover {
  background: rgba(219, 39, 119, 0.06);
}
.caret {
  width: 14px;
  flex-shrink: 0;
  font-size: 12px;
  color: var(--color-muted, #999);
}
.tree-icon {
  flex-shrink: 0;
}
.tree-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14px;
}
.tree-body {
  margin-left: 14px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.tree-folder {
  display: flex;
  flex-direction: column;
}
.tree-convs {
  margin-left: 12px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.tree-actions {
  position: relative;
  display: inline-flex;
  align-items: center;
}
.menu-btn {
  border: none;
  background: none;
  color: var(--color-muted, #999);
  font-size: 16px;
  line-height: 1;
  padding: 2px 6px;
  cursor: pointer;
  border-radius: 6px;
}
.menu-btn:hover {
  background: rgba(0, 0, 0, 0.06);
  color: inherit;
}
.menu-pop {
  position: absolute;
  right: 0;
  top: 22px;
  z-index: 50;
  background: #fff;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
  display: flex;
  flex-direction: column;
  padding: 4px;
  min-width: 120px;
}
.menu-pop button {
  border: none;
  background: none;
  text-align: left;
  padding: 8px 10px;
  font-size: 13px;
  border-radius: 6px;
  cursor: pointer;
  color: var(--color-text);
}
.menu-pop button:hover {
  background: rgba(219, 39, 119, 0.08);
}
.conv-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 8px;
  border: none;
  border-radius: var(--radius-sm);
  background: none;
  cursor: pointer;
  text-align: left;
  width: 100%;
  box-sizing: border-box;
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
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}
.conv-time {
  font-size: 11px;
  color: var(--color-muted, #999);
}
.cfg-entry {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 9px 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: none;
  cursor: pointer;
  font-size: 13px;
}
.cfg-entry-sub {
  font-size: 12px;
  color: var(--color-muted, #999);
}

/* ---------- 移动对话框 ---------- */
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.35);
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: center;
}
.move-dialog {
  background: #fff;
  border-radius: 12px;
  padding: 18px 20px;
  width: 300px;
  max-width: 90vw;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.move-dialog h3 {
  margin: 0;
  font-size: 16px;
}
.move-title {
  margin: 0;
  font-size: 13px;
  color: var(--color-muted, #666);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.move-dialog label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
}
.move-dialog select {
  padding: 7px 8px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  font-size: 14px;
  background: #fff;
}
.move-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

/* ---------- 主区域 ---------- */
.chat-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--color-bg, #fff);
}
.chat-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--color-border);
}
.sidebar-toggle {
  display: none;
  border: none;
  background: none;
  font-size: 20px;
  cursor: pointer;
}
.chat-title {
  flex: 1;
  font-weight: 600;
  font-size: 15px;
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.chat-source-tag {
  font-size: 12px;
  font-weight: 400;
  color: var(--color-muted, #888);
  background: rgba(0, 0, 0, 0.04);
  padding: 2px 8px;
  border-radius: 20px;
  flex-shrink: 0;
}
.builtin-hint {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 16px;
  font-size: 12.5px;
  background: #fff8e6;
  border-bottom: 1px solid #f0e3bd;
  color: #7a6424;
}
.hint-close {
  border: none;
  background: none;
  font-size: 16px;
  cursor: pointer;
  color: inherit;
}
.messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px 16px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.empty-state {
  margin: auto;
  text-align: center;
  color: var(--color-muted, #999);
  padding: 40px 16px;
}
.empty-logo {
  font-size: 44px;
  margin-bottom: 10px;
}
.empty-state h2 {
  font-size: 18px;
  color: var(--color-text);
  margin: 0 0 6px;
}
.empty-tips {
  margin-top: 16px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: center;
}
.empty-tips button {
  border: 1px solid var(--color-border);
  background: #fff;
  border-radius: 20px;
  padding: 6px 14px;
  font-size: 13px;
  cursor: pointer;
}
.empty-tips button:hover {
  border-color: var(--color-primary, #db2777);
  color: var(--color-primary, #db2777);
}
.msg-row {
  display: flex;
  gap: 10px;
  max-width: 86%;
}
.msg-row.user {
  align-self: flex-end;
  flex-direction: row-reverse;
}
.avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: rgba(219, 39, 119, 0.1);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
}
.bubble {
  padding: 10px 14px;
  border-radius: 14px;
  background: #f4f4f5;
  font-size: 14px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
}
.msg-row.user .bubble {
  background: var(--color-primary, #db2777);
  color: #fff;
}
.bubble.error {
  background: #fdecec;
  color: #c0392b;
}
.typing i {
  display: inline-block;
  width: 6px;
  height: 6px;
  margin-right: 3px;
  border-radius: 50%;
  background: #bbb;
  animation: typing 1.2s infinite;
}
.typing i:nth-child(2) {
  animation-delay: 0.2s;
}
.typing i:nth-child(3) {
  animation-delay: 0.4s;
}
@keyframes typing {
  0%, 60%, 100% { opacity: 0.3; transform: translateY(0); }
  30% { opacity: 1; transform: translateY(-3px); }
}
.composer {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  padding: 12px 16px;
  border-top: 1px solid var(--color-border);
}
.composer textarea {
  flex: 1;
  resize: none;
  border: 1px solid var(--color-border);
  border-radius: 12px;
  padding: 10px 14px;
  font-size: 14px;
  font-family: inherit;
  line-height: 1.5;
  max-height: 120px;
  background: #fff;
}
.composer textarea:focus {
  outline: none;
  border-color: var(--color-primary, #db2777);
}
.send-btn {
  width: 40px;
  height: 40px;
  border: none;
  border-radius: 50%;
  background: var(--color-primary, #db2777);
  color: #fff;
  font-size: 16px;
  cursor: pointer;
  flex-shrink: 0;
}
.send-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* ---------- 移动端 ---------- */
@media (max-width: 768px) {
  .ai-page {
    margin: -16px -14px -30px;
    height: calc(100vh - var(--navbar-h) - 50px);
  }
  .sidebar {
    position: fixed;
    left: 0;
    top: var(--navbar-h);
    bottom: 0;
    z-index: 90;
    width: 280px;
    transform: translateX(-100%);
    transition: transform 0.2s ease;
  }
  .sidebar.open {
    transform: translateX(0);
  }
  .sidebar-mask {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.35);
    z-index: 80;
  }
  .sidebar-toggle {
    display: block;
  }
}
</style>
