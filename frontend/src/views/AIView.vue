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
  AgentStepDTO,
} from "@/types/api";
import AIConfigModal from "@/components/AIConfigModal.vue";

// ---------------- 数据状态 ----------------

const groups = ref<AIGroupTree[]>([]);
const currentId = ref<number | null>(null);
/** 一次激活包/工具调用：请求与它的响应成对存在，响应可点开看全文 */
interface AgentCall {
  id: number;
  kind: "activate" | "tool";
  name: string;
  args: Record<string, unknown>;
  raw: string;
  result?: { ok: boolean; summary: string; content: string };
}
/** 一轮模型请求 = 一段思考 + 该轮内的若干调用（思考与工具调用写在一起）。
 *  done：流式期间该轮是否已结束（进入下一轮/整条回复完成）；
 *  userCardOpen/userThinkingOpen：用户手动折叠/展开后以用户意图为准（null=跟随默认）。 */
interface AgentStep {
  reasoning: string;
  calls: AgentCall[];
  done?: boolean;
  userCardOpen?: boolean | null;
  userThinkingOpen?: boolean | null;
}
interface AIBubble {
  role: string;
  content: string;
  pending?: boolean;
  error?: boolean;
  steps?: AgentStep[];
}
const bubbles = ref<Array<AIBubble>>([]);

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
  () =>
    configs.value.find((c) => c.is_builtin && c.name === "默认配置") ||
    configs.value.find((c) => c.is_builtin) ||
    null,
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

/** 历史轨迹 DTO → 运行时步骤（折叠状态走默认：有调用的卡片展开、思考过程折叠） */
function hydrateStep(dto: AgentStepDTO): AgentStep {
  return {
    reasoning: dto.reasoning || "",
    calls: (dto.calls || []).map((c) => ({
      id: c.id,
      kind: c.kind,
      name: c.name,
      args: c.args || {},
      raw: c.raw || "",
      result: c.result,
    })),
  };
}

function selectConversationObject(conv: AIConversation, msgs: AIMessage[]) {
  currentId.value = conv.id;
  bubbles.value = msgs.map((m) => {
    const steps =
      m.role === "assistant"
        ? (m.agent_steps || [])
            .map(hydrateStep)
            .filter((st) => st.reasoning.trim() || st.calls.length)
        : [];
    return {
      role: m.role,
      content: m.content,
      steps: steps.length ? steps : undefined,
    };
  });
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
  bubbles.value.push({
    role: "assistant",
    content: "",
    pending: true,
    steps: [],
  });
  // 必须从响应式数组取回代理对象：持有 push 前的原始引用去改属性，
  // Vue3 不会触发重渲染（表现为回复结束时整段一次性出现，而不是逐字流式）。
  const assistantBubble: AIBubble = bubbles.value[bubbles.value.length - 1];
  sending.value = true;
  scrollToBottom();

  /** 取/建第 index 轮的步骤卡片（思考与该轮调用归在同一卡片） */
  const ensureStep = (index: number): AgentStep => {
    const steps = (assistantBubble.steps = assistantBubble.steps || []);
    while (steps.length <= index) steps.push({ reasoning: "", calls: [] });
    return steps[index];
  };

  const onDelta = (piece: string) => {
    assistantBubble.content += piece;
    scrollToBottom();
  };
  const onReasoning = (piece: string) => {
    const step = ensureStep(
      assistantBubble.steps && assistantBubble.steps.length
        ? assistantBubble.steps.length - 1
        : 0,
    );
    step.reasoning += piece;
    scrollToBottom();
  };
  const onRound = (index: number) => {
    ensureStep(index);
    // 进入新一轮（工具结果回灌后）：之前的轮标记为已结束（思考自动折叠），
    // 上一轮的临时正文丢弃，最终回答会重新生成
    assistantBubble.steps?.forEach((st, i) => {
      if (i < index) st.done = true;
    });
    if (index >= 1) assistantBubble.content = "";
  };
  const onToolCall = (
    id: number,
    kind: "activate" | "tool",
    name: string,
    args: Record<string, unknown>,
    raw: string,
  ) => {
    assistantBubble.content = "";
    const steps = (assistantBubble.steps = assistantBubble.steps || []);
    const step = steps[steps.length - 1] || ensureStep(0);
    step.calls.push({ id, kind, name, args, raw });
    scrollToBottom();
  };
  const onToolResult = (
    id: number,
    name: string,
    ok: boolean,
    summary: string,
    content: string,
  ) => {
    const target = (assistantBubble.steps || [])
      .flatMap((s) => s.calls)
      .find((c) => c.id === id);
    if (target) target.result = { ok, summary, content };
    scrollToBottom();
  };

  try {
    if (isLocal) {
      await sendLocal(
        convId,
        cfg as AIConfig,
        content,
        onDelta,
        onReasoning,
        assistantBubble.steps || [],
      );
    } else {
      await sendRemote(convId, {
        onDelta,
        onReasoning,
        onRound,
        onToolCall,
        onToolResult,
      });
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
async function sendRemote(
  convId: number,
  handlers: {
    onDelta: (t: string) => void;
    onReasoning: (t: string) => void;
    onRound: (index: number) => void;
    onToolCall: (
      id: number,
      kind: "activate" | "tool",
      name: string,
      args: Record<string, unknown>,
      raw: string,
    ) => void;
    onToolResult: (
      id: number,
      name: string,
      ok: boolean,
      summary: string,
      content: string,
    ) => void;
  },
) {
  // 最后一条 user bubble 即本次提问
  const question = bubbles.value[bubbles.value.length - 2].content;
  await streamRemoteChat(convId, question, handlers);
}

/** 本地：浏览器直连，自行持久化 user / assistant 消息 */
async function sendLocal(
  convId: number,
  cfg: AIConfig,
  content: string,
  onDelta: (t: string) => void,
  onReasoning?: (t: string) => void,
  liveSteps: AgentStep[] = [],
) {
  if (!cfg.api_base || !cfg.model) {
    throw new Error("本地配置不完整：请在「AI 配置」中补全端点与模型名。");
  }
  // 先落用户消息
  await aiClient.appendMessage(convId, "user", content);

  // 组装上下文：系统提示词（本配置自定义 > 内置默认）+ 动态当前日期 + 此前全部消息
  const systemPrompt = cfg.system_prompt || builtinConfig.value?.system_prompt;
  const history: Array<{ role: string; content: string }> = [];
  const dateHint = (() => {
    const d = new Date();
    const wd = "日一二三四五六"[d.getDay()];
    return `# 当前时间\n今天是 ${d.getFullYear()} 年 ${d.getMonth() + 1} 月 ${d.getDate()} 日（星期${wd}）。` +
      "涉及「现在、今天、今年、最新、最近、当前」等时效性问题时一律以该日期为准，" +
      "严禁沿用旧年份（例如 2025）；需要实时信息时先联网搜索，关键词带上当前年份。";
  })();
  if (systemPrompt || dateHint) {
    history.push({ role: "system", content: [systemPrompt, dateHint].filter(Boolean).join("\n\n") });
  }
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
    onReasoning,
  );
  const traceSteps = liveSteps
    .filter((st) => st.reasoning.trim() || st.calls.length)
    .map((st) => ({ reasoning: st.reasoning, calls: st.calls }));
  await aiClient.appendMessage(
    convId,
    "assistant",
    answer,
    traceSteps.length ? traceSteps : undefined,
  );
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

// ---------------- 思考 / 激活包 / 工具调用 步骤卡片 ----------------

/** 卡片标题：无任何调用→「思考过程」；有激活包或工具调用→「思考与工具调用」 */
function stepTitle(step: AgentStep): string {
  return step.calls.length ? "思考与工具调用" : "思考过程";
}

/** 步骤卡片展开规则：
 * 流式中的当前轮自动展开；结束/历史消息里，有调用的卡片保持展开（看工具流程），
 * 纯思考卡片自动折叠；用户手动开合后始终以用户意图为准。 */
function isCardOpen(b: AIBubble, step: AgentStep): boolean {
  if (step.userCardOpen != null) return step.userCardOpen;
  const active = !!b.pending && !step.done;
  return active || step.calls.length > 0;
}

/** 思考过程展开规则：仅流式中的当前轮实时展开，结束后自动折叠（用户可再展开） */
function isThinkingOpen(b: AIBubble, step: AgentStep): boolean {
  if (step.userThinkingOpen != null) return step.userThinkingOpen;
  return !!b.pending && !step.done;
}

function onCardToggle(ev: Event, step: AgentStep) {
  step.userCardOpen = (ev.target as HTMLDetailsElement).open;
}
function onThinkingToggle(ev: Event, step: AgentStep) {
  step.userThinkingOpen = (ev.target as HTMLDetailsElement).open;
}

/** 请求行：激活包显示「pkg:activate + 包名」；工具显示「包名:工具名 + 参数」 */
function callRequestText(call: AgentCall): string {
  if (call.kind === "activate") {
    const pkg = String(call.args?.name || "").trim();
    return pkg ? `pkg:activate　name=${pkg}` : "pkg:activate";
  }
  return call.name || "(未知调用)";
}

/** 请求行右侧的参数摘要（键=值，超长截断） */
function callArgsPreview(call: AgentCall): string {
  if (call.kind === "activate") return "";
  const entries = Object.entries(call.args || {});
  if (!entries.length) return "";
  const text = entries
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join("　");
  return text.length > 80 ? text.slice(0, 79) + "…" : text;
}

// ---------------- 请求 / 响应详情弹窗 ----------------

interface DetailModalState {
  title: string;
  call: AgentCall;
  tab: "request" | "response";
}
const detailModal = ref<DetailModalState | null>(null);

function openRequestDetail(call: AgentCall) {
  detailModal.value = {
    title: call.kind === "activate" ? "激活包请求" : "工具调用请求",
    call,
    tab: "request",
  };
}
function openResponseDetail(call: AgentCall) {
  if (!call.result) return;
  detailModal.value = {
    title: call.kind === "activate" ? "激活包响应" : "工具调用响应",
    call,
    tab: "response",
  };
}
function closeDetail() {
  detailModal.value = null;
}

/** 参数按 key=value 列出；响应内容若是 JSON 则美化，其余按原文展示 */
const modalArgs = computed<Array<[string, string]>>(() => {
  const call = detailModal.value?.call;
  if (!call) return [];
  return Object.entries(call.args || {}).map(([k, v]) => [
    k,
    typeof v === "string" ? v : JSON.stringify(v, null, 2),
  ]);
});
const modalResponseText = computed(() => {
  const raw = detailModal.value?.call.result?.content || "";
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
});
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
            <!-- 每轮一张卡片：思考与该轮的激活包/工具调用写在一起 -->
            <details
              v-for="(step, si) in b.steps"
              :key="si"
              class="agent-step"
              :class="{ 'has-calls': step.calls.length }"
              :open="isCardOpen(b, step)"
              @toggle="onCardToggle($event, step)"
            >
              <summary class="agent-step-head">
                <span class="agent-step-icon">{{
                  step.calls.length ? "🧰" : "🧠"
                }}</span>
                <span class="agent-step-title">{{ stepTitle(step) }}</span>
                <span v-if="step.calls.length" class="agent-step-count">
                  {{ step.calls.length }} 次调用
                </span>
              </summary>
              <div class="agent-step-body">
                <!-- 有激活包/工具调用：思考过程折叠为子小节（生成完自动折叠），
                     工具调用流程以目录树平铺 -->
                <template v-if="step.calls.length">
                  <details
                    v-if="step.reasoning"
                    class="thinking-block"
                    :open="isThinkingOpen(b, step)"
                    @toggle="onThinkingToggle($event, step)"
                  >
                    <summary class="thinking-head">
                      <span class="thinking-icon">💭</span>
                      <span>思考过程</span>
                    </summary>
                    <div class="agent-reasoning">{{ step.reasoning }}</div>
                  </details>

                  <!-- 目录树：请求 ├─/└─，其响应为唯一子节点（竖线连接） -->
                  <div class="agent-tree">
                    <template v-for="(call, ci) in step.calls" :key="call.id">
                      <div
                        class="tree-row tree-request"
                        :class="call.kind"
                        title="点击查看完整请求与参数"
                        @click="openRequestDetail(call)"
                      >
                        <span class="tree-mark">{{
                          ci === step.calls.length - 1 ? "└─" : "├─"
                        }}</span>
                        <span class="req-icon">{{
                          call.kind === "activate" ? "📦" : "🔧"
                        }}</span>
                        <span class="req-cmd">{{ callRequestText(call) }}</span>
                        <span v-if="callArgsPreview(call)" class="req-args">
                          {{ callArgsPreview(call) }}
                        </span>
                        <span class="row-hint">查看请求</span>
                      </div>
                      <div class="tree-children">
                        <div v-if="!call.result" class="tree-row tree-pending">
                          <span class="tree-mark">└─</span>
                          <span class="waiting-dots">
                            <i></i><i></i><i></i> 等待响应…
                          </span>
                        </div>
                        <div
                          v-else
                          class="tree-row tree-response"
                          :class="{ error: !call.result.ok }"
                          title="点击查看完整响应"
                          @click="openResponseDetail(call)"
                        >
                          <span class="tree-mark">└─</span>
                          <span class="resp-status">{{
                            call.result.ok ? "✅" : "⚠️"
                          }}</span>
                          <span class="resp-preview">{{ call.result.summary }}</span>
                          <span class="row-hint">完整响应</span>
                        </div>
                      </div>
                    </template>
                  </div>
                </template>
                <!-- 无调用：整张卡片就是「思考过程」，正文在卡片外 -->
                <div v-else-if="step.reasoning" class="agent-reasoning">{{ step.reasoning }}</div>
              </div>
            </details>
            <span
              v-if="
                b.pending &&
                !b.content &&
                (!b.steps ||
                  !b.steps.some((s) => s.reasoning || s.calls.length))
              "
              class="typing"
            >
              <i></i><i></i><i></i>
            </span>
            <template v-if="b.content">{{ b.content }}</template>
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

    <!-- 请求 / 响应详情弹窗 -->
    <div
      v-if="detailModal"
      class="detail-mask"
      @click.self="closeDetail"
    >
      <div class="detail-modal">
        <header class="detail-header">
          <span class="detail-title">{{ detailModal.title }}</span>
          <button type="button" class="detail-close" @click="closeDetail">×</button>
        </header>
        <div class="detail-body">
          <template v-if="detailModal.tab === 'request'">
            <section class="detail-sec">
              <h4><span class="sec-tag">命令</span></h4>
              <pre class="detail-code cmd">{{
                detailModal.call.kind === "activate"
                  ? "pkg:activate"
                  : detailModal.call.name
              }}</pre>
            </section>
            <section v-if="modalArgs.length" class="detail-sec">
              <h4><span class="sec-tag">参数</span></h4>
              <table class="detail-args">
                <tbody>
                  <tr v-for="[k, v] in modalArgs" :key="k">
                    <th>{{ k }}</th>
                    <td><pre>{{ v }}</pre></td>
                  </tr>
                </tbody>
              </table>
            </section>
            <section v-if="detailModal.call.raw" class="detail-sec">
              <h4><span class="sec-tag">原始请求协议</span></h4>
              <pre class="detail-code raw">{{ detailModal.call.raw }}</pre>
            </section>
          </template>
          <template v-else>
            <section class="detail-sec">
              <h4>
                <span class="sec-tag">响应内容</span>
                <span
                  class="resp-badge"
                  :class="detailModal.call.result?.ok ? 'ok' : 'fail'"
                >
                  {{ detailModal.call.result?.ok ? "成功" : "失败" }}
                </span>
              </h4>
              <pre class="detail-code raw response">{{ modalResponseText }}</pre>
            </section>
          </template>
        </div>
      </div>
    </div>
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
  overflow-x: hidden;
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
  min-width: 0;
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
  min-width: 0;
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

/* ---------- 思考 / 工具步骤卡片（思考与调用写在一起） ---------- */
.agent-step {
  margin: 2px 0 8px;
  border: 1px solid rgba(127, 140, 255, 0.22);
  background: linear-gradient(180deg, rgba(127, 140, 255, 0.07), rgba(127, 140, 255, 0.03));
  border-radius: 10px;
  overflow: hidden;
  font-size: 12.5px;
}
.agent-step.has-calls {
  border-color: rgba(219, 39, 119, 0.22);
  background: linear-gradient(180deg, rgba(219, 39, 119, 0.05), rgba(127, 140, 255, 0.03));
}
.agent-step-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  cursor: pointer;
  user-select: none;
  list-style: none;
  color: #5b6075;
}
.agent-step-head::-webkit-details-marker {
  display: none;
}
.agent-step-icon {
  font-size: 13px;
}
.agent-step-title {
  font-weight: 600;
  color: #4a4f63;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.agent-step-count {
  margin-left: auto;
  font-size: 11px;
  color: #9aa0b5;
  background: rgba(127, 140, 255, 0.1);
  border-radius: 999px;
  padding: 1px 8px;
  flex-shrink: 0;
}
.agent-step-body {
  padding: 2px 10px 8px;
  min-width: 0;
}
.agent-reasoning {
  white-space: pre-wrap;
  word-break: break-word;
  color: #8a8fa6;
  line-height: 1.6;
  padding: 2px 0 6px;
}
/* 有工具调用的卡片内：思考过程折叠为子小节（生成完自动折叠，可手动展开） */
.thinking-block {
  margin: 0 0 6px;
  border: 1px solid rgba(127, 140, 255, 0.18);
  border-radius: 8px;
  background: rgba(127, 140, 255, 0.04);
  overflow: hidden;
}
.thinking-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  font-size: 12px;
  color: #8a8fa6;
  cursor: pointer;
  user-select: none;
  list-style: none;
}
.thinking-head::-webkit-details-marker {
  display: none;
}
.thinking-head::before {
  content: "▸";
  font-size: 10px;
  color: #b3b9d4;
  transition: transform 0.15s ease;
}
.thinking-block[open] .thinking-head::before {
  transform: rotate(90deg);
}
.thinking-block .agent-reasoning {
  padding: 2px 12px 8px;
}

/* ---------- 请求/响应目录树 ---------- */
.agent-tree {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.tree-row {
  display: flex;
  align-items: center;
  gap: 6px;
  border-radius: 7px;
  padding: 4px 8px;
  line-height: 1.45;
  cursor: default;
  min-width: 0;
}
.tree-mark {
  color: #b3b9d4;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  flex-shrink: 0;
}
.tree-request {
  cursor: pointer;
  border: 1px solid transparent;
  transition: background 0.15s, border-color 0.15s;
}
.tree-request.activate {
  background: rgba(124, 58, 237, 0.08);
  color: #6d28d9;
}
.tree-request.tool {
  background: rgba(37, 99, 235, 0.07);
  color: #1d4ed8;
}
.tree-request:hover {
  border-color: rgba(100, 110, 200, 0.35);
}
.req-cmd {
  font-weight: 600;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  flex-shrink: 0;
}
.req-args {
  color: #555b70;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}
.row-hint {
  margin-left: auto;
  font-size: 10.5px;
  color: #aab0c8;
  text-decoration: underline dotted;
  flex-shrink: 0;
}
/* 响应作为请求的子节点：竖线 + 缩进 */
.tree-children {
  margin-left: 20px;
  padding-left: 12px;
  border-left: 1.5px dashed rgba(130, 140, 200, 0.35);
  min-width: 0;
}
.tree-response {
  cursor: pointer;
  color: #5f6478;
  transition: background 0.15s;
}
.tree-response:hover {
  background: rgba(127, 140, 255, 0.08);
}
.tree-response.error {
  color: #c2410c;
  background: rgba(249, 115, 22, 0.07);
}
.resp-status {
  flex-shrink: 0;
}
.resp-preview {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}
.tree-pending {
  color: #9aa0b5;
}
.waiting-dots i,
.typing i {
  display: inline-block;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: currentColor;
  margin: 0 1px;
  animation: blink 1.2s infinite both;
}
.waiting-dots i:nth-child(2),
.typing i:nth-child(2) {
  animation-delay: 0.2s;
}
.waiting-dots i:nth-child(3),
.typing i:nth-child(3) {
  animation-delay: 0.4s;
}
@keyframes blink {
  0%, 80%, 100% { opacity: 0.2; }
  40% { opacity: 1; }
}

/* ---------- 请求 / 响应详情弹窗 ---------- */
.detail-mask {
  position: fixed;
  inset: 0;
  z-index: 200;
  background: rgba(30, 32, 48, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  backdrop-filter: blur(2px);
}
.detail-modal {
  width: min(720px, 100%);
  max-height: min(80vh, 720px);
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 14px;
  box-shadow: 0 18px 60px rgba(40, 42, 70, 0.28);
  overflow: hidden;
}
.detail-header {
  display: flex;
  align-items: center;
  padding: 14px 18px;
  border-bottom: 1px solid #eef0f6;
}
.detail-title {
  font-weight: 700;
  font-size: 15px;
  color: #33384f;
}
.detail-close {
  margin-left: auto;
  border: none;
  background: #f2f3f8;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  font-size: 17px;
  line-height: 1;
  color: #7a8099;
  cursor: pointer;
}
.detail-close:hover {
  background: #e6e8f2;
}
.detail-body {
  padding: 14px 18px 18px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.detail-sec h4 {
  margin: 0 0 6px;
  font-size: 12.5px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.sec-tag {
  background: rgba(127, 140, 255, 0.12);
  color: #5560c8;
  border-radius: 6px;
  padding: 2px 8px;
  font-weight: 600;
}
.detail-code {
  margin: 0;
  background: #1e2030;
  color: #d7dcf0;
  border-radius: 9px;
  padding: 10px 12px;
  font-size: 12px;
  line-height: 1.6;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  white-space: pre-wrap;
  word-break: break-word;
}
.detail-code.cmd {
  color: #9effc0;
}
.detail-code.response {
  max-height: 46vh;
  overflow-y: auto;
}
.detail-args {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}
.detail-args th {
  width: 110px;
  vertical-align: top;
  text-align: left;
  color: #7a8099;
  font-weight: 600;
  padding: 6px 10px 6px 0;
}
.detail-args td {
  padding: 4px 0;
}
.detail-args td pre {
  margin: 0;
  background: #f5f6fb;
  border: 1px solid #eaecf4;
  border-radius: 8px;
  padding: 7px 10px;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  color: #3c4157;
}
.resp-badge {
  border-radius: 999px;
  padding: 1px 9px;
  font-size: 11px;
  font-weight: 600;
}
.resp-badge.ok {
  background: rgba(34, 197, 94, 0.13);
  color: #15803d;
}
.resp-badge.fail {
  background: rgba(249, 115, 22, 0.15);
  color: #c2410c;
}
</style>