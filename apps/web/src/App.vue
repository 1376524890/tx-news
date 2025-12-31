<!-- Input: Router 当前路由 -->
<!-- Output: 全局布局（Header + RouterView） -->
<!-- Pos: SPA 根组件（变更时同步更新以上注释与所属目录 FOLDER.md） -->

<script setup lang="ts">
const isAdmin = import.meta.env.MODE === 'admin'
</script>

<template>
  <header class="header">
    <div class="brand">
      <div class="brand-title">TX-News</div>
      <div class="brand-subtitle">高时效性经济新闻 · 采集/去重/分析 · A股</div>
    </div>

    <div class="panel-actions" v-if="!isAdmin">
      <router-link to="/" class="btn btn-ghost" exact-active-class="active">对话</router-link>
      <router-link to="/dashboard" class="btn btn-ghost" exact-active-class="active">看板</router-link>
      <router-link to="/config" class="btn btn-ghost" exact-active-class="active">配置</router-link>
    </div>
    <div class="panel-actions" v-else>
      <router-link to="/" class="btn btn-ghost" exact-active-class="active">配置</router-link>
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
