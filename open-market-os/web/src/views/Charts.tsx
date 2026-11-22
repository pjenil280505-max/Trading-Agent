import { useEffect, useRef, useState } from 'preact/hooks'
import { get, put } from '../api'
import { ChartAdapter, indicatorCatalog, type ActiveIndicator, type ChartKind, type DataMeta, type IndSpec, type Scale } from '../chart/adapter'
import { DRAWING_TOOLS } from '../chart/overlays'
import { Btn, Field, Modal, StatusBadge } from '../components/ui'
import { errText, prefs, toast, useStore } from '../store'

type CellCfg = { instrument: string; tf: string; kind: ChartKind; scale: Scale; indicators: { id: string; params: any }[] }
type Layout = { grid: string; cells: CellCfg[]; sync: { crosshair: boolean; symbol: boolean; tf: boolean; range: boolean } }
const GRIDS: Record<string, [number, number]> = { '1': [1, 1], '2h': [1, 2], '2v': [2, 1], '3': [1, 3], '4': [2, 2], '6': [2, 3], '8': [2, 4] }
const TFS = ['1m', '3m', '5m', '15m', '30m', '45m', '1h', '2h', '4h', '1d', '1w', '1M']
const DEFAULT_CELL: CellCfg = { instrument: 'OKX:BTC-USDT', tf: '1h', kind: 'candle_solid', scale: 'normal', indicators: [{ id: 'ema', params: { length: 20 } }] }

// cross-cell sync bus
type SyncMsg = { from: number; kind: 'crosshair' | 'symbol' | 'tf' | 'range'; value: any }
const syncSubs = new Set<(m: SyncMsg) => void>()
const broadcast = (m: SyncMsg) => syncSubs.forEach((f) => f(m))

export function Charts() {
  const [layout, setLayout] = useState<Layout | null>(null)
  const [focus, setFocus] = useState(0)
  const [custom, setCustom] = useState('2x2')
  const saveTimer = useRef<number>()

  useEffect(() => {
    // list first: a missing workspace is normal on first run and should not produce a 404
    get<any[]>('/workspaces').then((ws) => ws.some((w) => w.name === 'default') ? get('/workspaces/default') : { layout: {} })
      .then((w) => setLayout(normalise(w.layout))).catch(() => setLayout(normalise({})))
  }, [])

  const update = (fn: (l: Layout) => Layout) => {
    setLayout((l) => {
      const next = fn(l!)
      clearTimeout(saveTimer.current)
      saveTimer.current = window.setTimeout(() => { put('/workspaces/default', { layout: next }).catch(() => {}) }, 800)
      return next
    })
  }
  if (!layout) return <div class="muted">Loading workspace…</div>
  const [rows, cols] = GRIDS[layout.grid] || parseGrid(layout.grid)
  const n = rows * cols
  const cells = Array.from({ length: n }, (_, i) => layout.cells[i] || { ...DEFAULT_CELL })
  const mobile = window.innerWidth < 760

  return (
    <>
      <div class="row">
        <span class="muted small">Layout</span>
        {Object.keys(GRIDS).map((g) => <Btn sm on={layout.grid === g} onClick={() => update((l) => ({ ...l, grid: g }))}>{g}</Btn>)}
        <input style="width:70px" value={custom} onInput={(e) => setCustom((e.target as HTMLInputElement).value)} title="custom rows x cols, e.g. 3x3" />
        <Btn sm onClick={() => { const g = parseGrid(custom); if (g[0] * g[1] <= 16) update((l) => ({ ...l, grid: `${g[0]}x${g[1]}` })) }}>Apply</Btn>
        <span class="sp" />
        <span class="muted small">Sync</span>
        {(['crosshair', 'symbol', 'tf', 'range'] as const).map((k) => (
          <label class="check small"><input type="checkbox" checked={layout.sync[k]}
            onChange={(e) => update((l) => ({ ...l, sync: { ...l.sync, [k]: (e.target as HTMLInputElement).checked } }))} />{k}</label>
        ))}
      </div>
      <div class="chartgrid" style={`grid-template-columns:repeat(${mobile ? 1 : cols},minmax(0,1fr));grid-template-rows:repeat(${mobile ? n : rows},minmax(${mobile ? 380 : 220}px,1fr));min-height:${mobile ? n * 390 : 560}px`}>
        {cells.map((c, i) => (
          <ChartCell key={i} index={i} cfg={c} focused={focus === i} sync={layout.sync}
            onFocus={() => setFocus(i)}
            onChange={(cfg) => update((l) => { const cs = [...cells]; cs[i] = cfg; return { ...l, cells: cs } })} />
        ))}
      </div>
    </>
  )
}

function parseGrid(s: string): [number, number] {
  const m = /^(\d)x(\d)$/.exec(s)
  return m ? [Math.max(1, Number(m[1])), Math.max(1, Number(m[2]))] : [1, 1]
}

function normalise(l: any): Layout {
  return { grid: l?.grid || '1', cells: Array.isArray(l?.cells) ? l.cells : [{ ...DEFAULT_CELL }],
    sync: { crosshair: true, symbol: false, tf: false, range: false, ...(l?.sync || {}) } }
}

function ChartCell(p: { index: number; cfg: CellCfg; focused: boolean; sync: Layout['sync']; onFocus: () => void; onChange: (c: CellCfg) => void }) {
  const host = useRef<HTMLDivElement>(null)
  const ad = useRef<ChartAdapter | null>(null)
  const [meta, setMeta] = useState<DataMeta | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState<string | null>(null)
  const [sym, setSym] = useState(p.cfg.instrument)
  const [active, setActive] = useState<ActiveIndicator[]>([])
  const [specs, setSpecs] = useState<IndSpec[]>([])
  const [adding, setAdding] = useState<IndSpec | null>(null)
  const [tree, setTree] = useState(false)
  const [full, setFull] = useState(false)
  const [results, setResults] = useState<any[]>([])
  const [, setTick] = useState(0)
  const pr = useStore(prefs)
  const cfg = useRef(p.cfg)
  cfg.current = p.cfg
  const syncRef = useRef(p.sync)
  syncRef.current = p.sync

  useEffect(() => {
    const a = new ChartAdapter(host.current!, {
      onMeta: (m) => { setMeta(m); setError('') },
      onError: (e) => setError(e),
      onLoading: setLoading,
      onCrosshair: (ts) => syncRef.current.crosshair && broadcast({ from: p.index, kind: 'crosshair', value: ts }),
      onRange: (from) => syncRef.current.range && broadcast({ from: p.index, kind: 'range', value: from }),
      onDrawingsChanged: () => setTick((x) => x + 1),
    })
    ad.current = a
    a.kind = p.cfg.kind
    a.load(p.cfg.instrument, p.cfg.tf)
    a.setScale(p.cfg.scale)
    Promise.all(p.cfg.indicators.map((i) => a.addIndicator(i.id, i.params))).then(() => setActive([...a.indicators])).catch((e) => setError(errText(e)))
    indicatorCatalog().then(setSpecs).catch(() => {})
    const onSync = (m: SyncMsg) => {
      if (m.from === p.index) return
      if (m.kind === 'crosshair') a.showCrosshairAt(m.value)
      if (m.kind === 'range') a.showRange(m.value)
      if (m.kind === 'symbol' && syncRef.current.symbol) change({ instrument: m.value }, false)
      if (m.kind === 'tf' && syncRef.current.tf) change({ tf: m.value }, false)
    }
    syncSubs.add(onSync)
    const ro = new ResizeObserver(() => a.resize())
    ro.observe(host.current!)
    return () => { syncSubs.delete(onSync); ro.disconnect(); a.destroy() }
  }, [])

  useEffect(() => { ad.current?.applyTheme() }, [pr.theme])
  useEffect(() => { setTimeout(() => ad.current?.resize(), 0) }, [full])

  const change = (patch: Partial<CellCfg>, emit = true) => {
    const a = ad.current!
    const next = { ...cfg.current, ...patch }
    if (patch.instrument !== undefined || patch.tf !== undefined) {
      a.load(next.instrument, next.tf)
      setSym(next.instrument)
      if (emit && patch.instrument !== undefined) broadcast({ from: p.index, kind: 'symbol', value: next.instrument })
      if (emit && patch.tf !== undefined) broadcast({ from: p.index, kind: 'tf', value: next.tf })
    }
    if (patch.kind) a.setKind(patch.kind)
    if (patch.scale) a.setScale(patch.scale)
    next.indicators = a.indicators.map((i) => ({ id: i.id, params: i.params }))
    p.onChange(next)
  }

  const search = async () => {
    const q = sym.trim()
    if (q.includes(':')) { change({ instrument: q.toUpperCase().startsWith('YAHOO:') ? `YAHOO:${q.split(':')[1].toUpperCase()}` : q }); setResults([]); return }
    try { setResults((await get('/instruments/search', { q })).results.slice(0, 12)) } catch (e) { toast(errText(e), 'error') }
  }

  const a = ad.current
  const simulated = meta?.status === 'SIMULATED'
  return (
    <div class={`chartcell ${p.focused ? 'focus' : ''} ${full ? 'fullscreen' : ''}`} onPointerDown={p.onFocus}>
      <div class="chartbar">
        <input aria-label="Instrument" value={sym} onInput={(e) => setSym((e.target as HTMLInputElement).value)}
          onKeyDown={(e) => e.key === 'Enter' && search()} placeholder="Search or PROVIDER:SYMBOL" />
        <select aria-label="Timeframe" value={p.cfg.tf} onChange={(e) => change({ tf: (e.target as HTMLSelectElement).value })}>
          {TFS.map((t) => <option>{t}</option>)}
        </select>
        <select aria-label="Chart type" value={p.cfg.kind} onChange={(e) => change({ kind: (e.target as HTMLSelectElement).value as ChartKind })}>
          <option value="candle_solid">Candles</option><option value="candle_stroke">Hollow candles</option>
          <option value="heikin_ashi">Heikin-Ashi</option><option value="ohlc">OHLC bars</option>
          <option value="line">Line</option><option value="area">Area</option>
        </select>
        <select aria-label="Price scale" value={p.cfg.scale} onChange={(e) => change({ scale: (e.target as HTMLSelectElement).value as Scale })}>
          <option value="normal">Linear</option><option value="logarithm">Log</option><option value="percentage">%</option>
        </select>
        <select aria-label="Add indicator" value="" onChange={(e) => {
          const s = specs.find((x) => x.id === (e.target as HTMLSelectElement).value)
          if (s) setAdding(s)
        }}>
          <option value="">+ Indicator</option>
          {['trend', 'momentum', 'volatility', 'volume', 'structure'].map((cat) => (
            <optgroup label={cat}>{specs.filter((s) => s.category === cat).map((s) => <option value={s.id}>{s.name}</option>)}</optgroup>
          ))}
        </select>
        {meta && <StatusBadge status={meta.status} stale={meta.stale} source={meta.source} />}
        <span class="sp" />
        <Btn sm title="Snapshot (PNG)" onClick={() => { const url = a!.snapshot(); const l = document.createElement('a'); l.href = url; l.download = `${p.cfg.instrument}-${p.cfg.tf}.png`; l.click() }}>📷</Btn>
        <Btn sm title="Fullscreen" onClick={() => setFull(!full)}>{full ? '⤡' : '⤢'}</Btn>
      </div>
      {p.focused && (
        <div class="chartbar drawbar">
          {DRAWING_TOOLS.map((t) => <Btn sm title={t.title} onClick={() => {
            if (t.name === 'textNote') { const txt = prompt('Note text'); if (txt) a!.startDrawing(t.name, { text: txt.slice(0, 200) }) } else a!.startDrawing(t.name)
          }}>{t.label}</Btn>)}
          <Btn sm on={a?.magnet !== 'normal'} title="Magnet mode (snap to OHLC)" onClick={() => { a!.magnet = a!.magnet === 'normal' ? 'strong_magnet' : 'normal'; setTick((x) => x + 1) }}>🧲</Btn>
          <Btn sm title="Lock all drawings" onClick={() => a!.setLockAll(true)}>🔒</Btn>
          <Btn sm title="Unlock all" onClick={() => a!.setLockAll(false)}>🔓</Btn>
          <Btn sm title="Hide/show all" onClick={() => a!.setVisibleAll(!(a!.drawings()[0]?.visible ?? true))}>👁</Btn>
          <Btn sm title="Object tree" onClick={() => setTree(true)}>☷ {a?.drawings().length || 0}</Btn>
          <Btn sm kind="ghost" title="Delete all drawings on this symbol" onClick={() => confirm('Delete all drawings on this symbol?') && a!.clearDrawings()}>🗑</Btn>
          {active.map((i) => <span class="badge" style="color:var(--muted)">{i.id}{Object.keys(i.params).length ? `(${Object.values(i.params).join(',')})` : ''}
            <a href="#" onClick={(e) => { e.preventDefault(); a!.removeIndicator(i.uid); setActive([...a!.indicators]); change({}) }}>✕</a></span>)}
        </div>
      )}
      <div class="chartbody">
        <div class="kl" ref={host} />
        {simulated && <div class="chartmsg" style="align-items:flex-start;color:var(--sim)">SIMULATED DATA — NOT REAL PRICES</div>}
        {meta?.stale && <div class="chartmsg" style="align-items:flex-end;color:var(--bad)">Provider unavailable — cached bars only, not live</div>}
        {loading && !error && <div class="chartmsg"><span>{loading}</span></div>}
        {error && <div class="chartmsg"><div class="alertbox error">{error}</div></div>}
      </div>
      {results.length > 0 && (
        <Modal title="Choose an instrument" onClose={() => setResults([])}>
          <table class="t"><tbody>{results.map((r) => (
            <tr style="cursor:pointer" onClick={() => { change({ instrument: r.id }); setResults([]) }}>
              <td class="mono">{r.id}</td><td>{r.name}</td><td class="muted">{r.asset_class}</td></tr>
          ))}</tbody></table>
        </Modal>
      )}
      {adding && <IndicatorDialog spec={adding} onClose={() => setAdding(null)} onAdd={async (params) => {
        try { await a!.addIndicator(adding.id, params); setActive([...a!.indicators]); change({}) } catch (e) { toast(errText(e), 'error') }
        setAdding(null)
      }} />}
      {tree && a && (
        <Modal title="Drawings on this symbol" onClose={() => setTree(false)}>
          {a.drawings().length === 0 ? <div class="muted">No drawings yet.</div> : (
            <table class="t"><thead><tr><th>Type</th><th>Points</th><th>State</th><th /></tr></thead><tbody>
              {a.drawings().map((o) => <tr><td>{o.name}</td><td class="small mono">{o.points.map((q) => `${new Date(q.timestamp!).toLocaleDateString()} @ ${q.value?.toPrecision(6)}`).join(' → ')}</td>
                <td>{o.lock ? 'locked' : ''} {o.visible === false ? 'hidden' : ''}</td>
                <td><Btn sm onClick={() => { a.removeDrawing(o.id); setTick((x) => x + 1) }}>Delete</Btn></td></tr>)}
            </tbody></table>
          )}
          <p class="small muted">Drawings are stored on the host with time/price anchors, so they stay put when you change timeframe, zoom or scale.</p>
        </Modal>
      )}
    </div>
  )
}

function IndicatorDialog(p: { spec: IndSpec; onClose: () => void; onAdd: (params: Record<string, any>) => void }) {
  const [vals, setVals] = useState<Record<string, any>>(Object.fromEntries(p.spec.params.map((x: any) => [x.name, x.default])))
  return (
    <Modal title={p.spec.name} onClose={p.onClose}>
      <div class="grid g2">
        {p.spec.params.map((x: any) => (
          <Field label={x.name}>
            {x.kind === 'choice'
              ? <select value={vals[x.name]} onChange={(e) => setVals({ ...vals, [x.name]: (e.target as HTMLSelectElement).value })}>{x.choices.map((c: string) => <option>{c}</option>)}</select>
              : <input type="number" step={x.kind === 'float' ? 'any' : '1'} min={x.min} max={x.max} value={vals[x.name]}
                  onInput={(e) => setVals({ ...vals, [x.name]: Number((e.target as HTMLInputElement).value) })} />}
          </Field>
        ))}
      </div>
      {p.spec.params.length === 0 && <div class="muted">No parameters.</div>}
      <p class="small muted">Values are calculated by the host with the same tested code the backtester and screener use.</p>
      <div class="row"><span class="sp" /><Btn kind="primary" onClick={() => p.onAdd(vals)}>Add</Btn></div>
    </Modal>
  )
}
