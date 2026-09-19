<script setup lang="ts">
/**
 * AI 配置管理弹窗：
 *  - 内置默认配置（机器人 .env.prod 同步过来，agnes-3.0-flash）：灰色不可编辑，仅展示；
 *  - 用户可新建/编辑/删除自己的远程（BYOK，走后端代理）或本地（浏览器直连）配置；
 *  - 单选激活；激活后群内机器人也跟着切换（配置前后端互通）。
 */
import { ref, watch } from "vue";
import { aiClient, extractErrMsg } from "@/api/http";
import type { AIConfig, AIConfigKind } from "@/types/api";

const props = defineProps<{ visible: boolean }>();
const emit = defineEmits<{
  (e: "close"): void;
  (e: "changed"): void;
}>();

const configs = ref<AIConfig[]>([]);
const activeId = ref<number>(0);
const loading = ref(false);

async function load() {
  loading.value = true;
  try {
    const data = await aiClient.listConfigs();
    configs.value = data.items;
    activeId.value = data.active_id;
  } catch (e) {
    // 错误在调用方页面也会出现；这里仅保持弹窗可用
  } finally {
    loading.value = false;
  }
}

watch(
  () => props.visible,
  (v) => {
    if (v) {
      load();
      toList();
    }
  },
);

// ---------------- 编辑器 ----------------

const editing = ref(false);
const editingId = ref<number | null>(null);
const form = ref({
  name: "",
  kind: "remote" as AIConfigKind,
  api_base: "",
  api_key: "",
  model: "",
  system_prompt: "",
});
const saving = ref(false);
const testing = ref(false);
const testNote = ref<{ ok: boolean; text: string } | null>(null);

function toList() {
  editing.value = false;
  editingId.value = null;
  testNote.value = null;
}

function startCreate(kind: AIConfigKind = "remote") {
  editing.value = true;
  editingId.value = null;
  form.value = {
    name: "",
    kind,
    api_base: "",
    api_key: "",
    model: "",
    system_prompt: "",
  };
  testNote.value = null;
}

function startEdit(cfg: AIConfig) {
  editing.value = true;
  editingId.value = cfg.id;
  form.value = {
    name: cfg.name,
    kind: cfg.kind,
    api_base: cfg.api_base || "",
    api_key: "", // 列表拿不到真实密钥，留空表示不改
    model: cfg.model || "",
    system_prompt: cfg.system_prompt || "",
  };
  testNote.value = null;
}

async function save() {
  if (!form.value.name.trim()) return;
  saving.value = true;
  try {
    const payload = {
      name: form.value.name.trim(),
      api_base: form.value.api_base.trim() || null,
      model: form.value.model.trim() || null,
      system_prompt: form.value.system_prompt || null,
    };
    if (editingId.value === null) {
      await aiClient.createConfig({
        ...payload,
        kind: form.value.kind,
        // 远程配置需要 key；本地配置 key 仅浏览器使用，不上传
        api_key:
          form.value.kind === "remote"
            ? form.value.api_key.trim() || null
            : null,
      });
    } else {
      await aiClient.updateConfig(editingId.value, {
        ...payload,
        // 仅远程配置允许更新 key
        ...(form.value.kind === "remote" && form.value.api_key.trim()
          ? { api_key: form.value.api_key.trim() }
          : {}),
      });
    }
    await load();
    emit("changed");
    toList();
  } catch (e) {
    testNote.value = { ok: false, text: extractErrMsg(e, "保存失败") };
  } finally {
    saving.value = false;
  }
}

async function remove(cfg: AIConfig) {
  if (!confirm(`确定删除配置「${cfg.name}」？`)) return;
  try {
    await aiClient.deleteConfig(cfg.id);
    await load();
    emit("changed");
  } catch (e) {
    testNote.value = { ok: false, text: extractErrMsg(e, "删除失败") };
  }
}

async function activate(cfg: AIConfig | null) {
  const id = cfg ? cfg.id : 0; // null = 内置默认
  try {
    const data = await aiClient.activateConfig(id);
    configs.value = data.items;
    activeId.value = data.active_id;
    emit("changed");
  } catch (e) {
    testNote.value = { ok: false, text: extractErrMsg(e, "切换失败") };
  }
}

// ---------------- 测试连接 ----------------

async function testRemote() {
  if (!form.value.api_base.trim()) {
    testNote.value = { ok: false, text: "请先填写 API 端点" };
    return;
  }
  testing.value = true;
  testNote.value = null;
  try {
    const out = await aiClient.testConfig({
      api_base: form.value.api_base.trim(),
      api_key: form.value.api_key.trim() || undefined,
    });
    const models = (out.details?.models as string[] | undefined) || [];
    testNote.value = {
      ok: true,
      text: models.length ? `连接成功，可用模型：${models.slice(0, 6).join("、")}` : "连接成功",
    };
  } catch (e) {
    testNote.value = { ok: false, text: extractErrMsg(e, "测试失败") };
  } finally {
    testing.value = false;
  }
}

async function testLocal() {
  if (!form.value.api_base.trim()) {
    testNote.value = { ok: false, text: "请先填写本地端点地址" };
    return;
  }
  testing.value = true;
  testNote.value = null;
  try {
    const base = form.value.api_base.trim().replace(/\/$/, "");
    const url = base.endsWith("/models") ? base : `${base}/models`;
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    const models = (data.data || []).map((m: { id?: string }) => m.id).filter(Boolean);
    testNote.value = {
      ok: true,
      text: models.length ? `连接成功，可用模型：${models.slice(0, 6).join("、")}` : "连接成功",
    };
  } catch (e) {
    testNote.value = {
      ok: false,
      text: `${extractErrMsg(e, "连接失败")}（确认服务已启动，且允许浏览器跨域）`,
    };
  } finally {
    testing.value = false;
  }
}

function builtin(): AIConfig | undefined {
  return configs.value.find((c) => c.is_builtin);
}
function userConfigs(): AIConfig[] {
  return configs.value.filter((c) => !c.is_builtin);
}
</script>

<template>
  <div v-if="visible" class="modal-mask" @click.self="emit('close')">
    <div class="modal">
      <div class="modal-head">
        <h3>🤖 AI 配置</h3>
        <button class="close-x" type="button" @click="emit('close')">×</button>
      </div>

      <!-- ========== 列表模式 ========== -->
      <div v-if="!editing" class="modal-body">
        <div class="hint-banner">
          💡 默认配置使用 <b>agnes-3.0-flash</b>，免费但能力有限，复杂问题表现一般。
          需要更好的体验可点「新建远程配置」，填入你自己的 API Key（OpenAI / DeepSeek /
          Kimi / GLM / 硅基流动 等任意 OpenAI 兼容接口）。
        </div>

        <!-- 内置默认 -->
        <div
          class="cfg-card builtin-card"
          :class="{ active: activeId === 0 }"
        >
          <label class="cfg-main">
            <input
              type="radio"
              name="ai-cfg"
              :checked="activeId === 0"
              @change="activate(null)"
            />
            <div class="cfg-info">
              <div class="cfg-name">
                默认配置
                <span class="tag tag-builtin">内置·免费</span>
                <span class="tag tag-remote">远程</span>
              </div>
              <!-- 灰色只读：跟随机器人 .env.prod -->
              <div class="builtin-fields">
                <input
                  class="grey-input"
                  :value="builtin()?.api_base || '（等待机器人启动同步…）'"
                  disabled
                />
                <input
                  class="grey-input"
                  :value="builtin()?.model || '—'"
                  disabled
                />
              </div>
            </div>
          </label>
        </div>

        <!-- 用户配置 -->
        <div
          v-for="cfg in userConfigs()"
          :key="cfg.id"
          class="cfg-card"
          :class="{ active: activeId === cfg.id }"
        >
          <label class="cfg-main">
            <input
              type="radio"
              name="ai-cfg"
              :checked="activeId === cfg.id"
              @change="activate(cfg)"
            />
            <div class="cfg-info">
              <div class="cfg-name">
                {{ cfg.name }}
                <span class="tag" :class="cfg.kind === 'local' ? 'tag-local' : 'tag-remote'">
                  {{ cfg.kind === "local" ? "本地·浏览器直连" : "远程" }}
                </span>
                <span v-if="activeId === cfg.id" class="tag tag-active">✓ 使用中</span>
              </div>
              <div class="cfg-desc">
                {{ cfg.model || "（未设模型）" }}
                <span v-if="cfg.kind === 'remote'">· {{ cfg.api_base }}</span>
              </div>
            </div>
          </label>
          <div class="cfg-ops">
            <button
              v-if="cfg.kind === 'local'"
              class="btn btn-ghost btn-sm"
              type="button"
              disabled
              title="本地配置仅网页端可用"
            >🔒 仅网页</button>
            <button class="btn btn-ghost btn-sm" type="button" @click="startEdit(cfg)">编辑</button>
            <button class="btn btn-ghost btn-sm btn-danger-text" type="button" @click="remove(cfg)">删除</button>
          </div>
        </div>

        <div class="add-row">
          <button class="btn btn-primary btn-sm" type="button" @click="startCreate('remote')">
            ＋ 新建远程配置
          </button>
          <button class="btn btn-ghost btn-sm" type="button" @click="startCreate('local')">
            ＋ 新建本地配置
          </button>
        </div>
      </div>

      <!-- ========== 编辑模式 ========== -->
      <div v-else class="modal-body">
        <div class="kind-switch">
          <label>
            <input type="radio" value="remote" v-model="form.kind" :disabled="editingId !== null" />
            远程（走服务器代理）
          </label>
          <label>
            <input type="radio" value="local" v-model="form.kind" :disabled="editingId !== null" />
            本地（浏览器直连·走你的本地网络）
          </label>
        </div>

        <div class="field">
          <label>配置名称</label>
          <input v-model="form.name" placeholder="例如：我的 DeepSeek" />
        </div>

        <div class="field">
          <label>API 端点（OpenAI 兼容）</label>
          <input
            v-model="form.api_base"
            :placeholder="form.kind === 'local' ? 'http://localhost:11434/v1' : 'https://api.deepseek.com/v1'"
          />
        </div>

        <div class="field">
          <label>
            API 密钥
            <span v-if="form.kind === 'local'" class="muted">（本地模型通常无需密钥，不会上传）</span>
            <span v-else-if="editingId !== null" class="muted">（留空则不修改）</span>
          </label>
          <input
            v-model="form.api_key"
            type="password"
            autocomplete="off"
            placeholder="sk-..."
          />
        </div>

        <div class="field">
          <label>模型名</label>
          <input
            v-model="form.model"
            :placeholder="form.kind === 'local' ? 'qwen2.5:7b' : 'deepseek-chat'"
          />
        </div>

        <div class="field">
          <label>自定义系统提示词<span class="muted">（可选，留空用默认）</span></label>
          <textarea v-model="form.system_prompt" rows="3" />
        </div>

        <!-- 本地配置说明 -->
        <div v-if="form.kind === 'local'" class="hint-banner local-hint">
          本地配置的请求<b>直接从你的浏览器发出</b>，不经过服务器，请确保本地模型服务已启动：
          <ul>
            <li>Ollama：默认允许 localhost 访问；非默认端口/域名需设置 OLLAMA_ORIGINS</li>
            <li>LM Studio：勾选 Settings → CORS</li>
            <li>llama.cpp server / vLLM：需自行开启跨域允许</li>
          </ul>
          本地配置仅在网页端可用，群内机器人无法访问你本机。
        </div>

        <div v-if="testNote" class="test-note" :class="testNote.ok ? 'ok' : 'fail'">
          {{ testNote.text }}
        </div>

        <div class="edit-foot">
          <button class="btn btn-ghost btn-sm" type="button" @click="toList()">返回</button>
          <div class="spacer" />
          <button
            class="btn btn-ghost btn-sm"
            type="button"
            :disabled="testing"
            @click="form.kind === 'local' ? testLocal() : testRemote()"
          >{{ testing ? "测试中…" : "测试连接" }}</button>
          <button class="btn btn-primary btn-sm" type="button" :disabled="saving" @click="save">
            {{ saving ? "保存中…" : "保存" }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-mask {
  position: fixed;
  inset: 0;
  z-index: 60;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.modal {
  width: 100%;
  max-width: 560px;
  max-height: 86vh;
  display: flex;
  flex-direction: column;
  background: var(--color-surface);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-md);
  overflow: hidden;
}
.modal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px 10px;
}
.modal-head h3 {
  margin: 0;
}
.close-x {
  border: none;
  background: none;
  font-size: 24px;
  line-height: 1;
  color: var(--color-muted);
  cursor: pointer;
}
.modal-body {
  padding: 6px 20px 18px;
  overflow-y: auto;
}

/* 提示横幅 */
.hint-banner {
  background: rgba(236, 72, 153, 0.08);
  border: 1px solid rgba(236, 72, 153, 0.25);
  border-radius: var(--radius-md);
  padding: 10px 12px;
  font-size: 13px;
  line-height: 1.7;
  margin-bottom: 14px;
}
.local-hint ul {
  margin: 6px 0 2px;
  padding-left: 18px;
}
.local-hint li {
  margin: 2px 0;
}

/* 配置卡片 */
.cfg-card {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 10px 12px;
  margin-bottom: 10px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.cfg-card.active {
  border-color: var(--color-primary);
  background: rgba(219, 39, 119, 0.04);
}
.cfg-main {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  flex: 1;
  cursor: pointer;
  margin: 0;
}
.cfg-name {
  font-weight: 600;
  font-size: 14px;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.cfg-desc {
  font-size: 12px;
  color: var(--color-muted);
  margin-top: 3px;
  word-break: break-all;
}
.cfg-ops {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}
.builtin-fields {
  margin-top: 6px;
  display: grid;
  grid-template-columns: 1fr 140px;
  gap: 6px;
}
.grey-input {
  background: #f3eff2 !important;
  color: var(--color-muted) !important;
  cursor: not-allowed;
  font-size: 12px;
}

/* 标签 */
.tag {
  font-size: 11px;
  font-weight: 500;
  padding: 1px 7px;
  border-radius: 999px;
  background: #f1e9ef;
  color: var(--color-muted);
}
.tag-builtin {
  background: rgba(168, 85, 247, 0.12);
  color: #9333ea;
}
.tag-remote {
  background: rgba(219, 39, 119, 0.1);
  color: var(--color-primary);
}
.tag-local {
  background: rgba(22, 163, 74, 0.1);
  color: var(--color-success);
}
.tag-active {
  background: rgba(219, 39, 119, 0.12);
  color: var(--color-primary);
}

.add-row {
  display: flex;
  gap: 8px;
  margin-top: 4px;
}

/* 编辑表单 */
.kind-switch {
  display: flex;
  gap: 18px;
  margin-bottom: 14px;
  font-size: 13px;
}
.kind-switch label {
  display: flex;
  align-items: center;
  gap: 5px;
  cursor: pointer;
  margin: 0;
}
.field {
  margin-bottom: 12px;
}
.field label {
  display: block;
  font-size: 13px;
  font-weight: 500;
  margin-bottom: 5px;
}
.field input,
.field textarea {
  width: 100%;
}
.field textarea {
  resize: vertical;
}
.muted {
  color: var(--color-muted);
  font-weight: 400;
}
.test-note {
  border-radius: var(--radius-sm);
  padding: 8px 10px;
  font-size: 12.5px;
  margin-bottom: 10px;
}
.test-note.ok {
  background: rgba(22, 163, 74, 0.08);
  color: var(--color-success);
}
.test-note.fail {
  background: rgba(220, 38, 38, 0.08);
  color: var(--color-danger);
}
.edit-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
}
.spacer {
  flex: 1;
}
.btn-danger-text {
  color: var(--color-danger) !important;
}
</style>
