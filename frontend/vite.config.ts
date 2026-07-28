import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  build: {
    // built assets are served directly by FastAPI (muzilla/web/static)
    outDir: '../src/muzilla/web/static',
    emptyOutDir: true,
  },
  server: {
    // 1846 is the container's *published* port (docker-compose.yml); the
    // container itself still listens on 8080, matching `muzilla serve`'s
    // CLI default — so local dev against a bare `muzilla serve` is
    // unaffected by the Docker port change. Do not "fix" this to 1846.
    proxy: {
      '/api': 'http://localhost:8080',
    },
  },
})
