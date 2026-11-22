// UI timings for scripts/benchmark.py. Prints one JSON line. Env: URL = sign-in link of a running host.
const { chromium } = require('playwright')

;(async () => {
  const browser = await chromium.launch()
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } })
  const t0 = Date.now()
  await page.goto(process.env.URL)
  await page.waitForSelector('.shell')
  const signedIn = Date.now() - t0
  const nav = await page.evaluate(() => {
    const n = performance.getEntriesByType('navigation')[0]
    return { dom_content_loaded_ms: Math.round(n.domContentLoadedEventEnd), load_ms: Math.round(n.loadEventEnd) }
  })
  const chart = async () => {
    await page.evaluate(() => window.omosNavigate('/'))
    await page.waitForTimeout(300)
    const s = Date.now()
    await page.evaluate(() => window.omosNavigate('/charts'))
    const cell = page.locator('.chartcell').first()
    const input = cell.locator('input[aria-label="Instrument"]')
    if ((await input.inputValue()) !== 'SIM:DEMO-TREND') {
      await input.fill('SIM:DEMO-TREND')
      await input.press('Enter')
    }
    await cell.locator('.badge', { hasText: 'SIMULATED' }).waitFor({ timeout: 60000 })
    return Date.now() - s
  }
  const first = await chart()   // includes the host generating and caching bars
  const warm = []
  for (let i = 0; i < 5; i++) warm.push(await chart())
  warm.sort((a, b) => a - b)
  const heap = await page.evaluate(() => (performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1048576) : null))
  console.log(JSON.stringify({ sign_in_to_app_ms: signedIn, ...nav, chart_first_load_ms: first, chart_open_warm_median_ms: warm[2], js_heap_mb: heap }))
  await browser.close()
})().catch((e) => { console.error(e); process.exit(1) })
