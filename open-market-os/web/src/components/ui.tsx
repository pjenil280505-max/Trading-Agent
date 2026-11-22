import type { ComponentChildren } from 'preact'
import { useEffect, useRef, useState } from 'preact/hooks'
import { errText, fmtNum, toasts, useStore } from '../store'

export function Card(p: { title?: ComponentChildren; actions?: ComponentChildren; children: ComponentChildren; class?: string }) {
  return (
    <section class={`card ${p.class || ''}`}>
      {p.title !== undefined && <h2>{p.title}<span class="sp" />{p.actions}</h2>}
      {p.children}
    </section>
  )
}

export function Badge(p: { kind: string; children?: ComponentChildren; title?: string }) {
  return <span class={`badge b-${p.kind}`} title={p.title}>{p.children ?? p.kind}</span>
}

/** Data-status label shown next to every price and chart: LIVE / DELAYED / HISTORICAL / SIMULATED / STALE / UNAVAILABLE.
 *  Streamed prices are LIVE; prices polled from the venue are also LIVE but say how often they are polled. */
export function StatusBadge(p: { status?: string; stale?: boolean; source?: string; pollSeconds?: number }) {
  if (!p.status) return null
  const st = p.stale ? 'STALE' : p.status
  const label = st === 'REALTIME' ? 'LIVE' : st
  const tips: Record<string, string> = {
    LIVE: 'Streamed from the venue as trades happen',
    REALTIME: `Polled from the venue${p.pollSeconds ? ` every ${p.pollSeconds} s` : ''}: up to one polling interval old`,
    DELAYED: 'The source delays its data (often 15–20 min) or is unofficial',
    HISTORICAL: 'End-of-day or archival data: not live',
    SIMULATED: 'NOT real prices: generated for demos and simulations',
    STALE: 'No fresh update: showing the last known data only. Bots do not trade on it',
    UNAVAILABLE: 'No data from this source right now',
    ESTIMATED: 'Derived values',
  }
  const kind = st === 'LIVE' ? 'REALTIME' : st  // green; .b-LIVE is the red live-TRADING mode badge
  return <Badge kind={kind} title={`${tips[st] || ''}${p.source ? ` — ${p.source}` : ''}`}>{label}{st === 'REALTIME' ? <span class="poll">·poll</span> : null}</Badge>
}

export function Btn(p: { onClick?: () => any; children: ComponentChildren; kind?: string; sm?: boolean; disabled?: boolean;
  title?: string; type?: 'button' | 'submit'; on?: boolean }) {
  const [busy, setBusy] = useState(false)
  return (
    <button type={p.type || 'button'} title={p.title} disabled={p.disabled || busy}
      class={`btn ${p.kind || ''} ${p.sm ? 'sm' : ''} ${p.on ? 'on' : ''}`}
      onClick={async () => {
        if (!p.onClick) return
        const r = p.onClick()
        if (r && typeof r.then === 'function') { setBusy(true); try { await r } finally { setBusy(false) } }
      }}>
      {busy ? '…' : p.children}
    </button>
  )
}

export function Field(p: { label: string; children: ComponentChildren; hint?: string }) {
  return <label class="field">{p.label}{p.children}{p.hint && <span class="small muted">{p.hint}</span>}</label>
}

export function Tabs<T extends string>(p: { tabs: [T, string][]; value: T; onChange: (t: T) => void }) {
  return (
    <div class="tabs" role="tablist">
      {p.tabs.map(([k, label]) => (
        <button role="tab" aria-selected={p.value === k} class={p.value === k ? 'on' : ''} onClick={() => p.onChange(k)}>{label}</button>
      ))}
    </div>
  )
}

export function Modal(p: { title: string; onClose: () => void; children: ComponentChildren }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === 'Escape' && p.onClose()
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [])
  return (
    <div class="modalbg" onClick={(e) => e.target === e.currentTarget && p.onClose()}>
      <div class="modal" role="dialog" aria-label={p.title}>
        <div class="row"><h2 style="margin:0;font-size:16px">{p.title}</h2><span class="sp" /><Btn sm kind="ghost" onClick={p.onClose}>✕</Btn></div>
        <div style="margin-top:12px">{p.children}</div>
      </div>
    </div>
  )
}

export function Toasts() {
  const list = useStore(toasts)
  return <div class="toasts" aria-live="polite">{list.map((t) => <div class={`toast ${t.kind}`}>{t.text}</div>)}</div>
}

export function ErrorBox(p: { error: unknown }) {
  if (!p.error) return null
  return <div class="alertbox error">{errText(p.error)}</div>
}

export function Empty(p: { children: ComponentChildren }) {
  return <div class="muted" style="padding:16px;text-align:center">{p.children}</div>
}

/** Load data from the API with loading/error states. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[] = []): { data: T | undefined; error: unknown; loading: boolean; reload: () => void } {
  const [data, setData] = useState<T>()
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(true)
  const [n, setN] = useState(0)
  useEffect(() => {
    let alive = true
    setLoading(true)
    fn().then((d) => { if (alive) { setData(d); setError(null) } })
      .catch((e) => { if (alive) setError(e) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [...deps, n])
  return { data, error, loading, reload: () => setN((x) => x + 1) }
}

export function Spark(p: { values: number[]; w?: number; h?: number }) {
  const w = p.w || 90, h = p.h || 26
  const v = p.values.filter((x) => Number.isFinite(x))
  if (v.length < 2) return <svg class="spark" width={w} height={h} />
  const min = Math.min(...v), max = Math.max(...v), span = max - min || 1
  const pts = v.map((x, i) => `${(i / (v.length - 1)) * w},${h - ((x - min) / span) * (h - 2) - 1}`).join(' ')
  const up = v[v.length - 1] >= v[0]
  return <svg class="spark" width={w} height={h} aria-hidden="true"><polyline points={pts} fill="none" stroke={up ? 'var(--ok)' : 'var(--bad)'} stroke-width="1.5" /></svg>
}

/** Simple canvas line chart for equity curves / indicator series (no extra dependency). */
export function LineChart(p: { series: { name: string; points: [number, number][]; color: string }[]; height?: number; zero?: boolean }) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const c = ref.current
    if (!c) return
    const dpr = window.devicePixelRatio || 1
    const w = c.clientWidth, h = p.height || 220
    c.width = w * dpr
    c.height = h * dpr
    const ctx = c.getContext('2d')!
    ctx.scale(dpr, dpr)
    ctx.clearRect(0, 0, w, h)
    const all = p.series.flatMap((s) => s.points)
    if (all.length < 2) return
    const xs = all.map((q) => q[0]), ys = all.map((q) => q[1])
    let ymin = Math.min(...ys), ymax = Math.max(...ys)
    if (p.zero) { ymin = Math.min(ymin, 0); ymax = Math.max(ymax, 0) }
    const xmin = Math.min(...xs), xmax = Math.max(...xs)
    const padL = 56, padB = 18
    const X = (x: number) => padL + ((x - xmin) / (xmax - xmin || 1)) * (w - padL - 6)
    const Y = (y: number) => 6 + (1 - (y - ymin) / (ymax - ymin || 1)) * (h - padB - 10)
    const css = getComputedStyle(document.documentElement)
    ctx.strokeStyle = css.getPropertyValue('--line')
    ctx.fillStyle = css.getPropertyValue('--muted')
    ctx.font = '11px system-ui'
    for (let i = 0; i <= 4; i++) {
      const y = ymin + ((ymax - ymin) * i) / 4
      ctx.beginPath(); ctx.moveTo(padL, Y(y)); ctx.lineTo(w, Y(y)); ctx.stroke()
      ctx.fillText(fmtNum(y), 2, Y(y) + 4)
    }
    ctx.fillText(new Date(xmin).toLocaleDateString(), padL, h - 4)
    const lbl = new Date(xmax).toLocaleDateString()
    ctx.fillText(lbl, w - ctx.measureText(lbl).width - 4, h - 4)
    for (const s of p.series) {
      ctx.strokeStyle = s.color
      ctx.lineWidth = 1.6
      ctx.beginPath()
      s.points.forEach(([x, y], i) => (i ? ctx.lineTo(X(x), Y(y)) : ctx.moveTo(X(x), Y(y))))
      ctx.stroke()
    }
  }, [p.series, p.height])
  return <canvas ref={ref} style={`width:100%;height:${p.height || 220}px;display:block`} />
}

export function KV(p: { items: [string, ComponentChildren][] }) {
  return (
    <div class="grid g4">
      {p.items.map(([k, v]) => <div class="stat"><span class="v">{v}</span><span class="k">{k}</span></div>)}
    </div>
  )
}

export function Confirm(p: { text: string; phrase?: string; onConfirm: () => any; onClose: () => void }) {
  const [typed, setTyped] = useState('')
  return (
    <Modal title="Please confirm" onClose={p.onClose}>
      <p>{p.text}</p>
      {p.phrase && <Field label={`Type exactly: ${p.phrase}`}><input value={typed} onInput={(e) => setTyped((e.target as HTMLInputElement).value)} /></Field>}
      <div class="row" style="margin-top:12px">
        <span class="sp" />
        <Btn onClick={p.onClose}>Cancel</Btn>
        <Btn kind="danger" disabled={!!p.phrase && typed !== p.phrase} onClick={async () => { await p.onConfirm(); p.onClose() }}>Confirm</Btn>
      </div>
    </Modal>
  )
}
