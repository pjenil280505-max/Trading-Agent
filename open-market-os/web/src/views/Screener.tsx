import { useState } from 'preact/hooks'
import { api, del, get, post } from '../api'
import { Btn, Card, Empty, ErrorBox, Field, StatusBadge, useLoad } from '../components/ui'
import { errText, fmtNum, fmtPct, toast } from '../store'

type Rule = { field?: string; cmp?: string; value?: number; expr?: string }
const PRESETS: Record<string, { op: 'and' | 'or'; rules: Rule[] }> = {
  'Oversold in uptrend': { op: 'and', rules: [{ field: 'rsi14', cmp: '<', value: 35 }, { field: 'dist_sma200_pct', cmp: '>', value: 0 }] },
  'Momentum leaders': { op: 'and', rules: [{ field: 'change_pct_20', cmp: '>', value: 5 }, { field: 'adx14', cmp: '>', value: 20 }] },
  'Volume spike': { op: 'and', rules: [{ field: 'rel_volume', cmp: '>', value: 2 }] },
  'Near 52-bar high': { op: 'and', rules: [{ field: 'high_52w_pct', cmp: '>', value: -3 }] },
  'Custom formula': { op: 'and', rules: [{ expr: 'crossover(ema(close, 20), ema(close, 50))' }] },
}

export function Screener() {
  const meta = useLoad(() => get('/system/meta'))
  const wls = useLoad(() => get<any[]>('/watchlists'))
  const saved = useLoad(() => get<any[]>('/screeners'))
  const [wl, setWl] = useState<number | null>(null)
  const [tf, setTf] = useState('1d')
  const [op, setOp] = useState<'and' | 'or'>('and')
  const [rules, setRules] = useState<Rule[]>(PRESETS['Oversold in uptrend'].rules)
  const [res, setRes] = useState<any>(null)
  const [err, setErr] = useState<unknown>(null)
  const [name, setName] = useState('')
  const fields: Record<string, string> = meta.data?.screener_fields || {}
  const wlId = wl || wls.data?.[0]?.id
  const def = () => ({ universe: { watchlist_id: wlId }, timeframe: tf, filter: { op, rules }, sort: { field: 'change_pct', desc: true } })

  return (
    <>
      <Card title="Build a screen" actions={<select onChange={(e) => { const pr = PRESETS[(e.target as HTMLSelectElement).value]; if (pr) { setOp(pr.op); setRules(pr.rules) } }}>
        <option>Presets…</option>{Object.keys(PRESETS).map((k) => <option>{k}</option>)}</select>}>
        <div class="row">
          <Field label="Universe (watchlist)"><select value={wlId} onChange={(e) => setWl(Number((e.target as HTMLSelectElement).value))}>
            {(wls.data || []).map((w) => <option value={w.id}>{w.name} ({w.items.length})</option>)}</select></Field>
          <Field label="Timeframe"><select value={tf} onChange={(e) => setTf((e.target as HTMLSelectElement).value)}>{['1h', '4h', '1d', '1w'].map((t) => <option>{t}</option>)}</select></Field>
          <Field label="Match"><select value={op} onChange={(e) => setOp((e.target as HTMLSelectElement).value as 'and')}><option value="and">ALL rules (AND)</option><option value="or">ANY rule (OR)</option></select></Field>
        </div>
        <div class="col" style="margin-top:10px">
          {rules.map((r, i) => (
            <div class="row">
              {r.expr !== undefined ? (
                <input style="flex:1" class="mono" value={r.expr} onInput={(e) => setRules(rules.map((x, j) => j === i ? { expr: (e.target as HTMLInputElement).value } : x))} />
              ) : <>
                <select value={r.field} onChange={(e) => setRules(rules.map((x, j) => j === i ? { ...x, field: (e.target as HTMLSelectElement).value } : x))}>
                  {Object.entries(fields).map(([k, v]) => <option value={k} title={v}>{k}</option>)}</select>
                <select value={r.cmp} onChange={(e) => setRules(rules.map((x, j) => j === i ? { ...x, cmp: (e.target as HTMLSelectElement).value } : x))}>
                  {['<', '<=', '>', '>=', '==', '!='].map((c) => <option>{c}</option>)}</select>
                <input type="number" step="any" style="width:110px" value={r.value} onInput={(e) => setRules(rules.map((x, j) => j === i ? { ...x, value: Number((e.target as HTMLInputElement).value) } : x))} />
                <span class="small muted">{fields[r.field || '']}</span>
              </>}
              <Btn sm kind="ghost" onClick={() => setRules(rules.filter((_, j) => j !== i))}>✕</Btn>
            </div>
          ))}
          <div class="row">
            <Btn sm onClick={() => setRules([...rules, { field: 'rsi14', cmp: '<', value: 30 }])}>+ Rule</Btn>
            <Btn sm onClick={() => setRules([...rules, { expr: 'close > sma(close, 50)' }])}>+ Formula (OMS Script)</Btn>
            <span class="sp" />
            <input placeholder="Save as…" value={name} onInput={(e) => setName((e.target as HTMLInputElement).value)} />
            <Btn sm onClick={async () => { try { await post('/screeners', { name, definition: def() }); saved.reload(); toast('Saved', 'ok') } catch (e) { toast(errText(e), 'error') } }}>Save</Btn>
            <Btn kind="primary" onClick={async () => { try { setErr(null); setRes(await post('/screener/run', { definition: def() })) } catch (e) { setErr(e) } }}>Run screen</Btn>
          </div>
        </div>
      </Card>
      <ErrorBox error={err} />
      {res && (
        <Card title={`Results: ${res.matched} of ${res.scanned}`} actions={<Btn sm onClick={async () => {
          const csv = await api<string>('POST', '/screener/export', { definition: def() })
          const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' })); a.download = 'screener.csv'; a.click()
        }}>Export CSV</Btn>}>
          <div class="small muted">{res.coverage_note}</div>
          <div class="tablewrap"><table class="t">
            <thead><tr><th>Instrument</th><th class="right">Price</th><th class="right">Chg</th><th class="right">20-bar</th><th class="right">RSI14</th><th class="right">ATR%</th><th class="right">Rel vol</th><th>Data</th></tr></thead>
            <tbody>{res.results.map((r: any) => <tr><td class="mono">{r.instrument_id}</td><td class="right">{fmtNum(r.price)}</td>
              <td class={`right ${r.change_pct >= 0 ? 'pos' : 'neg'}`}>{fmtPct(r.change_pct)}</td><td class="right">{fmtPct(r.change_pct_20)}</td>
              <td class="right">{fmtNum(r.rsi14, 1)}</td><td class="right">{fmtNum(r.atr_pct)}</td><td class="right">{fmtNum(r.rel_volume)}</td><td><StatusBadge status={r.data_status} /></td></tr>)}</tbody>
          </table></div>
          {res.results.length === 0 && <Empty>No instrument matched.</Empty>}
          {Object.keys(res.errors).length > 0 && <div class="small neg">Could not load: {Object.entries(res.errors).map(([k, v]) => `${k} (${v})`).join('; ')}</div>}
        </Card>
      )}
      <Card title="Saved screens">
        {(saved.data || []).map((s) => <div class="row"><b>{s.name}</b><span class="small muted">{s.schedule_minutes ? `every ${s.schedule_minutes} min` : 'manual'}</span><span class="sp" />
          <Btn sm onClick={async () => setRes(await post(`/screeners/${s.id}/run`))}>Run</Btn>
          <Btn sm onClick={async () => { await post(`/screeners/${s.id}/duplicate`, { name: `${s.name} copy` }); saved.reload() }}>Duplicate</Btn>
          <Btn sm kind="ghost" onClick={async () => { await del(`/screeners/${s.id}`); saved.reload() }}>Delete</Btn></div>)}
        {(saved.data || []).length === 0 && <Empty>None saved.</Empty>}
      </Card>
    </>
  )
}
