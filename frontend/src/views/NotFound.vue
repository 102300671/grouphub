<script setup lang="ts">
import { RouterLink } from "vue-router";
</script>

<template>
  <div style="display: flex; align-items: center; justify-content: center; min-height: 70vh; padding: 20px">
    <div class="card" style="text-align: center; max-width: 420px">
      <div style="font-size: 64px">🚧</div>
      <h2 style="margin: 10px 0 6px">404 · 页面不存在</h2>
      <p class="muted text-sm" style="margin: 0 0 20px">链接可能已失效，或者你输错了地址。</p>
      <RouterLink to="/" class="btn btn-primary">回到首页</RouterLink>
    </div>
  </div>
</template>
