<script setup lang="ts">
import { computed, ref } from "vue";

const props = withDefaults(
  defineProps<{
    src?: string | null;
    name?: string | null;
    size?: number;
  }>(),
  { src: null, name: "匿名", size: 28 }
);

// 图片加载失败时退回首字占位
const errored = ref(false);

// 无头像时取昵称首字做 fallback
const initial = computed(() => {
  const n = (props.name || "匿").trim();
  return n ? n[0].toUpperCase() : "?";
});
</script>

<template>
  <img
    v-if="src && !errored"
    class="avatar"
    :style="{ width: `${size}px`, height: `${size}px` }"
    :src="src"
    :alt="name || '头像'"
    @error="errored = true"
  />
  <span
    v-else
    class="avatar avatar-fallback"
    :style="{ width: `${size}px`, height: `${size}px`, fontSize: `${Math.round(size * 0.45)}px` }"
  >{{ initial }}</span>
</template>

<style scoped>
.avatar {
  border-radius: 50%;
  object-fit: cover;
  flex-shrink: 0;
}
.avatar-fallback {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--color-primary, #4f46e5);
  color: #fff;
  font-weight: 600;
}
</style>
