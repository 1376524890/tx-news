// Input: Vite dev/build config + env overrides
// Output: Vite config (proxy/build/allowedHosts)
// Pos: Frontend build/dev config (update this header + apps/web/FOLDER.md when changed)

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  const outDir = mode === 'admin' ? 'dist_admin' : 'dist_public'
  const apiPort = process.env.TXNEWS_API_PORT || '8000'
  const adminPort = process.env.TXNEWS_ADMIN_PORT || process.env.TXNEWS_API_PORT || '8001'
  const apiBase = `http://localhost:${apiPort}`
  const adminBase = `http://localhost:${adminPort}`
  const extraHosts = (process.env.TXNEWS_VITE_ALLOWED_HOSTS || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
  const allowedHosts = ['localhost', '127.0.0.1', 'txnews.plk161211.top', ...extraHosts]
  return {
    plugins: [vue()],
    build: { outDir, emptyOutDir: false },
    server: {
      allowedHosts,
      proxy: {
        '/chat': apiBase,
        '/search': apiBase,
        '/signals': apiBase,
        '/status': apiBase,
        '/health': apiBase,
        '/articles': apiBase,
        '/events': apiBase,
        '/entities': apiBase,
        '/kg': apiBase,
        '/feedback': apiBase,
        // Admin UI can optionally use a dedicated backend, but API also serves /api/config.
        '/api': mode === 'admin' ? adminBase : apiBase
      }
    }
  }
})
