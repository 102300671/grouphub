<script setup lang="ts">
import { onMounted, ref } from "vue";
import { extractErrMsg, request } from "@/api/http";

const threshold = ref(3);
const loading = ref(false);
const saving = ref(false);
const errorMsg = ref("");
const successMsg = ref("");

async function load() {
  loading.value = true;
  errorMsg.value = "";
  try {
    const res = await request<{ show_relation_threshold: number }>({ url: "/admin/settings/show_relation_threshold", method: "GET" });
    threshold.value = res.show_relation_threshold;
  } catch (e) {
    errorMsg.value = extractErrMsg(e, "获取设置失败");
  } finally {
    loading.value = false;
  }
}

async function save() {
  saving.value = true;
  errorMsg.value = "";
  successMsg.value = "";
  try {
    const res = await request<{ show_relation_threshold: number; note: string }>({
      url: "/admin/settings/show_relation_threshold",
      method: "PATCH",
      data: { threshold: threshold.value },
    });
    threshold.value = res.show_relation_threshold;
    successMsg.value = res.note;
  } catch (e) {
    errorMsg.value = extractErrMsg(e, "保存设置失败");
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>

<template>
  <section>
    <div>
      <h2 style="margin: 0">⚙️ 站点设置</h2>
      <p class="muted text-sm" style="margin: 6px 0 0">调整运行时策略；永久配置仍建议写回 backend/.env。</p>
    </div>

    <div v-if="errorMsg" class="alert alert-error mt-4">{{ errorMsg }}</div>
    <div v-if="successMsg" class="alert alert-success mt-4">{{ successMsg }}</div>

    <div class="card setting-card mt-4">
      <div class="row-between">
        <div>
          <h3 style="margin: 0 0 6px">作品关系展示阈值</h3>
          <p class="muted text-sm" style="margin: 0; line-height: 1.7">
            当支持者 / 推荐者数量达到该值时，作品详情页才展开具体名单。
            <br />例如设置为 3，只有至少 3 人参与时公开名单。
          </p>
        </div>
        <div class="threshold-control">
          <input v-model.number="threshold" class="input" type="number" min="0" max="1000" />
          <button class="btn btn-primary" :disabled="loading || saving" @click="save">
            {{ saving ? "保存中…" : "保存" }}
          </button>
        </div>
      </div>
      <div class="alert alert-info mt-4" style="margin-bottom: 0">
        当前修改只对正在运行的后端进程生效。要永久保存，请将 <code>SHOW_RELATION_THRESHOLD={{ threshold }}</code> 写入 <code>backend/.env</code> 并重启后端。
      </div>
    </div>
  </section>
</template>

<style scoped>
.setting-card {
  max-width: 820px;
}
.threshold-control {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-shrink: 0;
}
.threshold-control input {
  width: 110px;
}
code {
  padding: 1px 4px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.06);
  font-size: 12px;
}
@media (max-width: 640px) {
  .row-between {
    align-items: stretch;
    flex-direction: column;
  }
  .threshold-control {
    width: 100%;
  }
  .threshold-control input {
    flex: 1;
  }
}
</style>
