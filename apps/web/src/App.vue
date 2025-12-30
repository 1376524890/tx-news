<script setup lang="ts">
// No script logic needed for now
</script>

<template>
  <header class="header">
    <div class="brand">
      <div class="brand-title">TX-News</div>
      <div class="brand-subtitle">高时效性经济新闻 · 采集/去重/分析 · A股</div>
    </div>
    
    <div class="panel-actions">
        <router-link to="/" class="btn btn-ghost" active-class="active">对话</router-link>
        <router-link to="/admin" class="btn btn-ghost" active-class="active">管理台</router-link>
    </div>
  </header>

  <main class="layout">
    <router-view></router-view>
  </main>
</template>

<style scoped>
.active {
    background: rgba(255, 255, 255, 0.1);
    border-color: rgba(255, 255, 255, 0.2);
}
</style>