import { useEffect, useState } from 'preact/hooks'
import { api, del, get, live, post } from '../api'
import { Badge, Btn, Card, Confirm, Empty, ErrorBox, Field, KV, LineChart, StatusBadge, Tabs, useLoad } from '../components/ui'
import { errText, fmtNum, fmtPct, fmtTime, prefs, toast, useStore } from '../store'

export function Portfolio() {
  const pr = useStore(prefs)
  const accts = useLoad(() => get<any[]>('/accounts'))
  const aid = pr.accountId ?? accts.data?.[0]?.id ?? null
  const [tab, setTab] = useState<'positions' | 'ticket' | 'orders' | 'fills' | 'proposals' | 'journal' | 'analytics'>('positions')
  const [n, setN] = useState(0)
  const bump = () => { setN((x) => x + 1); accts.reload() }
  useEffect(() => live.on((e) => { if (e.topic === 'order' && e.data?.account_id === aid) setN((x) => x + 1) }), [aid])
  return (
    <>
      <Card title="Accounts" actions={<NewAccount onDone={(a) => { prefs.set((x) => ({ ...x, accountId: a.id })); accts.reload() }} />}>
        <ErrorBox error={accts.error} />
        <div class="row" style="flex-wrap:wrap">{(accts.data || []).map((a) => (
          <Btn on={a.id === aid} onClick={() => prefs.set((x) => ({ ...x, accountId: a.id }))}>{a.name} <Badge kind={a.label} /> {fmtNum(a.equity)}</Btn>
        ))}</div>
      </Card>
      {aid && <AccountSummary key={`${aid}-${n}`} aid={aid} onChange={bump} />}
      {aid && <>
        <Tabs tabs={[['positions', 'Positions'], ['ticket', 'Order ticket'], ['orders', 'Orders'], ['fills', 'Fills'], ['proposals', 'Proposals'], ['journal', 'Journal'], ['analytics', 'Analytics']]} value={tab} onChange={setTab} />
        {tab === 'positions' && <Positions key={n} aid={aid} onChange={bump} />}
        {tab === 'ticket' && <OrderTicket aid={aid} onDone={bump} />}
        {tab === 'orders' && <Orders key={n} aid={aid} onChange={bump} />}
        {tab === 'fills' && <Fills key={n} aid={aid} />}
        {tab === 'proposals' && <Proposals onChange={bump} />}
        {tab === 'journal' && <Journal aid={aid} />}
        {tab === 'analytics' && <Analytics key={n} aid={aid} />}
      </>}
    </>
  )
}

function NewAccount(p: { onDone: (a: any) => void }) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('Paper 2')
  const [cash, setCash] = useState(10000)
  if (!open) return <Btn sm onClick={() => setOpen(true)}>+ Paper account</Btn>
  return (
    <span class="row">
      <input value={name} style="width:120px" onInput={(e) => setName((e.target as HTMLInputElement).value)} />
      <input type="number" style="width:100px" value={cash} onInput={(e) => setCash(Number((e.target as HTMLInputElement).value))} />
      <Btn sm kind="primary" onClick={async () => { try { p.onDone(await post('/accounts', { name, initial_cash: cash })); setOpen(false) } catch (e) { toast(errText(e), 'error') } }}>Create</Btn>
      <Btn sm kind="ghost" onClick={() => setOpen(false)}>✕</Btn>
    </span>
  )
}

function AccountSummary(p: { aid: number; onChange: () => void }) {
  const s = useLoad(() => get(`/accounts/${p.aid}/summary`), [p.aid])
  const eq = useLoad(() => get<any[]>(`/accounts/${p.aid}/equity`), [p.aid])
  const [confirm, setConfirm] = useState<null | 'reset' | 'flatten'>(null)
  if (s.error) return <Card><ErrorBox error={s.error} /></Card>
  if (!s.data) return null
  const d = s.data
  return (
    <Card title={<>{d.name} <Badge kind={d.label} /> <span class="small muted">{d.currency}</span></>} actions={<>
      <Btn sm onClick={async () => { const r = await post('/orders/cancel_all', { account_id: p.aid }); toast(`${r.cancelled} order(s) cancelled`, 'ok'); p.onChange() }}>Cancel all orders</Btn>
      <Btn sm kind="danger" onClick={() => setConfirm('flatten')}>Close all positions</Btn>
      {d.kind === 'paper' && <Btn sm kind="ghost" onClick={() => setConfirm('reset')}>Reset</Btn>}
      <Btn sm kind="ghost" onClick={async () => {
        const csv = await api<string>('GET', `/accounts/${p.aid}/export.csv`)
        const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' })); a.download = `account-${p.aid}-fills.csv`; a.click()
      }}>Export CSV</Btn>
    </>}>
      {d.stale_prices.length > 0 && <div class="alertbox warn">No current price for {d.stale_prices.join(', ')}: valued at average cost.</div>}
      <KV items={[['Equity', fmtNum(d.equity)], ['Cash', fmtNum(d.cash)], ['Total P&L', <span class={d.total_pnl >= 0 ? 'pos' : 'neg'}>{fmtNum(d.total_pnl)} ({fmtPct(d.total_return_pct)})</span>],
        ['Unrealised', fmtNum(d.unrealized_pnl)], ['Realised', fmtNum(d.realized_pnl)], ['Fees paid', fmtNum(d.fees_paid)],
        ['Exposure', fmtPct(d.exposure_pct, 1).replace('+', '')], ['Open positions', d.open_positions]]} />
      {(eq.data || []).length > 1 && <LineChart height={160} series={[{ name: 'equity', points: eq.data!.map((r) => [r.ts, r.equity] as [number, number]), color: 'var(--accent)' }]} />}
      {confirm === 'reset' && <Confirm text={`Reset ${d.name}? Positions and history of open orders are cleared and cash returns to ${fmtNum(d.initial_cash)}.`} phrase="RESET"
        onClose={() => setConfirm(null)} onConfirm={async () => { await post(`/accounts/${p.aid}/reset`); toast('Account reset', 'ok'); p.onChange() }} />}
      {confirm === 'flatten' && <Confirm text="Send market orders to close every open position in this account? Orders still pass the risk engine." phrase="CLOSE ALL"
        onClose={() => setConfirm(null)} onConfirm={async () => { const r = await post(`/accounts/${p.aid}/flatten`); toast(`${r.length} closing order(s) sent`, 'ok'); p.onChange() }} />}
    </Card>
  )
}

function Positions(p: { aid: number; onChange: () => void }) {
  const s = useLoad(() => get(`/accounts/${p.aid}/summary`), [p.aid])
  const pos: any[] = s.data?.positions || []
  return (
    <Card>
      <div class="tablewrap"><table class="t">
        <thead><tr><th>Instrument</th><th class="right">Qty</th><th class="right">Avg price</th><th class="right">Last</th><th>Price data</th><th class="right">Value</th><th class="right">Unrealised</th><th class="right">%</th><th>Layers</th><th /></tr></thead>
        <tbody>{pos.map((x) => <tr>
          <td class="mono">{x.instrument_id}</td><td class="right">{fmtNum(x.qty, 6)}</td><td class="right">{fmtNum(x.avg_price)}</td><td class="right">{fmtNum(x.price)}</td>
          <td><StatusBadge status={x.price_status} /></td><td class="right">{fmtNum(x.market_value)}</td>
          <td class={`right ${x.unrealized_pnl >= 0 ? 'pos' : 'neg'}`}>{fmtNum(x.unrealized_pnl)}</td><td class="right">{fmtPct(x.unrealized_pnl_pct)}</td><td>{x.layers}</td>
          <td><Btn sm onClick={async () => {
            const r = await post('/orders', { account_id: p.aid, instrument_id: x.instrument_id, side: x.qty > 0 ? 'sell' : 'buy', qty: Math.abs(x.qty), type: 'market', reduce_only: true, reason: 'closed from Portfolio' })
            toast(r.order.status === 'rejected' ? `Rejected: ${r.order.reason}` : `Close order ${r.order.status}`, r.order.status === 'rejected' ? 'error' : 'ok'); p.onChange()
          }}>Close</Btn></td></tr>)}</tbody>
      </table></div>
      {s.data && pos.length === 0 && <Empty>No open positions.</Empty>}
    </Card>
  )
}

function OrderTicket(p: { aid: number; onDone: () => void }) {
  const [o, setO] = useState<any>({ instrument_id: 'OKX:BTC-USDT', side: 'buy', type: 'market', qty: 0.001, limit_price: '', stop_price: '', stop_loss: '', take_profit: '', tif: 'gtc' })
  const [quote, setQuote] = useState<any>(null)
  const [res, setRes] = useState<any>(null)
  const [err, setErr] = useState<unknown>(null)
  const set = (k: string) => (e: Event) => setO({ ...o, [k]: (e.target as HTMLInputElement).value })
  const numOrNull = (v: any) => (v === '' || v === null || v === undefined ? null : Number(v))
  const refreshQuote = async () => { try { setQuote(await get('/quote', { instrument: o.instrument_id })) } catch (e) { setQuote(null); setErr(e) } }
  const needsLimit = o.type === 'limit' || o.type === 'stop_limit', needsStop = o.type === 'stop' || o.type === 'stop_limit'
  return (
    <Card title="Paper order ticket">
      <div class="row">
        <Field label="Instrument"><input value={o.instrument_id} onInput={set('instrument_id')} onBlur={refreshQuote} /></Field>
        <Field label="Side"><select value={o.side} onChange={set('side')}><option value="buy">Buy</option><option value="sell">Sell</option></select></Field>
        <Field label="Type"><select value={o.type} onChange={set('type')}><option value="market">Market</option><option value="limit">Limit</option><option value="stop">Stop</option><option value="stop_limit">Stop-limit</option></select></Field>
        <Field label="Quantity"><input type="number" step="any" style="width:110px" value={o.qty} onInput={set('qty')} /></Field>
        {needsLimit && <Field label="Limit price"><input type="number" step="any" style="width:110px" value={o.limit_price} onInput={set('limit_price')} /></Field>}
        {needsStop && <Field label="Stop trigger"><input type="number" step="any" style="width:110px" value={o.stop_price} onInput={set('stop_price')} /></Field>}
        <Field label="Time in force"><select value={o.tif} onChange={set('tif')}><option value="gtc">Good till cancelled</option><option value="day">Day</option><option value="ioc">Immediate or cancel</option></select></Field>
      </div>
      <div class="row">
        <Field label="Protective stop-loss price (optional)" hint="used by the risk engine to size risk"><input type="number" step="any" style="width:140px" value={o.stop_loss} onInput={set('stop_loss')} /></Field>
        <Field label="Take-profit price (optional)"><input type="number" step="any" style="width:140px" value={o.take_profit} onInput={set('take_profit')} /></Field>
        {quote && <div class="small">Last <b>{fmtNum(quote.price)}</b> <StatusBadge status={quote.status} source={quote.source} />{quote.bid ? ` bid ${fmtNum(quote.bid)} / ask ${fmtNum(quote.ask)}` : ''}
          {' '}≈ notional {fmtNum(Number(o.qty) * quote.price)}</div>}
        <span class="sp" />
        <Btn onClick={refreshQuote}>Get price</Btn>
        <Btn kind={o.side === 'buy' ? 'primary' : 'danger'} onClick={async () => {
          try {
            setErr(null)
            const r = await post('/orders', { account_id: p.aid, instrument_id: o.instrument_id.trim(), side: o.side, type: o.type, qty: Number(o.qty), tif: o.tif,
              limit_price: needsLimit ? numOrNull(o.limit_price) : null, stop_price: needsStop ? numOrNull(o.stop_price) : null,
              stop_loss: numOrNull(o.stop_loss), take_profit: numOrNull(o.take_profit) })
            setRes(r); p.onDone()
          } catch (e) { setErr(e); setRes(null) }
        }}>{o.side === 'buy' ? 'Buy' : 'Sell'} (paper)</Btn>
      </div>
      <ErrorBox error={err} />
      {res && <div class={`alertbox ${res.order.status === 'rejected' ? 'error' : 'ok'}`} style="margin-top:8px">
        Order #{res.order.id}: <b>{res.order.status}</b>{res.order.avg_fill_price ? ` at ${fmtNum(res.order.avg_fill_price)}` : ''}{res.order.status === 'rejected' && ` — ${res.order.reason}`}
        {res.decision?.checks?.length > 0 && <details><summary class="small">Risk checks ({res.decision.checks.filter((c: any) => c.passed).length}/{res.decision.checks.length} passed)</summary>
          <table class="t small"><tbody>{res.decision.checks.map((c: any) => <tr><td>{c.passed ? '✔' : '✖'}</td><td class="mono">{c.rule}</td><td>{c.message}</td></tr>)}</tbody></table></details>}
      </div>}
    </Card>
  )
}

function Orders(p: { aid: number; onChange: () => void }) {
  const [openOnly, setOpenOnly] = useState(true)
  const orders = useLoad(() => get<any[]>(`/accounts/${p.aid}/orders`, { open_only: openOnly }), [p.aid, openOnly])
  return (
    <Card actions={<label class="row small"><input type="checkbox" checked={openOnly} onChange={(e) => setOpenOnly((e.target as HTMLInputElement).checked)} /> open only</label>} title="Orders">
      <ErrorBox error={orders.error} />
      <div class="tablewrap"><table class="t">
        <thead><tr><th>#</th><th>Time</th><th>Instrument</th><th>Side</th><th>Type</th><th class="right">Qty</th><th class="right">Limit</th><th class="right">Stop</th><th>Status</th><th class="right">Filled</th><th>Origin</th><th>Note</th><th /></tr></thead>
        <tbody>{(orders.data || []).map((o) => <tr>
          <td>{o.id}</td><td class="small">{fmtTime(o.created_at)}</td><td class="mono small">{o.instrument_id}</td><td class={o.side === 'buy' ? 'pos' : 'neg'}>{o.side}</td><td>{o.type}</td>
          <td class="right">{fmtNum(o.qty, 6)}</td><td class="right">{fmtNum(o.limit_price)}</td><td class="right">{fmtNum(o.stop_price)}</td>
          <td><Badge kind={o.status === 'rejected' ? 'error' : o.status === 'filled' ? 'ok' : 'HISTORICAL'}>{o.status}</Badge></td>
          <td class="right">{fmtNum(o.filled_qty, 6)}{o.avg_fill_price ? ` @ ${fmtNum(o.avg_fill_price)}` : ''}</td><td class="small">{o.origin}{o.bot_id ? ` (bot ${o.bot_id})` : ''}</td>
          <td class="small" title={o.reason}>{(o.reason || '').slice(0, 60)}</td>
          <td>{['new', 'accepted', 'partially_filled'].includes(o.status) && <Btn sm onClick={async () => { try { await del(`/orders/${o.id}`); orders.reload(); p.onChange() } catch (e) { toast(errText(e), 'error') } }}>Cancel</Btn>}</td>
        </tr>)}</tbody>
      </table></div>
      {orders.data?.length === 0 && <Empty>No orders.</Empty>}
    </Card>
  )
}

function Fills(p: { aid: number }) {
  const fills = useLoad(() => get<any[]>(`/accounts/${p.aid}/fills`), [p.aid])
  return (
    <Card title="Fills (executions)">
      <div class="tablewrap"><table class="t">
        <thead><tr><th>Time</th><th>Instrument</th><th>Side</th><th class="right">Qty</th><th class="right">Price</th><th class="right">Fee</th><th class="right">Realised P&L</th><th>Order</th></tr></thead>
        <tbody>{(fills.data || []).map((f) => <tr><td class="small">{fmtTime(f.ts)}</td><td class="mono small">{f.instrument_id}</td><td class={f.side === 'buy' ? 'pos' : 'neg'}>{f.side}</td>
          <td class="right">{fmtNum(f.qty, 6)}</td><td class="right">{fmtNum(f.price)}</td><td class="right">{fmtNum(f.fee)}</td>
          <td class={`right ${f.realized_pnl >= 0 ? 'pos' : 'neg'}`}>{f.realized_pnl ? fmtNum(f.realized_pnl) : ''}</td><td>#{f.order_id}</td></tr>)}</tbody>
      </table></div>
      {fills.data?.length === 0 && <Empty>No fills yet.</Empty>}
    </Card>
  )
}

function Proposals(p: { onChange: () => void }) {
  const [status, setStatus] = useState('pending')
  const props = useLoad(() => get<any[]>('/proposals', { status }), [status])
  return (
    <Card title="Order proposals from AI and automation" actions={<select value={status} onChange={(e) => setStatus((e.target as HTMLSelectElement).value)}>
      {['pending', 'approved', 'rejected', 'expired'].map((s) => <option>{s}</option>)}</select>}>
      <p class="small muted">AI agents and MCP clients can only <b>propose</b> orders. Nothing is placed until a person approves it here, and approved orders still pass the risk engine.</p>
      {(props.data || []).map((x) => <div class="card" style="background:var(--panel2)">
        <div class="row"><b>#{x.id}</b><Badge kind="HISTORICAL">{x.origin}</Badge><span class="mono">{x.payload.side} {x.payload.qty} {x.payload.instrument_id}</span>
          <span class="small muted">{x.payload.type || 'market'} · {fmtTime(x.created_at)}{x.expires_at ? ` · expires ${fmtTime(x.expires_at)}` : ''}</span><span class="sp" />
          {x.status === 'pending' && <>
            <Btn sm kind="primary" onClick={async () => { try { const r = await post(`/proposals/${x.id}/decide`, { approve: true }); toast(`Approved: order ${r.order?.status}`, 'ok'); props.reload(); p.onChange() } catch (e) { toast(errText(e), 'error') } }}>Approve</Btn>
            <Btn sm onClick={async () => { await post(`/proposals/${x.id}/decide`, { approve: false }); props.reload() }}>Reject</Btn></>}
        </div>
        {x.rationale && <div class="small" style="margin-top:4px">Rationale (AI-generated, unverified): {x.rationale}</div>}
      </div>)}
      {props.data?.length === 0 && <Empty>No {status} proposals.</Empty>}
    </Card>
  )
}

function Journal(p: { aid: number }) {
  const [tag, setTag] = useState('')
  const j = useLoad(() => get<any[]>('/journal', { tag: tag || undefined }), [tag])
  const [f, setF] = useState({ title: '', body: '', instrument_id: '', tags: '' })
  return (
    <Card title="Trading journal">
      <div class="row">
        <input placeholder="Title" value={f.title} onInput={(e) => setF({ ...f, title: (e.target as HTMLInputElement).value })} />
        <input placeholder="Instrument (optional)" value={f.instrument_id} onInput={(e) => setF({ ...f, instrument_id: (e.target as HTMLInputElement).value })} />
        <input placeholder="tags, comma separated" value={f.tags} onInput={(e) => setF({ ...f, tags: (e.target as HTMLInputElement).value })} />
      </div>
      <textarea style="width:100%;min-height:80px;margin-top:6px" placeholder="Why did you take this trade? What did you learn?" value={f.body} onInput={(e) => setF({ ...f, body: (e.target as HTMLTextAreaElement).value })} />
      <div class="row"><input placeholder="Filter by tag" value={tag} onInput={(e) => setTag((e.target as HTMLInputElement).value)} /><span class="sp" />
        <Btn kind="primary" onClick={async () => {
          try { await post('/journal', { ...f, account_id: p.aid, instrument_id: f.instrument_id || null, tags: f.tags.split(',').map((t) => t.trim()).filter(Boolean) }); setF({ title: '', body: '', instrument_id: '', tags: '' }); j.reload() } catch (e) { toast(errText(e), 'error') }
        }}>Add entry</Btn></div>
      {(j.data || []).map((e) => <div style="border-top:1px solid var(--line);padding:8px 0">
        <div class="row"><b>{e.title}</b><span class="small muted">{fmtTime(e.ts)}{e.instrument_id ? ` · ${e.instrument_id}` : ''}</span>{e.tags.map((t: string) => <Badge kind="HISTORICAL">{t}</Badge>)}<span class="sp" />
          <Btn sm kind="ghost" onClick={async () => { await del(`/journal/${e.id}`); j.reload() }}>Delete</Btn></div>
        {e.body && <div class="small" style="white-space:pre-wrap">{e.body}</div>}
      </div>)}
      {j.data?.length === 0 && <Empty>No entries.</Empty>}
    </Card>
  )
}

function Analytics(p: { aid: number }) {
  const a = useLoad(() => get(`/accounts/${p.aid}/analytics`), [p.aid])
  if (a.error) return <Card><ErrorBox error={a.error} /></Card>
  if (!a.data) return <Card><Empty>Loading…</Empty></Card>
  const d = a.data
  return (
    <Card title="Portfolio analytics">
      {d.points > 2 ? <KV items={[['Max drawdown', fmtPct(-d.max_drawdown_pct)], ['Best step', fmtPct(d.best_step_pct)], ['Worst step', fmtPct(d.worst_step_pct)], ['Equity snapshots', d.points]]} />
        : <Empty>Not enough equity history yet (snapshots are taken while the host runs).</Empty>}
      <h3>Allocation</h3>
      {d.allocation.length ? <table class="t"><tbody>{d.allocation.map((x: any) => <tr><td class="mono">{x.instrument_id}</td><td style="width:60%"><div style={`background:var(--accent);height:8px;width:${x.weight_pct}%;border-radius:4px`} /></td><td class="right">{fmtNum(x.weight_pct, 1)}%</td></tr>)}</tbody></table> : <Empty>No positions.</Empty>}
      {d.correlation && <>
        <h3>Correlation of daily returns (last ~120 days)</h3>
        <div class="tablewrap"><table class="t matrix"><thead><tr><th />{d.correlation.instruments.map((i: string) => <th class="small">{i.split(':')[1]}</th>)}</tr></thead>
          <tbody>{d.correlation.matrix.map((row: number[], r: number) => <tr><th class="small">{d.correlation.instruments[r].split(':')[1]}</th>
            {row.map((v) => <td style={`background:rgba(${v >= 0 ? '80,160,255' : '255,90,90'},${Math.abs(v) * 0.5})`}>{v.toFixed(2)}</td>)}</tr>)}</tbody></table></div>
      </>}
    </Card>
  )
}
