import { copyFileSync, mkdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { defineConfig, type Plugin } from 'vite'
import preact from '@preact/preset-vite'

const OUT = '../src/omos/web/dist'

// The minified bundle drops license comments, so ship the license and NOTICE texts of the
// libraries that end up in it (Apache-2.0 section 4(d) requires the NOTICE contents).
function thirdPartyLicenses(): Plugin {
  const files: [string, string][] = [
    ['node_modules/klinecharts/LICENSE', 'klinecharts-LICENSE.txt'],
    ['node_modules/klinecharts/NOTICE', 'klinecharts-NOTICE.txt'],
    ['node_modules/klinecharts/licenses/LICENSE-lightweight-charts', 'lightweight-charts-LICENSE.txt'],
    ['node_modules/preact/LICENSE', 'preact-LICENSE.txt'],
  ]
  return {
    name: 'omos-third-party-licenses',
    apply: 'build',
    closeBundle() {
      const dir = resolve(__dirname, OUT, 'third-party')
      mkdirSync(dir, { recursive: true })
      for (const [from, to] of files) copyFileSync(resolve(__dirname, from), resolve(dir, to))
    },
  }
}

// The built UI is served by the Python host from src/omos/web/dist (shipped inside the package).
export default defineConfig({
  plugins: [preact(), thirdPartyLicenses()],
  base: '/',
  build: {
    outDir: OUT,
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
  server: {
    port: 5173,
    proxy: { '/api': { target: 'http://127.0.0.1:8765', ws: true } },
  },
})
