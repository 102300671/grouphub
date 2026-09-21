<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { useUserStore } from "@/stores/user";
import { authClient, extractErrMsg } from "@/api/http";
import ChangePasswordModal from "@/components/ChangePasswordModal.vue";
import type { OpenidBindingItem, Work } from "@/types/api";

const user = useUserStore();
const tip = ref("");
const loading = ref(false);
const errorMsg = ref("");

// 修改密码
const changePwdVisible = ref(false);

// 用户参与的作品数据
const uploadedWorks = ref<Work[]>([]);
const supportedWorks = ref<Work[]>([]);
const recommendedWorks = ref<Work[]>([]);

// openid 绑定管理
const bindings = ref<OpenidBindingItem[]>([]);
const bindLoading = ref(false);
const bindModalVisible = ref(false);
const bindCode = ref("");
const bindExpires = ref(10);
const bindErrorMsg = ref("");
let bindPollTimer: ReturnType<typeof setInterval> | null = null;
/** 打开弹窗时的绑定快照（id:updated_at），用于检测绑定是否发生变化 */
let bindSnapshotAtStart = "";

async function loadBindings() {
  bindLoading.value = true;
  try {
    const res = await user.listBindings();
    bindings.value = res.items;
  } catch {
    // 忽略
  } finally {
    bindLoading.value = false;
  }
}

async function startBind() {
  bindErrorMsg.value = "";
  try {
    const out = await user.fetchBindCode();
    bindCode.value = out.code || "";
    bindExpires.value = out.expires_in_minutes || 10;
    bindSnapshotAtStart = bindingsSnapshot();
    bindModalVisible.value = true;
    startBindPoll();
  } catch (e) {
    bindErrorMsg.value = extractErrMsg(e, "获取验证码失败");
  }
}

function bindingsSnapshot(): string {
  return bindings.value.map((b) => `${b.id}:${b.updated_at}`).join("|");
}

function startBindPoll() {
  stopBindPoll();
  bindPollTimer = setInterval(async () => {
    try {
      await loadBindings();
      // 快照变化 = 新增了绑定或重新绑定了已有 openid（updated_at 变化）→ 关闭弹窗
      if (bindingsSnapshot() !== bindSnapshotAtStart) {
        stopBindPoll();
        bindModalVisible.value = false;
      }
    } catch {
      // 轮询失败忽略
    }
  }, 3000);
}

function stopBindPoll() {
  if (bindPollTimer) {
    clearInterval(bindPollTimer);
    bindPollTimer = null;
  }
}

function dismissBindModal() {
  stopBindPoll();
  bindModalVisible.value = false;
}

async function deleteBindingById(id: number) {
  if (!confirm("确定要解绑这个 openid 吗？解绑后该身份将无法使用群内机器人命令。")) return;
  try {
    await user.deleteBinding(id);
    await loadBindings();
  } catch (e) {
    bindErrorMsg.value = extractErrMsg(e, "解绑失败");
  }
}

/** 点击用户名切换显示个人 openid（群聊=member_openid，私聊=user_openid） */
const showUserOpenidId = ref<number | null>(null);
function toggleUserOpenid(b: OpenidBindingItem) {
  showUserOpenidId.value = showUserOpenidId.value === b.id ? null : b.id;
}
/** 个人身份默认展示站点用户名（注册时手动填的群内名称/QQ 用户名） */
function personLabel(b: OpenidBindingItem): string {
  return b.display_name || user.current?.nickname || `QQ ${user.current?.qq ?? ""}`;
}

/** 点击群名 badge 切换显示 group_openid（官方适配器拿不到群号，只有 group_openid） */
const showGroupOpenidId = ref<number | null>(null);
function toggleGroupOpenid(b: OpenidBindingItem) {
  if (b.openid_type !== "group" || !b.group_openid) return;
  showGroupOpenidId.value = showGroupOpenidId.value === b.id ? null : b.id;
}
function groupLabel(b: OpenidBindingItem): string {
  if (showGroupOpenidId.value === b.id) return b.group_openid || "";
  return b.group_name || (b.group_id ? `群 ${b.group_id}` : "群聊");
}

// 修改显示名称（站点昵称）
const nameModalVisible = ref(false);
const nameInput = ref("");
const nameSaving = ref(false);
const nameErrorMsg = ref("");

function openNameModal() {
  nameInput.value = user.current?.nickname || "";
  nameErrorMsg.value = "";
  nameModalVisible.value = true;
}

async function saveDisplayName() {
  const nickname = nameInput.value.trim();
  if (!nickname) {
    nameErrorMsg.value = "显示名称不能为空";
    return;
  }
  nameSaving.value = true;
  nameErrorMsg.value = "";
  try {
    await user.updateNickname(nickname);
    nameModalVisible.value = false;
    // 绑定列表的 display_name 来自站点昵称，改名后刷新
    await loadBindings();
  } catch (e) {
    nameErrorMsg.value = extractErrMsg(e, "修改失败");
  } finally {
    nameSaving.value = false;
  }
}

function copyBindCode() {
  navigator.clipboard?.writeText(bindCode.value).catch(() => {});
}

async function loadUserWorks() {
  loading.value = true;
  errorMsg.value = "";
  try {
    const me = await user.ensureMe();
    tip.value = me?.role === "admin"
      ? "你是管理员：右上角可随时切换到「管理模式」做站点管理。"
      : "欢迎回来！这里汇总了你参与的所有作品。";

    // 加载用户参与的作品
    const res = await authClient.userWorks();
    if (res.ok) {
      const mapWork = (w: Record<string, unknown>): Work => ({
        id: w.id as number,
        title: w.title as string,
        type: w.type as Work["type"],
        status: (w.status as Work["status"]) || "published",
        cover_url: (w.cover_url as string) || null,
        author: (w.author as string) || null,
        tags: [],
        created_at: (w.created_at as string) || "",
        updated_at: (w.updated_at as string) || undefined,
      });
      uploadedWorks.value = res.uploaded.map(mapWork);
      // 待审核/草稿作品只属于上传者，不应出现在「我支持/我推荐」的公开关系列表里
      supportedWorks.value = res.supported.map(mapWork).filter((w) => w.status === "published");
      recommendedWorks.value = res.recommended.map(mapWork).filter((w) => w.status === "published");
    }
  } catch (e) {
    errorMsg.value = extractErrMsg(e, "获取个人资料失败");
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  loadUserWorks();
  loadBindings();
});
onBeforeUnmount(stopBindPoll);
</script>

<template>
  <section class="me">
    <div class="card profile-card">
      <div class="avatar" v-if="user.current?.avatar_url">
        <img :src="user.current.avatar_url" :alt="user.current.nickname" />
      </div>
      <div class="avatar" v-else>{{ user.current?.nickname?.[0] ?? "?" }}</div>
      <div class="pinfo">
        <div class="row">
          <h2 style="margin: 0">{{ user.current?.nickname }}</h2>
          <span v-if="user.role === 'admin'" class="badge badge-admin">管理员</span>
          <span v-else class="badge badge-member">群友</span>
        </div>
        <div class="muted text-sm">QQ：{{ user.current?.qq }} · 注册时间 {{ user.current?.created_at?.slice(0, 10) ?? "—" }}</div>
        <div class="mt-4">
          <div class="alert alert-info" v-if="tip">{{ tip }}</div>
        </div>
        <div class="mt-4">
          <button class="btn btn-ghost btn-sm" type="button" @click="openNameModal">✏️ 修改昵称</button>
          <button class="btn btn-ghost btn-sm" type="button" @click="changePwdVisible = true">🔑 修改密码</button>
        </div>
      </div>
    </div>

    <!-- openid 绑定管理 -->
    <section class="mt-8">
      <div class="section-head">
        <h3>🔗 机器人身份绑定</h3>
        <button class="btn btn-primary btn-sm" @click="startBind" :disabled="bindLoading">
          {{ bindings.length > 0 ? '+ 添加 / 重新绑定' : '绑定机器人' }}
        </button>
      </div>
      <p class="muted text-sm">
        绑定后，你在 QQ 群里 @机器人 发送命令时，机器人能识别你的身份。私聊只有一个 openid，每个群各有一个群 openid。点击名称可查看对应的 openid。
      </p>
      <div v-if="bindErrorMsg" class="alert alert-error mt-2">{{ bindErrorMsg }}</div>
      <div v-if="bindLoading" class="card mt-2"><div class="muted text-sm" style="text-align:center;padding:16px">加载中…</div></div>
      <div v-else-if="bindings.length === 0" class="card mt-2">
        <div class="text-sm" style="text-align:center;padding:16px">
          <p>尚未绑定任何机器人身份。</p>
          <p class="muted mt-2">点击上方按钮开始绑定，把验证码发给机器人即可。</p>
        </div>
      </div>
      <div v-else class="binding-list mt-2">
        <div v-for="b in bindings" :key="b.id" class="card binding-item">
          <div class="binding-info">
            <!-- 场景徽章：群聊显示群名（点击看群 openid），私聊固定为「私聊」 -->
            <span
              v-if="b.openid_type === 'group'"
              class="badge badge-muted"
              :class="{ 'badge-clickable': !!b.group_openid, 'badge-mono': showGroupOpenidId === b.id }"
              :title="b.group_openid ? '点击查看群 openid' : ''"
              @click="toggleGroupOpenid(b)"
            >{{ groupLabel(b) }}</span>
            <span v-else class="badge badge-muted">私聊</span>
            <!-- 个人身份：默认显示站点用户名，点击显示个人 openid -->
            <span
              class="binding-name badge-clickable"
              :class="{ 'binding-openid': showUserOpenidId === b.id }"
              title="点击查看个人 openid"
              @click="toggleUserOpenid(b)"
            >{{ showUserOpenidId === b.id ? b.openid : personLabel(b) }}</span>
            <span class="muted text-sm">绑定于 {{ b.created_at.slice(0, 10) }}</span>
          </div>
          <button class="btn btn-ghost btn-sm" @click="deleteBindingById(b.id)">解绑</button>
        </div>
      </div>
    </section>

    <!-- 绑定码弹窗 -->
    <div v-if="bindModalVisible" class="modal-mask" @click.self="dismissBindModal">
      <div class="modal card">
        <div class="modal-head">
          <h3>🔗 绑定机器人身份</h3>
        </div>
        <p class="muted text-sm">
          把下方验证码发给机器人即可完成绑定：
        </p>
        <div class="bind-code-box">
          <span class="bind-code-text">{{ bindCode }}</span>
          <button class="btn btn-ghost btn-sm" type="button" @click="copyBindCode">复制</button>
        </div>
        <div class="bind-cmd-box">@机器人 /绑定 -c {{ bindCode }}</div>
        <p class="muted text-sm">在群里 @机器人 发送上方命令（或私聊机器人），本页会自动检测，绑定成功后弹窗自动关闭。</p>
        <div class="modal-foot">
          <span class="muted text-sm">有效期 {{ bindExpires }} 分钟</span>
          <button class="btn btn-ghost btn-sm" type="button" @click="dismissBindModal">取消</button>
        </div>
      </div>
    </div>

    <section class="mt-8">
      <h3>🎯 我参与的作品</h3>
      
      <div v-if="errorMsg" class="alert alert-error">{{ errorMsg }}</div>
      <div v-if="loading" class="card">
        <div class="muted text-sm" style="text-align: center; padding: 24px">加载中…</div>
      </div>
      
      <div v-else>
        <!-- 上传的作品 -->
        <div v-if="uploadedWorks.length > 0" class="mb-8">
          <h4>📤 我上传的作品</h4>
          <div class="grid-cards mt-2">
            <RouterLink
              v-for="w in uploadedWorks"
              :key="w.id"
              :to="`/works/${w.id}`"
              class="card work-card"
            >
              <div class="work-cover">
                <img v-if="w.cover_url" :src="w.cover_url" :alt="w.title" />
                <div v-else class="cover-placeholder">{{ w.type === 'gallery' ? '🖼️' : '📚' }}</div>
              </div>
              <div class="work-info">
                <div class="work-title">{{ w.title }}</div>
                <div class="work-meta">
                  <span class="badge badge-muted">{{ w.type }}</span>
                  <span v-if="w.status === 'pending'" class="badge badge-pending">待审核</span>
                  <span v-else-if="w.status === 'draft'" class="badge badge-draft">草稿</span>
                  <span v-if="w.author" class="muted text-sm">作者：{{ w.author }}</span>
                  <span v-if="w.updated_at" class="muted text-sm">{{ w.updated_at.slice(0, 10) }}</span>
                </div>
              </div>
            </RouterLink>
          </div>
        </div>

        <!-- 支持的作品 -->
        <div v-if="supportedWorks.length > 0" class="mb-8">
          <h4>❤️ 我支持的作品</h4>
          <div class="grid-cards mt-2">
            <RouterLink
              v-for="w in supportedWorks"
              :key="w.id"
              :to="`/works/${w.id}`"
              class="card work-card"
            >
              <div class="work-cover">
                <img v-if="w.cover_url" :src="w.cover_url" :alt="w.title" />
                <div v-else class="cover-placeholder">{{ w.type === 'gallery' ? '🖼️' : '📚' }}</div>
              </div>
              <div class="work-info">
                <div class="work-title">{{ w.title }}</div>
                <div class="work-meta">
                  <span class="badge badge-muted">{{ w.type }}</span>
                  <span v-if="w.author" class="muted text-sm">作者：{{ w.author }}</span>
                  <span v-if="w.updated_at" class="muted text-sm">{{ w.updated_at.slice(0, 10) }}</span>
                </div>
              </div>
            </RouterLink>
          </div>
        </div>
        
        <!-- 推荐的作品 -->
        <div v-if="recommendedWorks.length > 0" class="mb-8">
          <h4>⭐ 我推荐的作品</h4>
          <div class="grid-cards mt-2">
            <RouterLink
              v-for="w in recommendedWorks"
              :key="w.id"
              :to="`/works/${w.id}`"
              class="card work-card"
            >
              <div class="work-cover">
                <img v-if="w.cover_url" :src="w.cover_url" :alt="w.title" />
                <div v-else class="cover-placeholder">{{ w.type === 'gallery' ? '🖼️' : '📚' }}</div>
              </div>
              <div class="work-info">
                <div class="work-title">{{ w.title }}</div>
                <div class="work-meta">
                  <span class="badge badge-muted">{{ w.type }}</span>
                  <span v-if="w.author" class="muted text-sm">作者：{{ w.author }}</span>
                  <span v-if="w.updated_at" class="muted text-sm">{{ w.updated_at.slice(0, 10) }}</span>
                </div>
              </div>
            </RouterLink>
          </div>
        </div>
        
        <!-- 空状态 -->
        <div v-if="uploadedWorks.length === 0 && supportedWorks.length === 0 && recommendedWorks.length === 0" class="card">
          <div class="text-sm" style="text-align: center; padding: 24px">
            <p>你还没有参与任何作品。</p>
            <p class="muted mt-2">快去作品库看看吧！</p>
            <RouterLink to="/works" class="btn btn-primary mt-4">浏览作品库</RouterLink>
          </div>
        </div>
      </div>
    </section>
  </section>

  <!-- 修改昵称弹窗 -->
  <div v-if="nameModalVisible" class="modal-mask" @click.self="nameModalVisible = false">
    <div class="modal card">
      <div class="modal-head">
        <h3>✏️ 修改显示名称</h3>
      </div>
      <p class="muted text-sm">
        机器人拿不到你在群内的名称（群名片），这里填写的名称将用于绑定列表及站点各处展示，建议直接填群内名称。
      </p>
      <input
        v-model="nameInput"
        class="input mt-2"
        type="text"
        maxlength="50"
        placeholder="请输入显示名称"
        @keyup.enter="saveDisplayName"
      />
      <div v-if="nameErrorMsg" class="alert alert-error mt-2">{{ nameErrorMsg }}</div>
      <div class="modal-foot">
        <button class="btn btn-ghost btn-sm" type="button" :disabled="nameSaving" @click="nameModalVisible = false">取消</button>
        <button class="btn btn-primary btn-sm" type="button" :disabled="nameSaving" @click="saveDisplayName">
          {{ nameSaving ? "保存中…" : "保存" }}
        </button>
      </div>
    </div>
  </div>

  <!-- 修改密码弹窗（从「我的」页面进入：校验当前密码或验证码） -->
  <ChangePasswordModal :visible="changePwdVisible" @close="changePwdVisible = false" />
</template>

<style scoped>
.profile-card {
  display: flex;
  gap: 18px;
  align-items: center;
}
.avatar {
  width: 64px;
  height: 64px;
  border-radius: 50%;
  background: var(--accent-gradient);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  font-weight: 700;
  flex-shrink: 0;
  box-shadow: 0 10px 20px rgba(79, 70, 229, 0.18);
  overflow: hidden;
}
.avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.pinfo {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
}

/* 作品卡片样式 */
.grid-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
  margin-top: 12px;
}

.work-card {
  display: flex;
  gap: 12px;
  padding: 12px;
  text-decoration: none;
  color: inherit;
  transition: transform 0.2s, box-shadow 0.2s;
  border: 1px solid var(--color-border);
}
.work-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.08);
  border-color: var(--color-primary);
}

.work-cover {
  width: 64px;
  height: 64px;
  border-radius: 8px;
  overflow: hidden;
  flex-shrink: 0;
  background: var(--color-bg-subtle);
  display: flex;
  align-items: center;
  justify-content: center;
}
.work-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.cover-placeholder {
  font-size: 24px;
  opacity: 0.6;
}

.work-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.work-title {
  font-weight: 600;
  font-size: 15px;
  line-height: 1.3;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.work-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  font-size: 12px;
}
.badge-pending {
  background: #fef3c7;
  color: #92400e;
}
.badge-draft {
  background: #e5e7eb;
  color: #374151;
}

h4 {
  margin: 0 0 8px;
  font-size: 16px;
  font-weight: 600;
  color: var(--color-text);
}

/* ---------- 绑定管理 ---------- */
.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 4px;
}
.binding-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.binding-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
}
.binding-info {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.binding-name {
  font-size: 14px;
  color: var(--color-primary, #4f46e5);
}
.binding-name:hover {
  text-decoration: underline;
}
.binding-openid {
  font-family: var(--font-mono, monospace);
  font-size: 12px;
  word-break: break-all;
  color: var(--color-text-muted, #888);
  max-width: 100%;
}
.badge-clickable {
  cursor: pointer;
}
/* 徽章切换为 openid 时用等宽字体 */
.badge-mono {
  font-family: var(--font-mono, monospace);
  font-size: 12px;
  word-break: break-all;
  max-width: 100%;
}

/* ---------- 绑定码弹窗 ---------- */
.modal-mask {
  position: fixed;
  inset: 0;
  z-index: 50;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.modal {
  width: 100%;
  max-width: 420px;
  box-shadow: var(--shadow-md);
}
.modal-head h3 {
  margin: 0 0 8px;
}
.bind-code-box {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 12px 0 8px;
  padding: 12px 16px;
  border-radius: 12px;
  background: rgba(79, 70, 229, 0.08);
  border: 1px dashed rgba(79, 70, 229, 0.4);
}
.bind-code-text {
  font-size: 28px;
  font-weight: 700;
  letter-spacing: 6px;
  font-family: var(--font-mono, monospace);
}
.bind-cmd-box {
  padding: 10px 14px;
  border-radius: 10px;
  background: var(--color-bg-soft, #f5f5f7);
  border: 1px solid var(--color-border, #e5e5ea);
  font-family: var(--font-mono, monospace);
  margin-bottom: 8px;
  user-select: all;
}
.modal-foot {
  margin-top: 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
@media (max-width: 720px) {
  .profile-card {
    gap: 12px;
  }
  .avatar {
    width: 52px;
    height: 52px;
    font-size: 20px;
  }
  .grid-cards {
    grid-template-columns: 1fr;
    gap: 10px;
  }
  .binding-item {
    flex-wrap: wrap;
    padding: 10px 12px;
  }
  .bind-code-text {
    font-size: 22px;
    letter-spacing: 4px;
  }
}
</style>
