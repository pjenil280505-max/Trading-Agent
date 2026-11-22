import { get } from '../api'
import { Badge, Card, Empty, ErrorBox, KV, Spark, StatusBadge, useLoad } from '../components/ui'
import { fmtNum, fmtPct, fmtTime, prefs, useStore } from '../store'

export function Dashboard() {
  const pr = useStore(prefs)
  const wls = useLoad(() => get<any[]>('/watchlists'))
  const first = wls.data?.[0]
  const quotes = useLoad(() => first ? get<any[]>(`/watchlists/${first.id}/quotes`, { tf: '1d' }) : Promise.resolve([]), [first?.id])
  const acct = useLoad(() => pr.accountId ? get(`/accounts/${pr.accountId}/summary`) : Promise.resolve(null), [pr.accountId])
  const bots = useLoad(() => get<any[]>('/bots'))
  const status = useLoad(() => get('/system/status'))
  const notes = useLoad(() => get<any[]>('/notifications'))
  const go = (p: string) => (window as any).omosNavigate(p)
  const halted = status.data?.emergency_stop?.halted

  return (
    <>
      {halted && <div class="alertbox error">EMERGENCY STOP is active: all trading is halted. Clear it in Risk once you have reviewed why.</div>}
      {pr.mode === 'beginner' && (
        <div class="alertbox">
          <b>Welcome.</b> Everything here starts in <b>paper</b> mode with simulated money. Try: open <a href="/charts" onClick={(e) => { e.preventDefault(); go('/charts') }}>Charts</a>,
          then the <a href="/strategy" onClick={(e) => { e.preventDefault(); go('/strategy') }}>Strategy lab</a> to backtest an idea before any bot runs it.
          Every price shows a label: <StatusBadge status="REALTIME" /> <StatusBadge status="DELAYED" /> <StatusBadge status="HISTORICAL" />.
        </div>
      )}
      <div class="grid g2">
        <Card title={<>Market overview {first && <span class="muted small">({first.name})</span>}</>}>
          <ErrorBox error={quotes.error} />
          {quotes.loading && <div class="muted">Loading quotes…</div>}
          <table class="t"><tbody>
            {(quotes.data || []).map((q) => (
              <tr style="cursor:pointer" onClick={() => go('/charts')}>
                <td class="mono">{q.instrument_id}</td>
                {q.error ? <td colSpan={4} class="small neg">{q.error}</td> : <>
                  <td class="right">{fmtNum(q.price, q.price < 10 ? 4 : 2)}</td>
                  <td class={`right ${q.change_pct >= 0 ? 'pos' : 'neg'}`}>{fmtPct(q.change_pct)}</td>
                  <td><Spark values={q.spark} /></td>
                  <td><StatusBadge status={q.status} stale={q.stale} /></td></>}
              </tr>
            ))}
          </tbody></table>
        </Card>
        <Card title={<>Paper account {acct.data && <Badge kind={acct.data.label} />}</>}>
          <ErrorBox error={acct.error} />
          {acct.data ? <KV items={[
            ['Equity', fmtNum(acct.data.equity)], ['Total P&L', <span class={acct.data.total_pnl >= 0 ? 'pos' : 'neg'}>{fmtNum(acct.data.total_pnl)}</span>],
            ['Open positions', acct.data.open_positions], ['Exposure', fmtPct(acct.data.exposure_pct, 1).replace('+', '')],
          ]} /> : <Empty>No account selected.</Empty>}
        </Card>
        <Card title="Bots">
          {(bots.data || []).length === 0 ? <Empty>No bots yet. Create one in <a href="/bots" onClick={(e) => { e.preventDefault(); go('/bots') }}>Bots</a>.</Empty> : (
            <table class="t"><tbody>{bots.data!.map((b) => (
              <tr><td>{b.name}</td><td class="muted">{b.kind}</td><td class="mono small">{b.instrument_id}</td><td><Badge kind={b.state} /></td><td><Badge kind={b.mode === 'paper' ? 'PAPER' : 'LIVE'} /></td></tr>
            ))}</tbody></table>
          )}
        </Card>
        <Card title="Data sources">
          <table class="t"><tbody>{(status.data?.providers || []).map((p: any) => (
            <tr><td>{p.provider}</td><td><Badge kind={p.status === 'unknown' ? 'HISTORICAL' : p.status}>{p.status === 'unknown' ? 'not used yet' : p.status}</Badge></td>
              <td class="small muted">{p.latency_ms ? `${p.latency_ms.toFixed(0)} ms` : ''}</td><td class="small neg">{p.last_error || ''}</td></tr>
          ))}</tbody></table>
          <div class="small muted" style="margin-top:6px">{status.data?.note_24x7}</div>
        </Card>
      </div>
      <Card title="Recent notifications">
        {(notes.data || []).slice(0, 8).map((n) => <div class="row small"><span class="muted">{fmtTime(n.ts)}</span><b>{n.title}</b><span>{n.body}</span></div>)}
        {(notes.data || []).length === 0 && <Empty>Nothing yet.</Empty>}
      </Card>
    </>
  )
}
