import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { defineConfig, type Plugin } from 'vite'
import preact from '@preact/preset-vite'

const OUT = '../src/omos/web/dist'

// The minified bundle drops license comments, so ship the license and NOTICE texts of the
// libraries that end up in it (Apache-2.0 section 4(d) requires the NOTICE contents).
const MIT = `Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
associated documentation files (the "Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the
following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial
portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT
LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO
EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE
USE OR OTHER DEALINGS IN THE SOFTWARE.`

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
      // qrcode-generator ships its MIT notice only as a source header; keep it, plus the license text
      const header = readFileSync(resolve(__dirname, 'node_modules/qrcode-generator/dist/qrcode.mjs'), 'utf8')
        .split('\n').slice(0, 16).join('\n')
      writeFileSync(resolve(dir, 'qrcode-generator-LICENSE.txt'), `${header}\n\n${MIT}`)
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
