import { useState } from 'preact/hooks'
import { get, post } from '../api'
import { Badge, Btn, Card, Empty, ErrorBox, Field, Tabs, useLoad } from '../components/ui'
import { errText, fmtNum, fmtTime, toast } from '../store'

export function Research() {
  const [tab, setTab] = useState<'news' | 'fund' | 'cal'>('news')
  return (
    <>
      <Tabs tabs={[['news', 'News'], ['fund', 'Company fundamentals'], ['cal', 'Economic calendar']]} value={tab} onChange={setTab} />
      {tab === 'news' && <News />}
      {tab === 'fund' && <Fundamentals />}
      {tab === 'cal' && <Calendar />}
    </>
  )
}

function News() {
  const [q, setQ] = useState('')
  const [symbol, setSymbol] = useState('')
  const [topic, setTopic] = useState('')
  const news = useLoad(() => get<any[]>('/news', { q, symbol: symbol || undefined, topic: topic || undefined }), [q, symbol, topic])
  const wls = useLoad(() => get<any[]>('/watchlists'))
  return (
    <Card title="Headlines" actions={<Btn sm onClick={async () => {
      try {
        const syms = (wls.data?.[0]?.items || []).filter((s: string) => s.startsWith('YAHOO:'))
        const r = await post('/news/refresh', { symbols: syms })
        toast(`${r.new} new headline(s)${Object.keys(r.errors).length ? `; ${Object.keys(r.errors).length} feed(s) failed` : ''}`, 'ok')
        news.reload()
      } catch (e) { toast(errText(e), 'error') }
    }}>Refresh feeds</Btn>}>
      <div class="row">
        <input placeholder="Search" value={q} onInput={(e) => setQ((e.target as HTMLInputElement).value)} />
        <input placeholder="Symbol e.g. YAHOO:AAPL" value={symbol} onInput={(e) => setSymbol((e.target as HTMLInputElement).value)} />
        <select value={topic} onChange={(e) => setTopic((e.target as HTMLSelectElement).value)}>
          <option value="">All topics</option>{['earnings', 'macro', 'crypto', 'm&a', 'regulation', 'commodities'].map((t) => <option>{t}</option>)}</select>
      </div>
      <p class="small muted">Sentiment shown is a simple word-count heuristic, not a judgement and not AI. Headlines come from RSS feeds (Yahoo Finance's is unofficial).</p>
      <ErrorBox error={news.error} />
      {(news.data || []).map((n) => (
        <div style="padding:8px 0;border-bottom:1px solid var(--line)">
          <div class="row"><a href={n.url || undefined} target="_blank" rel="noopener noreferrer">{n.title}</a></div>
          <div class="row small muted">{fmtTime(n.ts)} · {n.source} {n.topics.map((t: string) => <Badge kind="HISTORICAL">{t}</Badge>)}
            {n.symbols.map((s: string) => <span class="mono">{s}</span>)}
            <span class={n.sentiment > 0 ? 'pos' : n.sentiment < 0 ? 'neg' : ''} title="lexicon heuristic">sentiment {n.sentiment?.toFixed(2)}</span></div>
        </div>
      ))}
      {(news.data || []).length === 0 && <Empty>No headlines stored yet — press "Refresh feeds".</Empty>}
    </Card>
  )
}

function Fundamentals() {
  const [t, setT] = useState('AAPL')
  const [data, setData] = useState<any>(null)
  const [err, setErr] = useState<unknown>(null)
  const load = async () => { try { setErr(null); setData(await get(`/fundamentals/${encodeURIComponent(t)}`)) } catch (e) { setErr(e); setData(null) } }
  const cols = ['revenue', 'gross_profit', 'operating_income', 'net_income', 'eps_diluted', 'total_assets', 'total_liabilities', 'equity', 'cash', 'long_term_debt', 'operating_cash_flow', 'capex']
  return (
    <Card title="US company fundamentals (SEC EDGAR filings)">
      <div class="row"><Field label="Ticker"><input value={t} onInput={(e) => setT((e.target as HTMLInputElement).value)} onKeyDown={(e) => e.key === 'Enter' && load()} /></Field><Btn kind="primary" onClick={load}>Load</Btn></div>
      <p class="small muted">Values are exactly as reported in 10-K/10-Q filings. The SEC requires a contact e-mail: set it in Settings → Data sources.</p>
      <ErrorBox error={err} />
      {data && <>
        <h3>{data.name} <span class="muted small">CIK {data.cik}</span></h3>
        <div class="tablewrap"><table class="t">
          <thead><tr><th>Item</th>{data.annual.map((r: any) => <th class="right">FY {r.fiscal_year} ({r.period_end})</th>)}</tr></thead>
          <tbody>{cols.map((c) => <tr><td>{c.replace(/_/g, ' ')}</td>{data.annual.map((r: any) => <td class="right">{r[c] === undefined ? '—' : fmtNum(r[c], c === 'eps_diluted' ? 2 : 0)}</td>)}</tr>)}</tbody>
        </table></div>
        <h3>Ratios</h3>
        <table class="t"><thead><tr><th>Period</th><th>Gross margin</th><th>Op. margin</th><th>Net margin</th><th>ROE</th><th>Debt/Equity</th><th>Rev growth</th><th>Free cash flow</th></tr></thead>
          <tbody>{data.ratios.map((r: any) => <tr><td>{r.period_end}</td>{['gross_margin', 'operating_margin', 'net_margin', 'roe', 'debt_to_equity', 'revenue_growth'].map((k) => <td>{r[k] === null ? '—' : (r[k] * (k === 'debt_to_equity' ? 1 : 100)).toFixed(1) + (k === 'debt_to_equity' ? '' : '%')}</td>)}<td>{fmtNum(r.free_cash_flow, 0)}</td></tr>)}</tbody></table>
      </>}
    </Card>
  )
}

function Calendar() {
  const cal = useLoad(() => get('/calendar'))
  return (
    <Card title="US economic indicators (FRED)">
      <ErrorBox error={cal.error} />
      {cal.loading && <div class="muted">Loading from FRED…</div>}
      {cal.data && <>
        <table class="t"><thead><tr><th>Event</th><th>Importance</th><th>Period</th><th class="right">Actual</th><th class="right">Previous</th><th>Forecast</th></tr></thead>
          <tbody>{cal.data.events.map((e: any) => <tr><td>{e.event}</td><td><Badge kind={e.importance === 'high' ? 'error' : 'HISTORICAL'}>{e.importance}</Badge></td>
            <td>{e.period || (e.error ? <span class="neg small" title={e.error}>unavailable: {e.error.slice(0, 80)}</span> : '—')}</td><td class="right">{fmtNum(e.actual, 2)}</td><td class="right">{fmtNum(e.previous, 2)}</td>
            <td class="small muted" title={e.forecast_note}>not available</td></tr>)}</tbody></table>
        <div class="alertbox" style="margin-top:10px">{cal.data.schedule.available
          ? <>Upcoming releases: {cal.data.schedule.upcoming.slice(0, 10).map((d: any) => `${d.date} ${d.event}`).join(' · ')}</>
          : cal.data.schedule.message}</div>
      </>}
    </Card>
  )
}
