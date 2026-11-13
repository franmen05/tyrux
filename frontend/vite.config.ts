import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// DBMON_API overrides the backend URL for the dev proxy (default http://localhost:8080).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', 'DBMON_')
  return {
    plugins: [react(), tailwindcss()],
    server: {
      port: 5173,
      proxy: { '/api': env.DBMON_API || 'http://localhost:8080' },
    },
  }
})
