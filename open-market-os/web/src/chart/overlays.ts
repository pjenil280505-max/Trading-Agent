// Custom drawing tools registered on top of KLineChart's built-ins.
// Every overlay stores time/price points, so drawings stay anchored across timeframe, zoom and scale changes.
import { registerOverlay, type OverlayTemplate } from 'klinecharts'

type Pt = { x: number; y: number }
const C = { up: '#26a69a', down: '#ef5350', line: '#3d8bfd', text: '#e6e9f0' }

function rectPoly(a: Pt, b: Pt) {
  return [{ x: a.x, y: a.y }, { x: b.x, y: a.y }, { x: b.x, y: b.y }, { x: a.x, y: b.y }]
}

const rect: OverlayTemplate = {
  name: 'rect', totalStep: 3, needDefaultPointFigure: true, needDefaultXAxisFigure: true, needDefaultYAxisFigure: true,
  createPointFigures: ({ coordinates }) => coordinates.length < 2 ? [] : [
    { type: 'polygon', attrs: { coordinates: rectPoly(coordinates[0], coordinates[1]) }, styles: { style: 'stroke_fill' } }],
}

const circle: OverlayTemplate = {
  name: 'circle', totalStep: 3, needDefaultPointFigure: true,
  createPointFigures: ({ coordinates }) => {
    if (coordinates.length < 2) return []
    const [a, b] = coordinates
    return [{ type: 'circle', attrs: { x: a.x, y: a.y, r: Math.hypot(b.x - a.x, b.y - a.y) }, styles: { style: 'stroke_fill' } }]
  },
}

const triangle: OverlayTemplate = {
  name: 'triangle', totalStep: 4, needDefaultPointFigure: true,
  createPointFigures: ({ coordinates }) => coordinates.length < 2 ? [] : [
    { type: coordinates.length === 3 ? 'polygon' : 'line', attrs: { coordinates }, styles: { style: 'stroke_fill' } }],
}

const arrow: OverlayTemplate = {
  name: 'arrow', totalStep: 3, needDefaultPointFigure: true,
  createPointFigures: ({ coordinates }) => {
    if (coordinates.length < 2) return []
    const [a, b] = coordinates
    const ang = Math.atan2(b.y - a.y, b.x - a.x), L = 12
    const p1 = { x: b.x - L * Math.cos(ang - 0.45), y: b.y - L * Math.sin(ang - 0.45) }
    const p2 = { x: b.x - L * Math.cos(ang + 0.45), y: b.y - L * Math.sin(ang + 0.45) }
    return [{ type: 'line', attrs: { coordinates: [a, b] } }, { type: 'polygon', attrs: { coordinates: [b, p1, p2] }, styles: { style: 'fill' } }]
  },
}

const textNote: OverlayTemplate<{ text?: string }> = {
  name: 'textNote', totalStep: 2, needDefaultPointFigure: true,
  createPointFigures: ({ coordinates, overlay }) => coordinates.length < 1 ? [] : [
    { type: 'text', attrs: { x: coordinates[0].x, y: coordinates[0].y, text: overlay.extendData?.text || 'Note', baseline: 'bottom' },
      styles: { color: C.text, backgroundColor: 'rgba(61,139,253,0.25)', borderRadius: 3, paddingLeft: 4, paddingRight: 4 } }],
}

const measure: OverlayTemplate = {
  name: 'measure', totalStep: 3, needDefaultPointFigure: true,
  createPointFigures: ({ coordinates, overlay }) => {
    if (coordinates.length < 2) return []
    const [a, b] = coordinates
    const [p, q] = overlay.points
    const dv = (q?.value ?? 0) - (p?.value ?? 0)
    const pct = p?.value ? (dv / p.value) * 100 : 0
    const bars = (q?.dataIndex ?? 0) - (p?.dataIndex ?? 0)
    const up = dv >= 0
    return [
      { type: 'polygon', attrs: { coordinates: rectPoly(a, b) }, styles: { style: 'fill', color: up ? 'rgba(38,166,154,0.18)' : 'rgba(239,83,80,0.18)' } },
      { type: 'text', attrs: { x: (a.x + b.x) / 2, y: Math.min(a.y, b.y) - 4, text: `${dv >= 0 ? '+' : ''}${dv.toPrecision(5)} (${pct.toFixed(2)}%) · ${bars} bars`, align: 'center', baseline: 'bottom' },
        styles: { color: up ? C.up : C.down, backgroundColor: 'rgba(0,0,0,0.4)' } },
    ]
  },
}

function position(name: 'longPosition' | 'shortPosition'): OverlayTemplate {
  const long = name === 'longPosition'
  return {
    name, totalStep: 4, needDefaultPointFigure: true,
    // points: [entry, stop, target]
    createPointFigures: ({ coordinates, overlay, bounding }) => {
      if (coordinates.length < 2) return []
      const [e, s] = coordinates
      const t = coordinates[2] ?? { x: s.x, y: e.y - (s.y - e.y) * 2 }
      const right = Math.min(bounding.width, Math.max(e.x, s.x, t.x) + 80)
      const pe = overlay.points[0]?.value ?? 0, ps = overlay.points[1]?.value ?? 0
      const pt = overlay.points[2]?.value ?? pe + (pe - ps) * 2
      const risk = Math.abs(pe - ps), reward = Math.abs(pt - pe)
      const rr = risk ? reward / risk : 0
      const ok = long ? ps < pe && pt > pe : ps > pe && pt < pe
      return [
        { type: 'polygon', attrs: { coordinates: rectPoly({ x: e.x, y: e.y }, { x: right, y: s.y }) }, styles: { style: 'fill', color: 'rgba(239,83,80,0.2)' } },
        { type: 'polygon', attrs: { coordinates: rectPoly({ x: e.x, y: e.y }, { x: right, y: t.y }) }, styles: { style: 'fill', color: 'rgba(38,166,154,0.2)' } },
        { type: 'line', attrs: { coordinates: [{ x: e.x, y: e.y }, { x: right, y: e.y }] } },
        { type: 'text', attrs: { x: right, y: e.y, text: ok ? `${long ? 'Long' : 'Short'} R:R ${rr.toFixed(2)}` : 'stop/target on wrong side', align: 'right', baseline: 'bottom' },
          styles: { color: ok ? C.text : C.down, backgroundColor: 'rgba(0,0,0,0.45)' } },
      ]
    },
  }
}

const fibExtension: OverlayTemplate = {
  name: 'fibExtension', totalStep: 4, needDefaultPointFigure: true,
  createPointFigures: ({ coordinates, overlay, bounding }) => {
    if (coordinates.length < 3) return coordinates.length === 2 ? [{ type: 'line', attrs: { coordinates } }] : []
    const [a, b, c] = overlay.points.map((p) => p.value ?? 0)
    const ratios = [0, 0.618, 1, 1.272, 1.618, 2.618]
    const yOf = (v: number) => {
      const [ca, cb] = [coordinates[0], coordinates[1]]
      const va = overlay.points[0].value ?? 0, vb = overlay.points[1].value ?? 1
      return ca.y + ((v - va) / ((vb - va) || 1)) * (cb.y - ca.y)
    }
    const figs: any[] = [{ type: 'line', attrs: { coordinates: coordinates.slice(0, 3) }, styles: { style: 'dashed' } }]
    for (const r of ratios) {
      const v = c + (b - a) * r
      const y = yOf(v)
      figs.push({ type: 'line', attrs: { coordinates: [{ x: coordinates[2].x, y }, { x: bounding.width, y }] } })
      figs.push({ type: 'text', attrs: { x: coordinates[2].x, y, text: `${(r * 100).toFixed(1)}%  ${v.toPrecision(6)}`, baseline: 'bottom' }, styles: { color: C.text } })
    }
    return figs
  },
}

const regressionChannel: OverlayTemplate = {
  name: 'regressionChannel', totalStep: 3, needDefaultPointFigure: true,
  createPointFigures: ({ chart, overlay, coordinates }) => {
    if (coordinates.length < 2) return []
    const [p, q] = overlay.points
    const i0 = Math.min(p.dataIndex ?? 0, q.dataIndex ?? 0), i1 = Math.max(p.dataIndex ?? 0, q.dataIndex ?? 0)
    const data = chart.getDataList().slice(i0, i1 + 1)
    if (data.length < 3) return [{ type: 'line', attrs: { coordinates } }]
    const n = data.length
    const xs = data.map((_, i) => i), ys = data.map((d) => d.close)
    const mx = xs.reduce((s, x) => s + x, 0) / n, my = ys.reduce((s, y) => s + y, 0) / n
    const slope = xs.reduce((s, x, i) => s + (x - mx) * (ys[i] - my), 0) / (xs.reduce((s, x) => s + (x - mx) ** 2, 0) || 1)
    const icpt = my - slope * mx
    const sd = Math.sqrt(ys.reduce((s, y, i) => s + (y - (icpt + slope * i)) ** 2, 0) / n)
    const line = (off: number) => {
      const pts = chart.convertToPixel([{ dataIndex: i0, value: icpt + off }, { dataIndex: i1, value: icpt + slope * (n - 1) + off }]) as Pt[]
      return { type: 'line', attrs: { coordinates: pts } }
    }
    return [line(0), { ...line(2 * sd), styles: { style: 'dashed' } }, { ...line(-2 * sd), styles: { style: 'dashed' } }]
  },
}

const pitchfork: OverlayTemplate = {
  name: 'pitchfork', totalStep: 4, needDefaultPointFigure: true,
  createPointFigures: ({ coordinates, bounding }) => {
    if (coordinates.length < 3) return coordinates.length === 2 ? [{ type: 'line', attrs: { coordinates } }] : []
    const [a, b, c] = coordinates
    const m = { x: (b.x + c.x) / 2, y: (b.y + c.y) / 2 }
    const dx = m.x - a.x, dy = m.y - a.y
    const ext = (p: Pt) => {
      const k = dx ? (bounding.width - p.x) / dx : 0
      return { x: p.x + dx * Math.max(k, 1), y: p.y + dy * Math.max(k, 1) }
    }
    return [{ type: 'line', attrs: { coordinates: [b, c] } }, { type: 'line', attrs: { coordinates: [a, ext(a)] } },
      { type: 'line', attrs: { coordinates: [b, ext(b)] } }, { type: 'line', attrs: { coordinates: [c, ext(c)] } }]
  },
}

let done = false
export function registerOverlays() {
  if (done) return
  done = true
  ;[rect, circle, triangle, arrow, textNote, measure, position('longPosition'), position('shortPosition'), fibExtension,
    regressionChannel, pitchfork].forEach((o) => registerOverlay(o as OverlayTemplate))
}

export const DRAWING_TOOLS: { name: string; label: string; title: string }[] = [
  { name: 'segment', label: '╱', title: 'Trend line' },
  { name: 'rayLine', label: '↗', title: 'Ray' },
  { name: 'straightLine', label: '⟷', title: 'Extended line' },
  { name: 'horizontalStraightLine', label: '—', title: 'Horizontal line' },
  { name: 'verticalStraightLine', label: '|', title: 'Vertical line' },
  { name: 'priceLine', label: '$', title: 'Price label' },
  { name: 'parallelStraightLine', label: '∥', title: 'Parallel channel' },
  { name: 'priceChannelLine', label: '⫽', title: 'Price channel' },
  { name: 'regressionChannel', label: 'Reg', title: 'Regression channel (±2σ)' },
  { name: 'fibonacciLine', label: 'Fib', title: 'Fibonacci retracement' },
  { name: 'fibExtension', label: 'FibX', title: 'Fibonacci extension (3 points)' },
  { name: 'pitchfork', label: 'Ψ', title: 'Andrews pitchfork (3 points)' },
  { name: 'rect', label: '▭', title: 'Rectangle' },
  { name: 'circle', label: '◯', title: 'Circle' },
  { name: 'triangle', label: '△', title: 'Triangle' },
  { name: 'arrow', label: '➜', title: 'Arrow' },
  { name: 'longPosition', label: 'L', title: 'Long position (entry, stop, target)' },
  { name: 'shortPosition', label: 'S', title: 'Short position (entry, stop, target)' },
  { name: 'measure', label: '⇕', title: 'Measure price/time' },
  { name: 'textNote', label: 'T', title: 'Text note' },
  { name: 'brush', label: '✎', title: 'Brush (freehand)' },
]
