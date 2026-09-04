<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useUserStore } from "@/stores/user";
import { authClient, extractErrMsg } from "@/api/http";
import type { Work } from "@/types/api";

const user = useUserStore();
const tip = ref("");
const loading = ref(false);
const errorMsg = ref("");

// 用户参与的作品数据
const uploadedWorks = ref<Work[]>([]);
const supportedWorks = ref<Work[]>([]);
const recommendedWorks = ref<Work[]>([]);

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
      // 转换数据格式以匹配 Work 类型
      uploadedWorks.value = res.uploaded.map(w => ({
        ...w,
        cover_url: w.cover_url || undefined,
        author: w.author || undefined,
        updated_at: w.updated_at || undefined,
      }));
      supportedWorks.value = res.supported.map(w => ({
        ...w,
        cover_url: w.cover_url || undefined,
        author: w.author || undefined,
        updated_at: w.updated_at || undefined,
      }));
      recommendedWorks.value = res.recommended.map(w => ({
        ...w,
        cover_url: w.cover_url || undefined,
        author: w.author || undefined,
        updated_at: w.updated_at || undefined,
      }));
    }
  } catch (e) {
    errorMsg.value = extractErrMsg(e, "获取个人资料失败");
  } finally {
    loading.value = false;
  }
}

onMounted(loadUserWorks);
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
</style>
