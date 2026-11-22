// End-to-end tests of the web UI against a real, isolated host (see scripts/e2e.sh).
// Uses SIMULATED demo instruments (SIM:*) so results do not depend on markets or the network;
// the tests also check that this data is labelled SIMULATED everywhere it appears.
//
// Env: OMOS_E2E_BASE (http://127.0.0.1:PORT), OMOS_PY (python with omos installed),
//      OMOS_E2E_ARTIFACTS (folder for failure screenshots).
const { chromium, devices } = require('playwright')
const { execFileSync } = require('child_process')
const http = require('http')
const path = require('path')
const fs = require('fs')

const BASE = process.env.OMOS_E2E_BASE
const PY = process.env.OMOS_PY || 'python'
const ART = process.env.OMOS_E2E_ARTIFACTS || path.join(process.cwd(), 'e2e-artifacts')
const SYM = 'SIM:DEMO-TREND'
fs.mkdirSync(ART, { recursive: true })

const results = []
let browser

function signInUrl() {
  return execFileSync(PY, ['-m', 'omos', 'open', '--print-only'], { encoding: 'utf8' }).trim()
}

function watch(page) {
  const problems = []
  page.on('console', (m) => { if (m.type() === 'error') problems.push(`console: ${m.text()}`) })
  page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`))
  page.on('response', (r) => { if (r.url().includes('/api/') && r.status() >= 500) problems.push(`HTTP ${r.status()} ${r.url()}`) })
  return problems
}

async function signedInPage(opts = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, ...opts })
  const page = await ctx.newPage()
  const problems = watch(page)
  await page.goto(signInUrl())
  await page.locator('.shell, .wizard').first().waitFor({ timeout: 20000 })
  if (await page.locator('.wizard').count()) {  // keep tests independent of the setup-assistant test
    await page.getByRole('button', { name: 'Skip setup' }).click()
    await page.waitForSelector('.shell', { timeout: 20000 })
  }
  return { ctx, page, problems }
}

async function go(page, route) {
  await page.evaluate((r) => window.omosNavigate(r), route)
  await page.waitForTimeout(300)
}

async function toast(page, re, timeout = 15000) {
  await page.locator('.toast', { hasText: re }).first().waitFor({ timeout })
}

function assert(cond, msg) { if (!cond) throw new Error(`assertion failed: ${msg}`) }

// A form control by the START of its visible label. getByLabel() also matches option texts inside
// a <label> (e.g. "Supertrend multi-timeframe" contains "timeframe"), so it is ambiguous here.
function field(scope, labelStart) {
  const esc = labelStart.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return scope.locator('label.field').filter({ hasText: new RegExp('^\\s*' + esc) }).locator('input, select, textarea').first()
}

async function drawingsOnHost(page, instrument) {
  return page.evaluate(async (i) => {
    const r = await fetch(`/api/drawings?instrument=${encodeURIComponent(i)}`, { headers: { Authorization: `Bearer ${localStorage.getItem('omos.token')}` } })
    return (await r.json()).length
  }, instrument)
}

async function test(name, fn) {
  const t0 = Date.now()
  let page
  try {
    await fn((p) => { page = p })
    results.push({ name, ok: true, ms: Date.now() - t0 })
    console.log(`  ✔ ${name} (${Date.now() - t0} ms)`)
  } catch (e) {
    results.push({ name, ok: false, ms: Date.now() - t0, error: e.message })
    console.log(`  ✖ ${name}\n      ${e.message.split('\n').join('\n      ')}`)
    if (page) await page.screenshot({ path: path.join(ART, `${name.replace(/[^a-z0-9]+/gi, '_')}.png`) }).catch(() => {})
  }
}

function rawRequest(method, urlPath, headers = {}) {
  const u = new URL(BASE)
  return new Promise((resolve, reject) => {
    const req = http.request({ host: u.hostname, port: u.port, path: urlPath, method, headers }, (res) => {
      let body = ''
      res.on('data', (c) => { body += c })
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body }))
    })
    req.on('error', reject)
    req.end()
  })
}

;(async () => {
  browser = await chromium.launch()
  console.log(`E2E against ${BASE}`)

  await test('first run: setup assistant detects the computer, checks sources, offers local AI, finishes', async (setPage) => {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } })
    const page = await ctx.newPage()
    setPage(page)
    const problems = watch(page)
    await page.goto(signInUrl())
    await page.locator('.wizard').waitFor({ timeout: 20000 })
    await page.getByText('Performance profile', { exact: true }).waitFor()
    assert(await page.locator('text=paper trading').count() > 0, 'welcome step states paper trading')
    await page.getByRole('button', { name: 'Next' }).click()
    // the source check needs the internet; either the results table or the offline warning must appear
    await page.locator('.wizcard table.t, .wizcard .alertbox.warn').first().waitFor({ timeout: 45000 })
    await page.getByRole('button', { name: 'Next' }).click()
    await page.locator('text=Recommended model').waitFor({ timeout: 15000 })
    assert(await page.getByRole('button', { name: /Set up free local AI|Use this model|Download and use/ }).count() === 1, 'local AI action offered')
    await page.getByRole('button', { name: 'Continue' }).click()
    // never let the test write a real start-at-login entry for the user running it
    const auto = page.locator('label.row', { hasText: 'Start Open Market OS when I sign in' }).locator('input')
    if (await auto.isChecked()) await auto.uncheck()
    await page.getByRole('button', { name: 'Next' }).click()
    await page.getByRole('button', { name: 'Start using Open Market OS' }).click()
    await page.waitForSelector('.shell', { timeout: 20000 })
    await page.reload()
    await page.waitForSelector('.shell', { timeout: 20000 })
    assert(await page.locator('.wizard').count() === 0, 'setup assistant does not come back once finished')
    const st = await page.evaluate(async () => (await fetch('/api/setup', { headers: { Authorization: `Bearer ${localStorage.getItem('omos.token')}` } })).json())
    assert(st.completed === true, 'setup recorded as completed')
    assert(problems.length === 0, problems.join('; '))
    await ctx.close()
  })

  await test('sign-in: wrong code is refused, one-time link signs in and survives reload', async (setPage) => {
    const ctx = await browser.newContext()
    const page = await ctx.newPage()
    setPage(page)
    await page.goto(BASE)
    await page.getByPlaceholder('e.g. 7K9MXQ2P').fill('WRONGCODE1')
    await page.getByRole('button', { name: 'Sign in' }).click()
    await page.locator('.alertbox.error').waitFor()
    const link = signInUrl()
    await page.goto(link)
    await page.waitForSelector('.shell', { timeout: 20000 })
    assert(!page.url().includes('code='), 'sign-in code must be removed from the address bar')
    await page.reload()
    await page.waitForSelector('.shell', { timeout: 20000 })
    const reuse = await ctx.newPage()
    await reuse.goto(link)  // a used code must not sign in a second time
    await reuse.evaluate(() => localStorage.removeItem('omos.token'))
    await reuse.goto(link)
    await reuse.locator('text=Sign in to Open Market OS').waitFor({ timeout: 10000 })
    await ctx.close()
  })

  await test('every page renders without errors', async (setPage) => {
    const { ctx, page, problems } = await signedInPage()
    setPage(page)
    for (const r of ['/', '/charts', '/watchlists', '/screener', '/research', '/strategy', '/bots', '/portfolio', '/risk', '/alerts', '/ai', '/settings', '/status']) {
      await go(page, r)
      await page.locator('.topbar h1').waitFor()
      await page.waitForTimeout(700)
    }
    assert(problems.length === 0, `problems: ${problems.join(' | ')}`)
    await ctx.close()
  })

  await test('charts: simulated data is labelled, indicator added, drawing persists across reload, 4-chart layout', async (setPage) => {
    const { ctx, page, problems } = await signedInPage()
    setPage(page)
    await go(page, '/charts')
    const cell = page.locator('.chartcell').first()
    const sym = cell.locator('input[aria-label="Instrument"]')
    await sym.fill(SYM)
    await sym.press('Enter')
    await cell.locator('.badge', { hasText: 'SIMULATED' }).waitFor({ timeout: 20000 })
    await cell.locator('text=SIMULATED DATA — NOT REAL PRICES').waitFor()
    await cell.locator('select[aria-label="Timeframe"]').selectOption('1h')
    await cell.locator('.badge', { hasText: 'SIMULATED' }).waitFor({ timeout: 20000 })
    await cell.locator('select[aria-label="Add indicator"]').selectOption('rsi')
    await page.locator('.modal').getByRole('button', { name: 'Add' }).click()
    await cell.locator('.drawbar .badge', { hasText: 'rsi' }).waitFor()
    // draw a horizontal line in the middle of the price pane
    await cell.locator('button[title="Horizontal line"]').click()
    const box = await cell.locator('.chartbody').boundingBox()
    const saved = page.waitForResponse((r) => r.url().includes('/api/drawings') && r.request().method() === 'PUT')
    await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.35)
    assert((await saved).ok(), 'drawing was not saved')
    await cell.locator('button[title="Object tree"]', { hasText: '1' }).waitFor()
    // regression: data reloads and unmounting used to DELETE saved drawings on the host
    await cell.locator('select[aria-label="Timeframe"]').selectOption('4h')
    await page.waitForTimeout(2500)
    await cell.locator('button[title="Object tree"]', { hasText: '1' }).waitFor()
    assert(await drawingsOnHost(page, SYM) === 1, 'timeframe change deleted the drawing on the host')
    await go(page, '/')
    await page.waitForTimeout(500)
    assert(await drawingsOnHost(page, SYM) === 1, 'leaving the chart page deleted the drawing on the host')
    await go(page, '/charts')
    await page.locator('.chartcell').first().locator('button[title="Object tree"]', { hasText: '1' }).waitFor({ timeout: 20000 })
    await page.locator('.chartcell').first().locator('select[aria-label="Timeframe"]').selectOption('1h')
    await page.waitForTimeout(1200) // workspace save is debounced
    await page.reload()
    await page.waitForSelector('.shell')
    await go(page, '/charts')
    const cell2 = page.locator('.chartcell').first()
    await cell2.locator('.badge', { hasText: 'SIMULATED' }).waitFor({ timeout: 20000 })
    assert(await cell2.locator('input[aria-label="Instrument"]').inputValue() === SYM, 'workspace did not restore the symbol')
    await cell2.locator('button[title="Object tree"]', { hasText: '1' }).waitFor({ timeout: 10000 })
    await cell2.locator('.drawbar .badge', { hasText: 'rsi' }).waitFor()
    assert(await drawingsOnHost(page, SYM) === 1, 'reload changed the stored drawings')
    // compare with another symbol (own pane) and keyboard shortcuts on the focused chart
    page.once('dialog', (d) => d.accept('SIM:DEMO-RANGE'))
    await cell2.getByRole('button', { name: '⇄ Compare' }).click()
    await cell2.locator('.drawbar .badge', { hasText: 'compare' }).waitFor({ timeout: 10000 })
    await cell2.locator('.chartbody').click({ position: { x: 40, y: 40 } })
    for (const k of ['+', '-', 'ArrowLeft', 'ArrowRight', 'End']) await page.keyboard.press(k)
    await page.keyboard.press('?')
    await toast(page, /Chart keys/)
    await page.getByRole('button', { name: '4', exact: true }).click()
    await page.waitForFunction(() => document.querySelectorAll('.chartcell').length === 4)
    await page.getByRole('button', { name: '1', exact: true }).click()
    assert(problems.length === 0, `problems: ${problems.join(' | ')}`)
    await ctx.close()
  })

  await test('watchlist: create a list and add an instrument with its data label', async (setPage) => {
    const { ctx, page, problems } = await signedInPage()
    setPage(page)
    await go(page, '/watchlists')
    await page.getByPlaceholder('New list').fill('E2E list')
    await page.locator('.card', { hasText: 'Watchlists' }).getByRole('button', { name: 'Add' }).click()
    await page.getByRole('button', { name: 'E2E list' }).click()
    const add = page.getByPlaceholder('e.g. ETH, AAPL, RELIANCE, OKX:SOL-USDT')
    await add.fill('SIM:DEMO-RANGE')
    await add.press('Enter')
    const row = page.locator('tr', { hasText: 'SIM:DEMO-RANGE' })
    await row.locator('.badge', { hasText: 'SIMULATED' }).waitFor({ timeout: 20000 })
    assert(problems.length === 0, `problems: ${problems.join(' | ')}`)
    await ctx.close()
  })

  await test('strategy lab: template backtest with costs and warnings, save, walk-forward', async (setPage) => {
    const { ctx, page, problems } = await signedInPage()
    setPage(page)
    await go(page, '/strategy')
    await field(page, 'Start from a template').selectOption({ label: 'EMA crossover (trend)' })
    await field(page, 'Instrument (PROVIDER:SYMBOL)').fill(SYM)
    await field(page, 'Timeframe').selectOption('1h')
    await page.getByRole('button', { name: 'Run backtest' }).click()
    await page.locator('.stat', { hasText: 'Total return' }).waitFor({ timeout: 60000 })
    await page.locator('.alertbox', { hasText: 'SIMULATED data' }).waitFor()
    const fees = await page.locator('.stat', { hasText: 'Fees paid' }).locator('.v').textContent()
    assert(fees && fees !== '—', 'fees must be reported')
    await page.getByRole('button', { name: 'Save', exact: true }).click()
    await toast(page, /Saved .* v1/)
    await page.getByRole('button', { name: 'Walk-forward' }).click()
    await page.locator('text=Walk-forward analysis').waitFor({ timeout: 120000 })
    await page.locator('text=Walk-forward efficiency').waitFor()
    assert(problems.length === 0, `problems: ${problems.join(' | ')}`)
    await ctx.close()
  })

  await test('portfolio: paper market order fills through the risk engine, position closes', async (setPage) => {
    const { ctx, page, problems } = await signedInPage()
    setPage(page)
    await go(page, '/portfolio')
    await page.getByRole('tab', { name: 'Order ticket' }).click()
    await field(page, 'Instrument').fill(SYM)
    await field(page, 'Quantity').fill('1')
    await page.getByRole('button', { name: 'Buy (paper)' }).click()
    const res = page.locator('.alertbox', { hasText: /Order #\d+/ })
    await res.waitFor({ timeout: 20000 })
    const text = await res.textContent()
    assert(/filled/.test(text), `expected a fill, got: ${text}`)
    await res.locator('summary', { hasText: 'Risk checks' }).click()
    await page.getByRole('tab', { name: 'Positions' }).click()
    const row = page.locator('tr', { hasText: SYM })
    await row.waitFor()
    await row.locator('.badge', { hasText: 'SIMULATED' }).waitFor()
    await row.getByRole('button', { name: 'Close' }).click()
    await toast(page, /Close order filled/)
    assert(problems.length === 0, `problems: ${problems.join(' | ')}`)
    await ctx.close()
  })

  await test('bots: create a rule bot, validate, start, pause, stop', async (setPage) => {
    const { ctx, page, problems } = await signedInPage()
    setPage(page)
    await go(page, '/bots')
    await page.getByRole('button', { name: '+ New bot' }).click()
    await field(page, 'Name').fill('E2E trend bot')
    await field(page, 'Instrument').fill(SYM)
    await field(page, 'Timeframe').selectOption('1h')
    await page.getByRole('button', { name: 'Create (paper)' }).click()
    const detail = page.locator('.card', { hasText: 'E2E trend bot' }).filter({ has: page.locator('text=Overview') })
    await detail.locator('h2 .badge', { hasText: 'Draft' }).waitFor({ timeout: 15000 })
    await detail.getByRole('button', { name: 'Validate' }).click()
    await detail.locator('h2 .badge', { hasText: 'Testing' }).waitFor({ timeout: 20000 })
    await detail.getByRole('button', { name: 'Start' }).click()
    await detail.locator('h2 .badge', { hasText: 'Paper trading' }).waitFor({ timeout: 15000 })
    await detail.getByRole('button', { name: 'Pause' }).click()
    await detail.locator('h2 .badge', { hasText: 'Paused' }).waitFor({ timeout: 15000 })
    await detail.getByRole('button', { name: /Stop/ }).click()
    await detail.locator('h2 .badge', { hasText: 'Stopped' }).waitFor({ timeout: 15000 })
    await detail.getByRole('tab', { name: 'State history' }).click()
    await detail.locator('td', { hasText: 'running' }).first().waitFor()
    assert(problems.length === 0, `problems: ${problems.join(' | ')}`)
    await ctx.close()
  })

  await test('alerts: create a price alert and fire a test notification', async (setPage) => {
    const { ctx, page, problems } = await signedInPage()
    setPage(page)
    await go(page, '/alerts')
    await field(page, 'Instrument').fill(SYM)
    await field(page, 'Price level').fill('1')
    await field(page, 'Name (optional)').fill('E2E alert')
    await page.getByRole('button', { name: 'Create alert' }).click()
    const row = page.locator('tr', { hasText: 'E2E alert' })
    await row.waitFor({ timeout: 15000 })
    await row.getByRole('button', { name: 'Test' }).click()
    await toast(page, /Test notification sent/)
    await toast(page, /TEST: E2E alert/)   // delivered live over the WebSocket
    assert(problems.length === 0, `problems: ${problems.join(' | ')}`)
    await ctx.close()
  })

  await test('risk: emergency stop blocks orders; clearing it needs the exact phrase', async (setPage) => {
    const { ctx, page, problems } = await signedInPage()
    setPage(page)
    await go(page, '/risk')
    await page.getByRole('button', { name: '■ EMERGENCY STOP' }).click()
    await page.locator('.modal').getByRole('button', { name: 'Confirm' }).click()
    await page.locator('.alertbox.error', { hasText: 'ACTIVE since' }).waitFor({ timeout: 15000 })
    await go(page, '/portfolio')
    await page.getByRole('tab', { name: 'Order ticket' }).click()
    await field(page, 'Instrument').fill(SYM)
    await field(page, 'Quantity').fill('1')
    await page.getByRole('button', { name: 'Buy (paper)' }).click()
    await page.locator('.alertbox.error', { hasText: /rejected.*emergency stop/ }).waitFor({ timeout: 15000 })
    await go(page, '/risk')
    await page.getByRole('button', { name: 'Clear emergency stop…' }).click()
    const confirm = page.locator('.modal').getByRole('button', { name: 'Confirm' })
    assert(await confirm.isDisabled(), 'confirm must be disabled before the phrase is typed')
    await page.locator('.modal input').fill('resume trading')
    assert(await confirm.isDisabled(), 'confirm must stay disabled for a wrong phrase')
    await page.locator('.modal input').fill('RESUME TRADING')
    await confirm.click()
    await toast(page, /Emergency stop cleared/)
    assert(problems.length === 0, `problems: ${problems.join(' | ')}`)
    await ctx.close()
  })

  await test('settings: pairing code, MCP permission persists, live trading needs typed confirmation', async (setPage) => {
    const { ctx, page, problems } = await signedInPage()
    setPage(page)
    await go(page, '/settings')
    await page.getByRole('tab', { name: 'Devices & access' }).click()
    await page.getByRole('button', { name: 'Create one-time code' }).click()
    const code = (await page.locator('.alertbox.ok .mono').textContent()).trim()
    assert(/^[A-Z2-9]{8}$/.test(code), `unexpected pairing code format: ${code}`)
    await page.getByRole('tab', { name: 'MCP' }).click()
    const box = page.getByLabel('allow get_portfolio')
    const before = await box.isChecked()
    await box.click()
    await page.waitForTimeout(500)
    await page.reload()
    await page.waitForSelector('.shell')
    await go(page, '/settings')
    await page.getByRole('tab', { name: 'MCP' }).click()
    assert(await page.getByLabel('allow get_portfolio').isChecked() === !before, 'MCP permission change did not persist')
    await page.getByLabel('allow get_portfolio').click() // restore
    await page.getByRole('tab', { name: 'Live trading' }).click()
    await page.getByRole('button', { name: 'Turn on…' }).click()
    const confirm = page.locator('.modal').getByRole('button', { name: 'Confirm' })
    assert(await confirm.isDisabled(), 'live-trading confirm must start disabled')
    await page.locator('.modal input').fill('yes')
    assert(await confirm.isDisabled(), 'a casual "yes" must not enable live trading')
    await page.locator('.modal').getByRole('button', { name: 'Cancel' }).click()
    assert(problems.length === 0, `problems: ${problems.join(' | ')}`)
    await ctx.close()
  })

  await test('phone layout: bottom navigation, no horizontal scrolling', async (setPage) => {
    const { ctx, page, problems } = await signedInPage({ ...devices['iPhone 13'] })
    setPage(page)
    await page.locator('.bottomnav').waitFor()
    for (const r of ['/', '/charts', '/portfolio', '/settings']) {
      await go(page, r)
      await page.waitForTimeout(800)
      const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
      assert(over <= 1, `${r} scrolls horizontally by ${over}px on a phone`)
    }
    await page.locator('.bottomnav').getByRole('button', { name: /More/ }).click()
    await page.locator('.side.open').waitFor()
    assert(problems.length === 0, `problems: ${problems.join(' | ')}`)
    await ctx.close()
  })

  if (process.env.OMOS_E2E_LAN_IP) await test('phone pairing over the LAN with a one-time code and limited permissions', async (setPage) => {
    const { ctx: adminCtx, page: admin } = await signedInPage()
    await go(admin, '/settings')
    await admin.getByRole('tab', { name: 'Devices & access' }).click()
    await admin.getByRole('button', { name: 'Create one-time code' }).click()
    const code = (await admin.locator('.alertbox.ok .mono').textContent()).trim()
    // a separate browser without any HTTP proxy, like a real phone on the LAN (CI/dev sandboxes often
    // route non-local traffic through a proxy that cannot reach LAN addresses)
    const lanBrowser = await chromium.launch({ args: ['--no-proxy-server'] })
    const phoneCtx = await lanBrowser.newContext({ ...devices['iPhone 13'] })
    const phone = await phoneCtx.newPage()
    setPage(phone)
    const problems = watch(phone)
    const lan = BASE.replace('127.0.0.1', process.env.OMOS_E2E_LAN_IP)
    await phone.goto(lan)
    await phone.getByPlaceholder('e.g. 7K9MXQ2P').fill(code)
    await phone.getByRole('button', { name: 'Sign in' }).click()
    await phone.waitForSelector('.shell', { timeout: 20000 })
    const scopes = await phone.evaluate(async () => (await (await fetch('/api/auth/me', { headers: { Authorization: `Bearer ${localStorage.getItem('omos.token')}` } })).json()).scopes)
    assert(JSON.stringify(scopes) === JSON.stringify(['read', 'trade:paper']), `default phone preset should be read + paper trading, got ${scopes}`)
    await go(phone, '/settings')
    await phone.locator('.alertbox.warn', { hasText: 'lacks the admin permission' }).waitFor()
    const reuse = await (await lanBrowser.newContext()).newPage()
    await reuse.goto(lan)
    await reuse.getByPlaceholder('e.g. 7K9MXQ2P').fill(code)
    await reuse.getByRole('button', { name: 'Sign in' }).click()
    await reuse.locator('.alertbox.error').waitFor()           // the code works once
    // In some sandboxes Chromium's WebSocket to the machine's own LAN address is answered with 502 by the
    // network layer (a raw WebSocket client gets 101 + "hello"; see docs/DEVELOPMENT_STATUS.md). Only that
    // exact message is tolerated here; the live channel itself is covered by the alerts test on localhost.
    const real = problems.filter((m) => !/WebSocket connection to 'ws:\/\/[^']+\/api\/ws' failed: .*Unexpected response code: 502/.test(m))
    assert(real.length === 0, `problems: ${real.join(' | ')}`)
    await phoneCtx.close(); await adminCtx.close(); await lanBrowser.close()
  })

  await test('security: API needs a token, bad tokens are refused, headers set, foreign Host refused', async (setPage) => {
    const ctx = await browser.newContext()
    const page = await ctx.newPage()
    setPage(page)
    await page.goto(BASE)
    const noTok = await page.evaluate(async () => (await fetch('/api/system/status')).status)
    assert(noTok === 401, `expected 401 without a token, got ${noTok}`)
    await page.evaluate(() => localStorage.setItem('omos.token', 'not-a-real-token'))
    await page.reload()
    await page.locator('text=Sign in to Open Market OS').waitFor({ timeout: 10000 })
    const root = await rawRequest('GET', '/')
    const csp = root.headers['content-security-policy'] || ''
    assert(csp.includes("frame-ancestors 'none'") && csp.includes("script-src 'self'"), `weak or missing CSP: ${csp}`)
    assert(root.headers['x-content-type-options'] === 'nosniff', 'missing nosniff')
    const evil = await rawRequest('GET', '/api/health', { Host: 'evil.example.com' })
    assert(evil.status === 421, `foreign Host header must be refused (got ${evil.status})`)
    await ctx.close()
  })

  await browser.close()
  const failed = results.filter((r) => !r.ok)
  console.log(`\n${results.length - failed.length} passed, ${failed.length} failed`)
  fs.writeFileSync(path.join(ART, 'results.json'), JSON.stringify(results, null, 2))
  process.exit(failed.length ? 1 : 0)
})().catch((e) => { console.error('E2E runner crashed:', e); process.exit(2) })
