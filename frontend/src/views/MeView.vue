<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { useUserStore } from "@/stores/user";
import { authClient, extractErrMsg } from "@/api/http";
import type { OpenidBindingItem, Work } from "@/types/api";

const user = useUserStore();
const tip = ref("");
const loading = ref(false);
const errorMsg = ref("");

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

function maskOpenid(openid: string): string {
  if (openid.length <= 8) return openid;
  return openid.slice(0, 4) + "****" + openid.slice(-4);
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
        cover_url: (w.cover_url as string) || null,
        author: (w.author as string) || null,
        tags: [],
        created_at: (w.created_at as string) || "",
        updated_at: (w.updated_at as string) || undefined,
      });
      uploadedWorks.value = res.uploaded.map(mapWork);
      supportedWorks.value = res.supported.map(mapWork);
      recommendedWorks.value = res.recommended.map(mapWork);
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
        绑定后，你在 QQ 群里 @机器人 发送命令时，机器人能识别你的身份。一个 QQ 可绑定多个 openid（如不同群的身份）。
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
            <span class="binding-openid">{{ maskOpenid(b.openid) }}</span>
            <span
              class="badge badge-muted"
              :class="{ 'badge-clickable': b.openid_type === 'group' && b.group_openid }"
              :title="b.openid_type === 'group' && b.group_openid ? '点击查看群 openid' : ''"
              @click="toggleGroupOpenid(b)"
            >{{ groupLabel(b) }}</span>
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
        <div class="bind-cmd-box">@机器人 绑定 {{ bindCode }}</div>
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
.binding-openid {
  font-family: var(--font-mono, monospace);
  font-size: 14px;
}
.badge-clickable {
  cursor: pointer;
  font-family: var(--font-mono, monospace);
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
