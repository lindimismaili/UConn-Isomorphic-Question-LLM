import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

/**
 * Frontend origin for proxied backend requests during local development.
 *
 * Vite forwards protected API requests and `/version.json` to this URL.
 */
const backendTarget = process.env.VITE_BACKEND_URL ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': backendTarget,
      '/version.json': backendTarget,
    },
  },
  build: {
    outDir: '../dist',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        // Main app entry.
        main: path.resolve(process.cwd(), 'index.html'),
        // Design-system showcase: a living catalog of the UI primitives.
        designSystem: path.resolve(process.cwd(), 'design-system.html'),
      },
    },
  },
})
