// Input: Vue Router 路由配置 + Vite build mode（public/admin）
// Output: SPA 路由（public：/ 对话；admin：/ 配置）
// Pos: 前端路由入口（变更时同步更新以上注释与所属目录 FOLDER.md）

import { createRouter, createWebHistory } from 'vue-router'

const isAdmin = import.meta.env.MODE === 'admin'

const router = createRouter({
  history: createWebHistory(),
  routes:
    isAdmin
      ? [
          {
            path: '/',
            name: 'config',
            component: () => import('../views/ConfigView.vue')
          }
        ]
      : [
          {
            path: '/',
            name: 'chat',
            component: () => import('../views/ChatView.vue')
          },
          {
            path: '/config',
            name: 'config',
            component: () => import('../views/ConfigView.vue')
          },
          {
            path: '/dashboard',
            name: 'dashboard',
            component: () => import('../views/DashboardView.vue')
          },
          {
            path: '/:pathMatch(.*)*',
            redirect: '/'
          }
        ]
})

export default router
