import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// En desarrollo, /api se redirige al backend FastAPI (puerto 8000).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: { '/api': { target: process.env.VITE_BACKEND_URL ?? 'http://localhost:8000', changeOrigin: true } },
  },
})
