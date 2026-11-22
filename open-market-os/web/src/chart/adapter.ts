// ChartAdapter: the ONLY module that talks to the chart library (KLineChart, Apache-2.0).
// Data, indicators and drawings come from the Open Market OS host; the library only renders.
import { dispose, init, registerIndicator, type Chart, type KLineData, type OverlayEvent, type Period } from 'klinecharts'
import { get, live, post, put, del } from '../api'
import { registerOverlays } from './overlays'

export type ChartKind = 'candle_solid' | 'candle_stroke' | 'ohlc' | 'area' | 'line' | 'heikin_ashi'
export type Scale = 'normal' | 'logarithm' | 'percentage'
export type IndSpec = { id: string; name: string; category: string; overlay: boolean; params: any[]; outputs: { name: string; kind: string; color: string }[]; levels: number[] }
export type ActiveIndicator = { uid: string; id: string; params: Record<string, any> }
export type DataMeta = { status: string; source: string; stale?: boolean; quality?: any; instrument?: any; count: number }

const BARS: Record<string, number> = { low: 1500, medium: 5000, high: 20000 }
let barLimit = 1500
const FIRST_CHUNK = 1000

// Price/volume decimals per instrument, learned from tick size or the data itself (KLineChart's
// setSymbol reloads data, so we remember the answer to avoid reloading twice next time).
const PREC_KEY = 'omos.precision'
const precision: Record<string, [number, number]> = (() => { try { return JSON.parse(localStorage.getItem(PREC_KEY) || '{}') } catch { return {} } })()
// Decimals at 7 significant digits: real tick sizes survive, float noise from sources such as
// Yahoo (189.97999572753906) does not. Handles exponent notation for very small prices/ticks.
function decimals(v: number): number {
  if (!Number.isFinite(v) || v === 0) return 0
  const m = /^-?\d+(?:\.(\d+))?(?:e([+-]\d+))?$/.exec(Number(v.toPrecision(7)).toString())
  if (!m) return 0
  return Math.max(0, (m[1]?.length ?? 0) - (m[2] ? Number(m[2]) : 0))
}
function precisionOf(bars: number[][], tick?: number | null): [number, number] {
  let p = tick && tick > 0 ? decimals(tick) : 0
  let v = 0
  if (!(tick && tick > 0)) for (const b of bars.slice(-300)) p = Math.max(p, decimals(b[1]), decimals(b[2]), decimals(b[3]), decimals(b[4]))
  for (const b of bars.slice(-300)) v = Math.max(v, decimals(b[5] ?? 0))
  return [Math.min(p, 8), Math.min(v, 4)]
}
export function setDeviceTier(tier: string) { barLimit = BARS[tier] || 1500 }

export function tfToPeriod(tf: string): Period {
  const m = /^(\d+)([smhdwM])$/.exec(tf)
  if (!m) return { type: 'day', span: 1 }
  const n = Number(m[1])
  const t = ({ s: 'second', m: 'minute', h: 'hour', d: 'day', w: 'week', M: 'month' } as const)[m[2] as 's']
  return { type: t, span: n }
}

let catalog: IndSpec[] | null = null
export async function indicatorCatalog(): Promise<IndSpec[]> {
  if (!catalog) catalog = await get<IndSpec[]>('/indicators')
  return catalog
}

const registered = new Set<string>()

function registerBackendIndicator(spec: IndSpec) {
  const name = `omos_${spec.id}`
  if (registered.has(name)) return
  registered.add(name)
  registerIndicator<Record<string, number | undefined>, number, { instrument: string; tf: string; params: any; cache?: any }>({
    name,
    shortName: spec.name.length > 14 ? spec.id.toUpperCase() : spec.name,
    series: spec.overlay ? 'price' : 'normal',
    precision: 4,
    figures: spec.outputs.map((o) => ({
      key: o.name, title: `${o.name}: `,
      type: o.kind === 'histogram' ? 'bar' : o.kind === 'dots' || o.kind === 'markers' ? 'circle' : 'line',
      baseValue: o.kind === 'histogram' ? 0 : undefined,
      styles: ({ data }: any) => {
        if (o.kind === 'histogram') {
          const v = data?.current?.[o.name]
          return { color: v === undefined || v >= 0 ? 'rgba(38,166,154,0.7)' : 'rgba(239,83,80,0.7)' }
        }
        return { color: o.color }
      },
    })),
    // Values are computed by the host (single tested implementation shared with backtests and screeners).
    calc: async (dataList: KLineData[], indicator) => {
      const ext = indicator.extendData
      if (!ext || !dataList.length) return dataList.map(() => ({}))
      const now = Date.now()
      const lastTs = dataList[dataList.length - 1].timestamp
      let cache = ext.cache
      if (!cache || cache.firstTs > dataList[0].timestamp || (cache.lastTs !== lastTs && now - cache.at > 4000)) {
        const r = await post<{ ts: number[]; outputs: Record<string, (number | null)[]> }>('/indicators/compute', {
          instrument: ext.instrument, tf: ext.tf, id: spec.id, params: ext.params, limit: Math.min(dataList.length + 5, 50000),
        })
        const map = new Map<number, Record<string, number | undefined>>()
        r.ts.forEach((t, i) => {
          const row: Record<string, number | undefined> = {}
          for (const k of Object.keys(r.outputs)) { const v = r.outputs[k][i]; row[k] = v === null ? undefined : v }
          map.set(t, row)
        })
        cache = { map, firstTs: r.ts[0] ?? 0, lastTs, at: now }
        ext.cache = cache
      }
      return dataList.map((d) => cache.map.get(d.timestamp) || {})
    },
  })
}

export type Listener = { onMeta?: (m: DataMeta) => void; onError?: (e: string) => void; onLoading?: (msg: string | null) => void; onCrosshair?: (ts: number | null) => void;
  onRange?: (from: number, to: number) => void; onDrawingsChanged?: () => void }

export class ChartAdapter {
  chart: Chart
  instrument = ''
  tf = '1d'
  kind: ChartKind = 'candle_solid'
  scale: Scale = 'normal'
  indicators: ActiveIndicator[] = []
  magnet: 'normal' | 'weak_magnet' | 'strong_magnet' = 'normal'
  private unsubLive: (() => void) | null = null
  private offEvt: (() => boolean) | null = null
  private ls: Listener
  private loadedOlder = false
  private total = 0
  suppressSync = false

  constructor(el: HTMLElement, ls: Listener = {}) {
    registerOverlays()
    this.ls = ls
    const c = init(el, {
      styles: theme(),
      layout: { pane: { height: 140, minHeight: 60 } } as any,
      zoomAnchor: 'cursor',
    })
    if (!c) throw new Error('chart failed to initialise')
    this.chart = c
    this.chart.subscribeAction('onCrosshairChange', (d: any) => {
      if (!this.suppressSync) this.ls.onCrosshair?.(d?.kLineData?.timestamp ?? null)
    })
    this.chart.subscribeAction('onVisibleRangeChange', () => {
      if (this.suppressSync) return
      const r = this.chart.getVisibleRange()
      const dl = this.chart.getDataList()
      const a = dl[Math.max(0, r.from)]?.timestamp, b = dl[Math.min(dl.length - 1, r.to - 1)]?.timestamp
      if (a && b) this.ls.onRange?.(a, b)
    })
  }

  // ---------------------------------------------------------------- data
  load(instrument: string, tf: string) {
    const prev = this.instrument
    this.instrument = instrument.trim()
    if (prev && prev !== this.instrument) this.hideAllDrawings()
    this.tf = tf
    this.loadedOlder = false
    this.total = 0
    this.unsubLive?.()
    this.offEvt?.()
    const [pp, vp] = precision[this.instrument] || [2, 2]
    this.chart.setSymbol({ ticker: this.instrument, pricePrecision: pp, volumePrecision: vp })
    this.chart.setPeriod(tfToPeriod(tf))
    const self = this
    this.chart.setDataLoader({
      getBars: async ({ type, timestamp, callback }) => {
        try {
          if (type === 'init') {
            // A small first chunk paints fast even when the host must fetch history from the venue;
            // older bars load as the user scrolls left, up to this device's bar budget.
            const first = Math.min(barLimit, FIRST_CHUNK)
            const slow = window.setTimeout(() => self.ls.onLoading?.(`Loading ${self.instrument} ${self.tf}… the first load of a symbol fetches history from the data provider and can take several seconds; later loads come from the local cache.`), 700)
            self.ls.onLoading?.(`Loading ${self.instrument} ${self.tf}…`)
            try {
              const d = await get('/candles', { instrument: self.instrument, tf: self.tf, limit: first, ha: self.kind === 'heikin_ashi' })
              self.ls.onMeta?.({ status: d.status, source: d.source, stale: d.quality?.stale, quality: d.quality, instrument: d.instrument, count: d.count })
              self.total = d.bars.length
              callback(toK(d.bars), { forward: d.bars.length >= first * 0.9 && self.total < barLimit, backward: false })
              const [pp, vp] = precisionOf(d.bars, d.instrument?.tick_size)
              const cur = self.chart.getSymbol()
              if (d.bars.length && cur && (cur.pricePrecision !== pp || cur.volumePrecision !== vp)) {
                precision[self.instrument] = [pp, vp]
                try { localStorage.setItem(PREC_KEY, JSON.stringify(precision)) } catch { /* storage full or blocked */ }
                window.setTimeout(() => self.chart.setSymbol({ ticker: self.instrument, pricePrecision: pp, volumePrecision: vp }), 0)
              }
            } finally {
              window.clearTimeout(slow)
              self.ls.onLoading?.(null)
            }
            await self.loadDrawings()
          } else if (type === 'forward' && timestamp) {
            const want = Math.min(2000, barLimit - self.total)
            if (want <= 0) { callback([], false); return }
            const d = await get('/candles', { instrument: self.instrument, tf: self.tf, limit: want, end: timestamp - 1, ha: self.kind === 'heikin_ashi' })
            const bars = toK(d.bars).filter((b) => b.timestamp < timestamp)
            self.loadedOlder = true
            self.total += bars.length
            callback(bars, { forward: bars.length > 50 && self.total < barLimit, backward: false })
          } else callback([], false)
        } catch (e: any) {
          self.ls.onError?.(e?.message || String(e))
          callback([], false)
        }
      },
      subscribeBar: ({ callback }) => {
        self.unsubLive = live.subscribe(self.instrument, self.tf)
        self.offEvt = live.on((evt) => {
          if (evt.topic !== 'candle') return
          const d = evt.data
          if (d.instrument !== self.instrument || d.timeframe !== self.tf || self.kind === 'heikin_ashi') return
          const [t, o, h, l, c, v] = d.bar
          callback({ timestamp: t, open: o, high: h, low: l, close: c, volume: v })
        })
      },
      unsubscribeBar: () => { self.unsubLive?.(); self.offEvt?.(); self.unsubLive = null },
    })
    this.applyKind()
    this.reapplyIndicators()
  }

  // ---------------------------------------------------------------- appearance
  setKind(kind: ChartKind) {
    const wasHA = this.kind === 'heikin_ashi'
    this.kind = kind
    this.applyKind()
    if (wasHA !== (kind === 'heikin_ashi') && this.instrument) this.load(this.instrument, this.tf)
  }

  private applyKind() {
    const type = this.kind === 'heikin_ashi' ? 'candle_solid' : this.kind === 'line' ? 'area' : this.kind
    const area = this.kind === 'line'
      ? { lineColor: '#3d8bfd', backgroundColor: [{ offset: 0, color: 'rgba(61,139,253,0)' }, { offset: 1, color: 'rgba(61,139,253,0)' }] }
      : { lineColor: '#3d8bfd', backgroundColor: [{ offset: 0, color: 'rgba(61,139,253,0.02)' }, { offset: 1, color: 'rgba(61,139,253,0.25)' }] }
    this.chart.setStyles({ candle: { type: type as any, area } as any })
  }

  setScale(s: Scale) {
    this.scale = s
    this.chart.overrideYAxis({ paneId: 'candle_pane', name: s } as any)
  }

  applyTheme() { this.chart.setStyles(theme()) }

  // ---------------------------------------------------------------- indicators
  async addIndicator(id: string, params: Record<string, any> = {}, uid?: string) {
    const ai: ActiveIndicator = { uid: uid || `${id}_${Math.random().toString(36).slice(2, 8)}`, id, params }
    await this.createOnChart(ai)
    this.indicators.push(ai)
    return ai
  }

  private async createOnChart(ai: ActiveIndicator) {
    const specs = await indicatorCatalog()
    const spec = specs.find((s) => s.id === ai.id)
    if (!spec) throw new Error(`unknown indicator ${ai.id}`)
    registerBackendIndicator(spec)
    this.chart.createIndicator({
      name: `omos_${ai.id}`, id: ai.uid, paneId: spec.overlay ? 'candle_pane' : undefined,
      extendData: { instrument: this.instrument, tf: this.tf, params: ai.params },
    } as any, spec.overlay)
  }

  removeIndicator(uid: string) {
    this.chart.removeIndicator({ id: uid })
    this.indicators = this.indicators.filter((i) => i.uid !== uid)
  }

  // Re-create indicators for new instrument/timeframe WITHOUT emptying `indicators`: callers save the
  // workspace right after load(), and an emptied list would silently drop the user's indicators.
  private reapplyIndicators() {
    for (const i of this.indicators) {
      this.chart.removeIndicator({ id: i.uid })
      void this.createOnChart(i).catch((e) => this.ls.onError?.(e?.message || String(e)))
    }
  }

  // ---------------------------------------------------------------- drawings (persisted per instrument)
  // `persisted` = ids of drawings stored on the host and shown for the current instrument.
  // KLineChart fires onRemoved for EVERY removal, and onRemoved deletes the drawing on the host.
  // So a removal that only changes the view (other symbol, unmount) must first drop the id from
  // `persisted`; data reloads (timeframe, precision) never remove drawings at all.
  private persisted = new Set<string>()

  private hideAllDrawings() {
    this.persisted.clear()
    this.chart.removeOverlay()
  }

  startDrawing(name: string, extend: Record<string, any> = {}) {
    this.chart.createOverlay({
      name, mode: this.magnet, modeSensitivity: 8, extendData: extend,
      id: `dr_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`,
      onDrawEnd: (e: OverlayEvent<unknown>) => { void this.saveOverlay(e.overlay) },
      onPressedMoveEnd: (e: OverlayEvent<unknown>) => { void this.saveOverlay(e.overlay) },
      onRemoved: (e: OverlayEvent<unknown>) => { void this.deleteOverlay(e.overlay.id) },
    } as any)
  }

  private async saveOverlay(o: any) {
    const points = (o.points || []).filter((p: any) => p.timestamp !== undefined && p.value !== undefined)
      .map((p: any) => ({ t: p.timestamp, v: p.value }))
    if (!points.length) return
    await put('/drawings', { id: o.id, instrument_id: this.instrument, kind: o.name, points, locked: !!o.lock,
      visible: o.visible !== false, extend: o.extendData || {}, styles: o.styles || {} })
    this.persisted.add(o.id)
    this.ls.onDrawingsChanged?.()
  }

  private async deleteOverlay(id: string) {
    if (!this.persisted.has(id)) return
    this.persisted.delete(id)
    await del(`/drawings/${encodeURIComponent(id)}`)
    this.ls.onDrawingsChanged?.()
  }

  async loadDrawings() {
    const inst = this.instrument
    const list = await get<any[]>('/drawings', { instrument: inst })
    if (inst !== this.instrument) return  // the user switched symbol while this was loading
    for (const d of list) {
      const shown = this.chart.getOverlays({ id: d.id }).length > 0
      this.persisted.add(d.id)
      if (shown) continue  // idempotent: reloads only add drawings that are missing
      this.chart.createOverlay({
        id: d.id, name: d.kind, lock: d.locked, visible: d.visible, extendData: d.extend, styles: d.styles,
        points: d.points.map((p: any) => ({ timestamp: p.t, value: p.v })),
        onPressedMoveEnd: (e: OverlayEvent<unknown>) => { void this.saveOverlay(e.overlay) },
        onRemoved: (e: OverlayEvent<unknown>) => { void this.deleteOverlay(e.overlay.id) },
      } as any)
    }
    this.ls.onDrawingsChanged?.()
  }

  drawings() { return this.chart.getOverlays().filter((o) => this.persisted.has(o.id)) }

  async setLockAll(lock: boolean) {
    for (const o of this.drawings()) { this.chart.overrideOverlay({ id: o.id, lock }); await this.saveOverlay({ ...o, lock }) }
  }

  async setVisibleAll(visible: boolean) {
    for (const o of this.drawings()) { this.chart.overrideOverlay({ id: o.id, visible }); await this.saveOverlay({ ...o, visible }) }
  }

  async clearDrawings() {
    for (const o of this.drawings()) this.chart.removeOverlay({ id: o.id })
  }

  removeDrawing(id: string) { this.chart.removeOverlay({ id }) }

  // ---------------------------------------------------------------- sync, snapshot, misc
  showCrosshairAt(ts: number | null) {
    if (ts === null) return
    const px = this.chart.convertToPixel({ timestamp: ts, value: 0 }, { paneId: 'candle_pane' }) as { x?: number }
    if (px?.x === undefined) return
    this.suppressSync = true
    this.chart.executeAction('onCrosshairChange', { x: px.x, y: 30, paneId: 'candle_pane' })
    this.suppressSync = false
  }

  showRange(from: number) {
    this.suppressSync = true
    this.chart.scrollToTimestamp(from)
    setTimeout(() => { this.suppressSync = false }, 50)
  }

  snapshot(): string {
    const bg = getComputedStyle(document.documentElement).getPropertyValue('--panel').trim() || '#161c2b'
    return this.chart.getConvertPictureUrl(true, 'png', bg)
  }

  resize() { this.chart.resize() }

  destroy() {
    this.unsubLive?.()
    this.offEvt?.()
    this.persisted.clear()  // unmounting must never delete drawings on the host
    dispose(this.chart)
  }

  get olderLoaded() { return this.loadedOlder }
}

function toK(rows: number[][]): KLineData[] {
  return rows.map(([t, o, h, l, c, v]) => ({ timestamp: t, open: o, high: h, low: l, close: c, volume: v }))
}

function theme(): any {
  const css = getComputedStyle(document.documentElement)
  const text = css.getPropertyValue('--muted').trim() || '#8b93a7'
  const line = css.getPropertyValue('--line').trim() || '#2a3248'
  return {
    grid: { horizontal: { color: line }, vertical: { color: line } },
    candle: { tooltip: { text: { color: text } }, priceMark: { last: { text: { color: '#fff' } } } },
    indicator: { tooltip: { text: { color: text } } },
    xAxis: { axisLine: { color: line }, tickText: { color: text }, tickLine: { color: line } },
    yAxis: { axisLine: { color: line }, tickText: { color: text }, tickLine: { color: line } },
    separator: { color: line },
    crosshair: { horizontal: { text: { backgroundColor: '#3d8bfd' } }, vertical: { text: { backgroundColor: '#3d8bfd' } } },
  }
}
