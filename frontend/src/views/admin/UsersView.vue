<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { extractErrMsg, request } from "@/api/http";
import { adminClient } from "@/api";
import type { AdminUser, UserRole } from "@/types/api";
import { useUserStore } from "@/stores/user";

const users = ref<AdminUser[]>([]);
const loading = ref(false);
const errorMsg = ref("");
const query = ref("");
const role = ref<"" | UserRole>("");
const activeFilter = ref<"" | "true" | "false">("");
const page = ref(1);
const pageSize = ref(50);
const deletingId = ref<number | null>(null);
const togglingId = ref<number | null>(null);
const user = useUserStore();

const pageTitle = computed(() => {
  const parts: string[] = [];
  if (role.value) parts.push(role.value === "admin" ? "管理员" : "普通用户");
  if (activeFilter.value === "false") parts.push("已禁用");
  parts.push(parts.length ? "列表" : "全部用户");
  return parts.join(" · ");
});

async function loadUsers() {
  loading.value = true;
  errorMsg.value = "";
  try {
    const params = new URLSearchParams({ page: String(page.value), page_size: String(pageSize.value) });
    if (query.value.trim()) params.set("q", query.value.trim());
    if (role.value) params.set("role", role.value);
    if (activeFilter.value) params.set("active", activeFilter.value);
    users.value = await request<AdminUser[]>({ url: `/admin/users?${params.toString()}`, method: "GET" });
  } catch (e) {
    errorMsg.value = extractErrMsg(e, "加载用户列表失败");
  } finally {
    loading.value = false;
  }
}

async function changeRole(u: AdminUser) {
  const next: UserRole = u.role === "admin" ? "member" : "admin";
  if (!window.confirm(`确认把 ${u.nickname}（QQ ${u.qq}）改为 ${next}？`)) return;
  try {
    await request({ url: `/admin/users/${u.id}/role`, method: "PATCH", data: { role: next } });
    await loadUsers();
  } catch (e) {
    errorMsg.value = extractErrMsg(e, "修改角色失败");
  }
}

async function toggleActive(u: AdminUser) {
  const next = !u.is_active;
  const tip = next
    ? `确认启用用户 ${u.nickname}（QQ ${u.qq}）？启用后可正常登录和使用机器人。`
    : `确认禁用用户 ${u.nickname}（QQ ${u.qq}）？\n禁用后该用户将无法登录站点，也无法使用 QQ 机器人命令（约 10 分钟内生效）。`;
  if (!window.confirm(tip)) return;
  togglingId.value = u.id;
  try {
    const res = await adminClient.setUserActive(u.id, next);
    u.is_active = res.is_active;
  } catch (e: any) {
    window.alert(extractErrMsg(e, "操作失败"));
  } finally {
    togglingId.value = null;
  }
}

async function deleteUser(u: AdminUser) {
  if (u.id === user.current?.id) {
    window.alert("不能删除你自己。");
    return;
  }
  const ok = window.confirm(
    `确定删除用户 ${u.nickname}（QQ ${u.qq}，ID ${u.id}）？\n` +
    "上传者作品、评论、阅读关系等将一并清理。受保护的 ADMIN_QQS 账号后端会拒绝删除。\n" +
    "如只是想阻止其登录，建议改用「禁用」。此操作不可恢复。"
  );
  if (!ok) return;
  deletingId.value = u.id;
  try {
    const res = await adminClient.deleteUser(u.id);
    if (res.ok) {
      users.value = users.value.filter((x) => x.id !== u.id);
    } else {
      window.alert(res.message || "删除失败");
    }
  } catch (e: any) {
    window.alert(extractErrMsg(e, "删除失败"));
  } finally {
    deletingId.value = null;
  }
}

function search() {
  page.value = 1;
  loadUsers();
}
function prevPage() {
  if (page.value > 1) {
    page.value -= 1;
    loadUsers();
  }
}
function nextPage() {
  if (users.value.length >= pageSize.value) {
    page.value += 1;
    loadUsers();
  }
}

onMounted(loadUsers);
</script>

<template>
  <section>
    <div class="row-between">
      <div>
        <h2 style="margin: 0">👥 用户管理</h2>
        <p class="muted text-sm" style="margin: 6px 0 0">{{ pageTitle }} · 角色修改受后端自保护规则约束。</p>
      </div>
    </div>

    <div class="card filters mt-4">
      <input v-model="query" class="input" placeholder="按 QQ 或昵称搜索" @keyup.enter="search" />
      <select v-model="role" class="input" @change="search">
        <option value="">全部角色</option>
        <option value="admin">管理员</option>
        <option value="member">普通用户</option>
      </select>
      <select v-model="activeFilter" class="input" @change="search">
        <option value="">全部状态</option>
        <option value="true">正常</option>
        <option value="false">已禁用</option>
      </select>
      <button class="btn btn-primary" @click="search">搜索</button>
    </div>

    <div v-if="errorMsg" class="alert alert-error mt-4">{{ errorMsg }}</div>
    <div v-if="loading" class="mt-4 muted text-sm">加载中…</div>

    <div v-else class="card table-wrap mt-4">
      <table class="simple">
        <thead>
          <tr><th>ID</th><th>QQ</th><th>昵称</th><th>角色</th><th>状态</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="u in users" :key="u.id" :class="{ 'row-disabled': !u.is_active }">
            <td class="muted">#{{ u.id }}</td>
            <td>{{ u.qq }}</td>
            <td>{{ u.nickname }}</td>
            <td>
              <span :class="['badge', u.role === 'admin' ? 'badge-admin' : 'badge-member']">
                {{ u.role === "admin" ? "管理员" : "普通用户" }}
              </span>
            </td>
            <td>
              <span :class="['badge', u.is_active ? 'badge-active' : 'badge-banned']">
                {{ u.is_active ? "正常" : "已禁用" }}
              </span>
            </td>
            <td>
              <div class="row" style="gap: 4px; justify-content: flex-start; flex-wrap: wrap;">
                <button class="btn btn-ghost text-sm" @click="changeRole(u)">
                  {{ u.role === "admin" ? "降为普通用户" : "设为管理员" }}
                </button>
                <button
                  class="btn text-sm"
                  :class="u.is_active ? 'btn-warn' : 'btn-primary'"
                  :disabled="togglingId === u.id || u.id === user.current?.id"
                  :title="u.id === user.current?.id ? '不能禁用你自己的账号' : ''"
                  @click="toggleActive(u)"
                >
                  {{ togglingId === u.id ? "处理中…" : (u.is_active ? "禁用" : "启用") }}
                </button>
                <button class="btn btn-danger text-sm" :disabled="deletingId === u.id || u.id === user.current?.id" @click="deleteUser(u)">
                  {{ deletingId === u.id ? "删除中…" : "删除用户" }}
                </button>
              </div>
            </td>
          </tr>
          <tr v-if="users.length === 0"><td colspan="6" class="muted" style="text-align: center">暂无用户</td></tr>
        </tbody>
      </table>
      <div class="pagination row-between mt-4">
        <button class="btn" :disabled="page <= 1" @click="prevPage">上一页</button>
        <span class="muted text-sm">第 {{ page }} 页</span>
        <button class="btn" :disabled="users.length < pageSize" @click="nextPage">下一页</button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.filters {
  display: flex;
  gap: 10px;
  align-items: center;
}
.filters .input:first-child {
  flex: 1;
}
.filters select {
  width: 140px;
}
.table-wrap {
  overflow-x: auto;
}
.btn-danger { background: #dc2626 !important; color: #fff !important; border-color: #dc2626 !important; }
.btn-danger:hover { background: #b91c1c !important; }
.btn-danger[disabled] { opacity: 0.6; cursor: not-allowed; }
.btn-warn { background: #d97706 !important; color: #fff !important; border-color: #d97706 !important; }
.btn-warn:hover { background: #b45309 !important; }
.btn[disabled] { opacity: 0.6; cursor: not-allowed; }
.text-sm { font-size: 12px !important; }
.badge-active { background: #dcfce7; color: #166534; }
.badge-banned { background: #fee2e2; color: #991b1b; }
.row-disabled { opacity: 0.7; }
.row-disabled td:nth-child(3) { text-decoration: line-through; }
@media (max-width: 640px) {
  .filters {
    align-items: stretch;
    flex-direction: column;
  }
  .filters select {
    width: auto;
  }
  table.simple {
    min-width: 620px;
  }
}
</style>
