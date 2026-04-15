import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import fs from 'node:fs'
import path from 'node:path'

function embedAgentsConfigAsset() {
  const agentsPath = [
    path.resolve(process.cwd(), '..', 'backend', 'data', 'agents.json'),
    path.resolve(process.cwd(), 'backend', 'data', 'agents.json'),
    path.resolve(process.cwd(), '..', 'data', 'agents.json'),
  ].find((candidate) => fs.existsSync(candidate))
  return {
    name: 'embed-agents-config-asset',
    generateBundle() {
      if (!agentsPath) return
      const source = fs.readFileSync(agentsPath, 'utf-8')
      this.emitFile({ type: 'asset', fileName: 'agents.json', source })
      this.emitFile({ type: 'asset', fileName: 'data/agents.json', source })
    },
  }
}

// Avoid localhost IPv6/IPv4 resolution issues on Windows.
// Uvicorn here binds to 0.0.0.0 (IPv4), so prefer 127.0.0.1.
const backendTarget = process.env.VITE_BACKEND_URL ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react(), embedAgentsConfigAsset()],
  server: {
    proxy: {
      '/api': backendTarget,
      '/version.json': backendTarget,
      '/data': backendTarget,
      '/skills': backendTarget,
    },
  },
  build: {
    outDir: '../dist',
    emptyOutDir: true,
  },
})
