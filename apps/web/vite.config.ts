import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  const outDir = mode === 'admin' ? 'dist_admin' : 'dist_public'
  const apiPort = process.env.TXNEWS_API_PORT || '8000'
  const adminPort = process.env.TXNEWS_ADMIN_PORT || process.env.TXNEWS_API_PORT || '8001'
  const apiBase = `http://localhost:${apiPort}`
  const adminBase = `http://localhost:${adminPort}`
  return {
    plugins: [vue()],
    build: { outDir, emptyOutDir: false },
    server: {
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
