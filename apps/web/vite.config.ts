import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  const outDir = mode === 'admin' ? 'dist_admin' : 'dist_public'
  return {
    plugins: [vue()],
    build: { outDir, emptyOutDir: false },
    server: {
      proxy: {
        '/chat': 'http://localhost:8000',
        '/search': 'http://localhost:8000',
        '/signals': 'http://localhost:8000',
        '/status': 'http://localhost:8000',
        '/health': 'http://localhost:8000',
        '/articles': 'http://localhost:8000',
        '/events': 'http://localhost:8000',
        '/entities': 'http://localhost:8000',
        '/api': mode === 'admin' ? 'http://localhost:8001' : 'http://localhost:8000',
      }
    }
  }
})
