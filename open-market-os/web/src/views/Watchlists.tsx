import { useState } from 'preact/hooks'
import { del, get, post } from '../api'
import { Btn, Card, Empty, ErrorBox, Field, Spark, StatusBadge, useLoad } from '../components/ui'
import { errText, fmtNum, fmtPct, toast } from '../store'

export function Watchlists() {
  const lists = useLoad(() => get<any[]>('/watchlists'))
  const [sel, setSel] = useState<number | null>(null)
  const [name, setName] = useState('')
  const current = (lists.data || []).find((w) => w.id === sel) || lists.data?.[0]
  return (
    <div class="grid" style="grid-template-columns:minmax(200px,260px) 1fr">
      <Card title="Watchlists">
        <ErrorBox error={lists.error} />
        {(lists.data || []).map((w) => <div class="row"><Btn sm on={current?.id === w.id} onClick={() => setSel(w.id)}>{w.name}</Btn><span class="muted small">{w.items.length}</span></div>)}
        <div class="row" style="margin-top:10px">
          <input value={name} placeholder="New list" onInput={(e) => setName((e.target as HTMLInputElement).value)} />
          <Btn sm onClick={async () => { try { await post('/watchlists', { name }); setName(''); lists.reload() } catch (e) { toast(errText(e), 'error') } }}>Add</Btn>
        </div>
      </Card>
      {current ? <List wl={current} onChange={lists.reload} /> : <Card><Empty>Create a watchlist.</Empty></Card>}
    </div>
  )
}

function List(p: { wl: any; onChange: () => void }) {
  const [tf, setTf] = useState('1d')
  const quotes = useLoad(() => get<any[]>(`/watchlists/${p.wl.id}/quotes`, { tf }), [p.wl.id, tf, p.wl.items.length])
  const [q, setQ] = useState('')
  const [found, setFound] = useState<any[]>([])
  const add = async (iid: string) => {
    try { await post(`/watchlists/${p.wl.id}/items`, { instrument_id: iid }); setFound([]); setQ(''); p.onChange() } catch (e) { toast(errText(e), 'error') }
  }
  return (
    <Card title={p.wl.name} actions={<>
      <select value={tf} onChange={(e) => setTf((e.target as HTMLSelectElement).value)}>{['1h', '4h', '1d', '1w'].map((t) => <option>{t}</option>)}</select>
      <Btn sm kind="ghost" onClick={async () => { if (confirm(`Delete watchlist ${p.wl.name}?`)) { await del(`/watchlists/${p.wl.id}`); p.onChange() } }}>Delete list</Btn>
    </>}>
      <div class="row">
        <Field label="Add instrument (search, or type PROVIDER:SYMBOL)">
          <div class="row"><input value={q} onInput={(e) => setQ((e.target as HTMLInputElement).value)} placeholder="e.g. ETH, AAPL, RELIANCE, OKX:SOL-USDT"
            onKeyDown={async (e) => { if (e.key === 'Enter') { if (q.includes(':')) add(q.trim()); else setFound((await get('/instruments/search', { q })).results) } }} />
            <Btn sm onClick={async () => setFound((await get('/instruments/search', { q })).results)}>Search</Btn></div>
        </Field>
      </div>
      {found.length > 0 && <table class="t"><tbody>{found.slice(0, 15).map((r) => (
        <tr><td class="mono">{r.id}</td><td>{r.name}</td><td class="muted">{r.asset_class}</td><td><Btn sm onClick={() => add(r.id)}>Add</Btn></td></tr>
      ))}</tbody></table>}
      <ErrorBox error={quotes.error} />
      <div class="tablewrap" style="margin-top:10px">
        <table class="t">
          <thead><tr><th>Instrument</th><th class="right">Last</th><th class="right">Change</th><th>Trend</th><th>Data</th><th /></tr></thead>
          <tbody>{(quotes.data || []).map((r) => (
            <tr>
              <td class="mono">{r.instrument_id}</td>
              {r.error ? <td colSpan={4} class="neg small">{r.error}</td> : <>
                <td class="right">{fmtNum(r.price, r.price < 10 ? 4 : 2)}</td>
                <td class={`right ${r.change_pct >= 0 ? 'pos' : 'neg'}`}>{fmtPct(r.change_pct)}</td>
                <td><Spark values={r.spark} /></td><td><StatusBadge status={r.status} stale={r.stale} /></td></>}
              <td><Btn sm kind="ghost" onClick={async () => { await del(`/watchlists/${p.wl.id}/items`, { instrument_id: r.instrument_id }); p.onChange() }}>✕</Btn></td>
            </tr>
          ))}</tbody>
        </table>
        {quotes.data?.length === 0 && <Empty>Empty list.</Empty>}
      </div>
    </Card>
  )
}
