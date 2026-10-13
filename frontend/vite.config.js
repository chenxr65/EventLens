import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发模式：vite dev server 代理 /api 到 FastAPI（8000）
// 生产模式：npm run build 产出 dist/，由 FastAPI 直接托管
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 1600,
  },
})
