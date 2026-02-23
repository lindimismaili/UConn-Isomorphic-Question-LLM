import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import fs from 'node:fs'
import path from 'node:path'

function embedAgentsConfigAsset() {
  const agentsPath = path.resolve(process.cwd(), '..', 'data', 'agents.json')
  return {
    name: 'embed-agents-config-asset',
    generateBundle() {
      if (!fs.existsSync(agentsPath)) return
      const source = fs.readFileSync(agentsPath, 'utf-8')
      this.emitFile({ type: 'asset', fileName: 'agents.json', source })
      this.emitFile({ type: 'asset', fileName: 'data/agents.json', source })
    },
  }
}

export default defineConfig({
  plugins: [react(), embedAgentsConfigAsset()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/version.json': 'http://localhost:8000',
      '/data': 'http://localhost:8000',
      '/skills': 'http://localhost:8000',
    },
  },
  build: {
    outDir: '../dist',
    emptyOutDir: true,
  },
})
