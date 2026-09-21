<script setup lang="ts">
import { onMounted, ref } from "vue";
import { extractErrMsg } from "@/api/http";
import { adminClient } from "@/api";

const threshold = ref(3);
const requireReview = ref(false);
const loading = ref(false);
const savingThreshold = ref(false);
const savingReview = ref(false);
const errorMsg = ref("");
const successMsg = ref("");

function flash(msg: string) {
  successMsg.value = msg;
  window.setTimeout(() => {
    if (successMsg.value === msg) successMsg.value = "";
  }, 3000);
}

async function load() {
  loading.value = true;
  errorMsg.value = "";
  try {
    const res = await adminClient.getSettings();
    threshold.value = res.show_relation_threshold;
    requireReview.value = res.works_require_review;
  } catch (e) {
    errorMsg.value = extractErrMsg(e, "获取设置失败");
  } finally {
    loading.value = false;
  }
}

async function saveThreshold() {
  savingThreshold.value = true;
  errorMsg.value = "";
  try {
    const res = await adminClient.patchSettings({ show_relation_threshold: threshold.value });
    threshold.value = res.settings.show_relation_threshold;
    flash("阈值已保存（重启后端也不会丢失）");
  } catch (e) {
    errorMsg.value = extractErrMsg(e, "保存设置失败");
  } finally {
    savingThreshold.value = false;
  }
}

async function saveReview() {
  savingReview.value = true;
  errorMsg.value = "";
  try {
    const res = await adminClient.patchSettings({ works_require_review: requireReview.value });
    requireReview.value = res.settings.works_require_review;
    flash(
      requireReview.value
        ? "已开启：新提交的作品将进入待审核状态"
        : "已关闭：新提交的作品直接公开发布",
    );
  } catch (e) {
    // 失败时回滚开关，避免界面与服务端不一致
    requireReview.value = !requireReview.value;
    errorMsg.value = extractErrMsg(e, "保存设置失败");
  } finally {
    savingReview.value = false;
  }
}

onMounted(load);
</script>

<template>
  <section>
    <div>
      <h2 style="margin: 0">⚙️ 站点设置</h2>
      <p class="muted text-sm" style="margin: 6px 0 0">
        设置保存在数据库 admin_settings 表，重启后端自动生效；如需随部署统一配置，仍可在 backend/.env 中设默认值。
      </p>
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
          <button class="btn btn-primary" :disabled="loading || savingThreshold" @click="saveThreshold">
            {{ savingThreshold ? "保存中…" : "保存" }}
          </button>
        </div>
      </div>
    </div>

    <div class="card setting-card mt-4">
      <div class="row-between">
        <div>
          <h3 style="margin: 0 0 6px">新作品需审核</h3>
          <p class="muted text-sm" style="margin: 0; line-height: 1.7">
            开启后，网页上传与 QQ 群「安利」提交的作品先进入<strong>待审核</strong>状态，
            <br />仅上传者本人和管理员可见；在「作品管理」中通过后才会公开展示。
          </p>
        </div>
        <div class="threshold-control">
          <label class="switch-row">
            <input
              type="checkbox"
              class="switch"
              :checked="requireReview"
              :disabled="loading || savingReview"
              @change="requireReview = ($event.target as HTMLInputElement).checked; saveReview()"
            />
            <span>{{ requireReview ? "已开启" : "已关闭" }}</span>
          </label>
        </div>
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
.threshold-control input[type="number"] {
  width: 110px;
}
.switch-row {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 14px;
  white-space: nowrap;
}
.switch {
  width: 18px;
  height: 18px;
  cursor: pointer;
}
@media (max-width: 640px) {
  .row-between {
    align-items: stretch;
    flex-direction: column;
  }
  .threshold-control {
    width: 100%;
  }
  .threshold-control input[type="number"] {
    flex: 1;
  }
}
</style>
