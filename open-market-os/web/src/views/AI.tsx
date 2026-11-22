import { useState } from 'preact/hooks'
import { get, post } from '../api'
import { Badge, Btn, Card, Empty, ErrorBox, Field, Tabs, useLoad } from '../components/ui'
import { fmtNum, fmtTime } from '../store'

const ROLES: [string, string][] = [['market_analyst', 'Market analyst'], ['bull', 'Bull analyst'], ['bear', 'Bear analyst'], ['quant', 'Quant researcher'],
  ['risk', 'Risk analyst'], ['news', 'News analyst'], ['fundamental', 'Fundamental analyst'], ['strategy_developer', 'Strategy developer']]

export function AIWorkspace() {
  const providers = useLoad(() => get<any[]>('/ai/providers'))
  const runs = useLoad(() => get('/ai/runs'))
  const enabled = (providers.data || []).filter((p) => p.enabled)
  const [pid, setPid] = useState<number | null>(null)
  const [mode, setMode] = useState<'analyze' | 'debate' | 'chat'>('analyze')
  const [instrument, setInstrument] = useState('OKX:BTC-USDT')
  const [tf, setTf] = useState('1d')
  const [role, setRole] = useState('market_analyst')
  const [task, setTask] = useState('Analyse this instrument.')
  const [question, setQuestion] = useState('What is the trend of OKX:BTC-USDT on the daily chart, and how volatile is it?')
  const [out, setOut] = useState<any>(null)
  const [err, setErr] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const provider = enabled.find((p) => p.id === pid) || enabled[0]
  const go = async () => {
    if (!provider) return
    setBusy(true); setErr(null); setOut(null)
    try {
      if (mode === 'analyze') setOut({ mode, ...(await post('/ai/analyze', { provider_id: provider.id, instrument, tf, role, task })) })
      if (mode === 'debate') setOut({ mode, ...(await post('/ai/debate', { provider_id: provider.id, instrument, tf })) })
      if (mode === 'chat') setOut({ mode, ...(await post('/ai/chat', { provider_id: provider.id, question })) })
      runs.reload()
    } catch (e) { setErr(e) } finally { setBusy(false) }
  }
  return (
    <>
      <div class="alertbox">AI is optional. Models only see an <b>evidence pack</b> built by code from real data, every number they quote is checked against it,
        and they cannot place trades or change settings: at most they can <b>propose</b> an order for you to approve.</div>
      <ErrorBox error={providers.error} />
      {providers.data && enabled.length === 0 && <Card><Empty>No AI provider is enabled. Add one in <a href="/settings" onClick={(e) => { e.preventDefault(); (window as any).omosNavigate('/settings') }}>Settings → AI</a>
        (Ollama runs free on your own computer; "mock" is a rule-based stand-in for testing).</Empty></Card>}
      {provider && <Card title="Ask" actions={runs.data && <span class="small muted">today: ${fmtNum(runs.data.spend_today_usd, 4)} of ${fmtNum(runs.data.daily_budget_usd)} budget (hosted providers only)</span>}>
        <div class="row">
          <Field label="Provider"><select value={provider.id} onChange={(e) => setPid(Number((e.target as HTMLSelectElement).value))}>
            {enabled.map((p) => <option value={p.id}>{p.name} · {p.model}{p.is_local ? ' (local, free)' : ' (hosted, may cost money)'}</option>)}</select></Field>
          <Tabs tabs={[['analyze', 'Single analyst'], ['debate', 'Bull vs bear debate'], ['chat', 'Question with tools']]} value={mode} onChange={setMode} />
        </div>
        {mode !== 'chat' && <div class="row">
          <Field label="Instrument"><input value={instrument} onInput={(e) => setInstrument((e.target as HTMLInputElement).value.trim())} /></Field>
          <Field label="Timeframe"><select value={tf} onChange={(e) => setTf((e.target as HTMLSelectElement).value)}>{['1h', '4h', '1d', '1w'].map((t) => <option>{t}</option>)}</select></Field>
          {mode === 'analyze' && <Field label="Role"><select value={role} onChange={(e) => setRole((e.target as HTMLSelectElement).value)}>{ROLES.map(([k, v]) => <option value={k}>{v}</option>)}</select></Field>}
          {mode === 'analyze' && <Field label="Task"><input style="width:320px" value={task} onInput={(e) => setTask((e.target as HTMLInputElement).value)} /></Field>}
        </div>}
        {mode === 'debate' && <p class="small muted">Runs bull, bear and risk analysts on the same evidence, then a supervisor summarises where they agree and disagree (4 model calls).</p>}
        {mode === 'chat' && <textarea style="width:100%;min-height:70px" value={question} onInput={(e) => setQuestion((e.target as HTMLTextAreaElement).value)} />}
        <div class="row"><span class="sp" />{!provider.is_local && <span class="small neg">hosted provider: this request may be billed</span>}
          <Btn kind="primary" disabled={busy} onClick={go}>{busy ? 'Thinking… (local models can take a minute)' : 'Run'}</Btn></div>
      </Card>}
      <ErrorBox error={err} />
      {out?.mode === 'analyze' && <><Analysis r={out.result} title={ROLES.find(([k]) => k === role)?.[1] || role} /><Evidence ev={out.evidence} /></>}
      {out?.mode === 'debate' && <>
        <Card title={`Debate: agreement ${Math.round(out.agreement_ratio * 100)}% · total cost $${fmtNum(out.total_cost_usd, 4)}`}>
          <div class="grid g3">{Object.entries(out.views).map(([r, v]) => <Analysis compact r={v} title={r} />)}</div>
        </Card>
        <Analysis r={out.consensus} title="Supervisor consensus" />
        <Evidence ev={out.evidence} />
      </>}
      {out?.mode === 'chat' && <>
        {out.trace?.length > 0 && <Card title="Tool calls made by the model">{out.trace.map((t: any) => <div class="small">{t.ok ? '✔' : '✖'} <span class="mono">{t.tool}</span> {t.error || ''}</div>)}</Card>}
        {out.error ? <div class="alertbox error">{out.error}</div> : <Analysis r={out.result} title="Answer" />}
      </>}
      <Card title="Recent AI runs">
        <div class="tablewrap" style="max-height:300px;overflow:auto"><table class="t">
          <thead><tr><th>Time</th><th>Workflow</th><th>Role</th><th class="right">Tokens in/out</th><th class="right">Cost</th><th>Status</th></tr></thead>
          <tbody>{(runs.data?.runs || []).map((r: any) => <tr><td class="small">{fmtTime(r.ts)}</td><td>{r.workflow}</td><td>{r.role}</td>
            <td class="right">{r.tokens_in} / {r.tokens_out}</td><td class="right">${fmtNum(r.cost, 4)}</td><td><Badge kind={r.status === 'ok' ? 'ok' : 'degraded'}>{r.status}</Badge></td></tr>)}</tbody>
        </table></div>
      </Card>
    </>
  )
}

function Analysis(p: { r: any; title: string; compact?: boolean }) {
  const r = p.r
  if (!r) return null
  if (r.error) return <Card title={p.title}><div class="alertbox error">{r.label}: {r.error}</div>{r.raw_preview && <pre>{r.raw_preview}</pre>}</Card>
  const stanceKind = r.stance === 'bullish' ? 'ok' : r.stance === 'bearish' ? 'error' : 'HISTORICAL'
  const v = r.verification
  return (
    <Card title={<>{p.title} <Badge kind={stanceKind}>{r.stance}</Badge> <span class="small muted">confidence {Math.round((r.confidence || 0) * 100)}%</span></>}>
      <div class="small" style="color:var(--warn)">{r.label}</div>
      <p>{r.summary}</p>
      {v && v.numeric_observations > 0 && <div class={`small ${v.verified === v.numeric_observations ? 'pos' : 'neg'}`}>
        {v.verified} of {v.numeric_observations} numeric claims match the evidence{v.unverified_claims.length > 0 && <>; unverified: {v.unverified_claims.join(' | ')}</>}</div>}
      {r.observations?.length > 0 && <><h3>Observations</h3><ul class="small">{r.observations.map((o: any) => <li>
        <span title={o.verified === null ? 'no number to check' : o.verified ? 'number found in evidence' : 'number NOT found in evidence'}>{o.verified === null ? '·' : o.verified ? '✔' : '✖'}</span> {o.claim}
        {o.evidence && <span class="muted mono"> [{o.evidence}]</span>}</li>)}</ul></>}
      {!p.compact && r.calculations?.length > 0 && <><h3>Calculations</h3><ul class="small">{r.calculations.map((c: any) => <li>{c.description}: <b>{c.result}</b></li>)}</ul></>}
      {r.interpretation && <><h3>Interpretation</h3><p class="small">{r.interpretation}</p></>}
      {r.predictions?.length > 0 && <><h3>Predictions (not facts)</h3><ul class="small">{r.predictions.map((x: any) => <li>{x.statement} <span class="muted">({x.horizon}, {Math.round(x.confidence * 100)}%)</span></li>)}</ul></>}
      {!p.compact && r.assumptions?.length > 0 && <><h3>Assumptions</h3><ul class="small">{r.assumptions.map((a: string) => <li>{a}</li>)}</ul></>}
      {r.uncertainty && <p class="small"><b>Uncertainty:</b> {r.uncertainty}</p>}
      {r._meta && <div class="small muted">{r._meta.provider} · {r._meta.model} · {r._meta.tokens_in}+{r._meta.tokens_out} tokens · ${fmtNum(r._meta.cost_usd, 4)} · {fmtNum(r._meta.latency_ms, 0)} ms{r._meta.is_local ? ' · local' : ''}</div>}
    </Card>
  )
}

function Evidence(p: { ev: any }) {
  if (!p.ev) return null
  return <Card title="Evidence pack (what the model was shown)"><details><summary class="small">Show JSON ({JSON.stringify(p.ev).length.toLocaleString()} characters)</summary><pre class="small">{JSON.stringify(p.ev, null, 2)}</pre></details></Card>
}
