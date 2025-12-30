import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/chat': 'http://localhost:8000',
      '/admin': 'http://localhost:8000',
      '/search': 'http://localhost:8000',
      '/signals': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
      '/articles': 'http://localhost:8000',
      '/events': 'http://localhost:8000',
      '/entities': 'http://localhost:8000',
    }
  }
})