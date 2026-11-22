import { defineConfig } from 'vite'
import preact from '@preact/preset-vite'

// The built UI is served by the Python host from src/omos/web/dist (shipped inside the package).
export default defineConfig({
  plugins: [preact()],
  base: '/',
  build: {
    outDir: '../src/omos/web/dist',
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
  server: {
    port: 5173,
    proxy: { '/api': { target: 'http://127.0.0.1:8765', ws: true } },
  },
})
