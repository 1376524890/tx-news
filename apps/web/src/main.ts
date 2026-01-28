// Input: Vue 应用入口 + Router 配置
// Output: 挂载 Vue 应用与 Router
// Pos: 前端入口（变更时同步更新以上注释与所属目录 FOLDER.md）
import { createApp } from 'vue'
import './style.css'
import App from './App.vue'
import router from './router'

createApp(App).use(router).mount('#app')
