import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// dev 時把 /api 與 /ws 代理到本機後端（skeleton 跑在 8000）
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8000', ws: true },
    },
  },
})
