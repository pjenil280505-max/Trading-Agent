import { useState } from 'preact/hooks'
import { get, patch, post, put } from '../api'
import { Badge, Btn, Card, Empty, ErrorBox, Field, useLoad } from '../components/ui'
import { errText, fmtTime, prefs, toast } from '../store'

type F = [key: string, label: string, kind?: 'num' | 'bool' | 'json' | 'select', options?: string[]]
const TRIGGERS: Record<string, { label: string; fields: F[] }> = {
  schedule: { label: 'Every N minutes', fields: [['every_minutes', 'Minutes', 'num']] },
  price: { label: 'Price crosses a level', fields: [['instrument', 'Instrument'], ['timeframe', 'Bar timeframe'], ['op', 'Direction', 'select', ['>', '<']], ['level', 'Level', 'num']] },
  indicator: { label: 'Formula becomes true (closed bars)', fields: [['instrument', 'Instrument'], ['timeframe', 'Timeframe'], ['expression', 'OMS Script formula']] },
  market_open: { label: 'Market opens', fields: [['session', 'Session id (e.g. xnys, xnse)']] },
  market_close: { label: 'Market closes', fields: [['session', 'Session id']] },
  event: { label: 'App event', fields: [['topic', 'Topic', 'select', ['order', 'risk', 'bot_state', 'notification', 'proposal', 'screener_match', 'data']]] },
}
const STEPS: Record<string, { label: string; fields: F[]; effect?: boolean }> = {
  condition: { label: 'Check a condition (stop if false)', fields: [['instrument', 'Instrument'], ['timeframe', 'Timeframe'], ['expression', 'Formula']] },
  risk_check: { label: 'Stop if trading is halted', fields: [['account_id', 'Account id', 'num']] },
  run_screener: { label: 'Run a saved screener', fields: [['screener_id', 'Screener id', 'num'], ['stop_if_empty', 'Stop if no matches', 'bool']] },
  backtest: { label: 'Backtest a saved strategy', fields: [['strategy_id', 'Strategy id', 'num'], ['instrument', 'Instrument'], ['timeframe', 'Timeframe']] },
  ai_analysis: { label: 'AI analysis', effect: true, fields: [['provider_id', 'AI provider id', 'num'], ['instrument', 'Instrument'], ['timeframe', 'Timeframe'], ['role', 'Role']] },
  notify: { label: 'Send a notification', effect: true, fields: [['title', 'Title'], ['body', 'Text (use {{step.field}})']] },
  journal: { label: 'Write a journal entry', effect: true, fields: [['title', 'Title'], ['body', 'Text']] },
  paper_order: { label: 'Place a PAPER order (risk-checked)', effect: true, fields: [['account_id', 'Paper account id', 'num'], ['instrument', 'Instrument'], ['side', 'Side', 'select', ['buy', 'sell']], ['qty', 'Quantity', 'num'], ['stop_loss_pct', 'Stop-loss %', 'num']] },
  bot: { label: 'Start / pause / stop a bot', effect: true, fields: [['bot_id', 'Bot id', 'num'], ['action', 'Action', 'select', ['start', 'pause', 'stop']]] },
  mcp_tool: { label: 'Call an external MCP tool', effect: true, fields: [['server_id', 'MCP server id', 'num'], ['tool', 'Tool name'], ['args', 'Arguments (JSON)', 'json']] },
}

function blank(): any {
  const acct = prefs.get().accountId || 1
  return {
    name: 'RSI dip alert', trigger: { type: 'indicator', instrument: 'OKX:BTC-USDT', timeframe: '1h', expression: 'crossunder(rsi(close, 14), 30)' },
    steps: [
      { type: 'risk_check', name: 'risk', account_id: acct },
      { type: 'notify', name: 'tell_me', title: 'BTC RSI dipped below 30', body: 'Trigger: {{trigger.instrument}} — review the chart before acting.' },
    ],
    limits: { max_runs_per_day: 6, cooldown_minutes: 60, max_retries: 1 },
  }
}

export function Automation() {
  const wfs = useLoad(() => get<any[]>('/workflows'))
  const status = useLoad(() => get('/system/status'))
  const [edit, setEdit] = useState<any>(null)
  const [runsFor, setRunsFor] = useState<number | null>(null)
  const enabled = status.data?.automation_enabled
  return (
    <>
      <Card title={<>Automation {status.data && <Badge kind={enabled ? 'ok' : 'error'}>{enabled ? 'master switch ON' : 'master switch OFF'}</Badge>}</>} actions={<>
        {enabled ? <Btn sm kind="danger" onClick={async () => { await post('/workflows/disable_all'); toast('All automation stopped', 'warn'); status.reload() }}>Stop all automation</Btn>
          : <Btn sm onClick={async () => { try { await put('/settings', { 'automation.enabled': true }); status.reload() } catch (e) { toast(errText(e), 'error') } }}>Turn master switch on</Btn>}
        <Btn sm kind="primary" onClick={() => setEdit(blank())}>+ New workflow</Btn></>}>
        <p class="small muted">A workflow is: WHEN a trigger fires → run steps in order. New workflows are saved <b>disabled</b>. Use "Dry run" first: it evaluates conditions
          but only describes side effects. Orders can only go to PAPER accounts and always pass the risk engine. This is a step list, not a free-form node graph.</p>
        <ErrorBox error={wfs.error} />
        <table class="t"><tbody>{(wfs.data || []).map((w) => <tr>
          <td><b>{w.name}</b> <span class="small muted">v{w.current_version}</span></td>
          <td class="small">{TRIGGERS[w.definition?.trigger?.type]?.label} → {w.definition?.steps?.map((s: any) => s.type).join(' → ')}</td>
          <td><Badge kind={w.enabled ? 'ok' : 'HISTORICAL'}>{w.enabled ? 'enabled' : 'disabled'}</Badge></td>
          <td class="row" style="flex-wrap:nowrap">
            <Btn sm onClick={async () => { const r = await post(`/workflows/${w.id}/run`, { dry_run: true }); toast(`Dry run: ${r.status}`, 'ok'); setRunsFor(w.id) }}>Dry run</Btn>
            <Btn sm onClick={async () => { if (confirm('Run for real now? Side-effect steps will execute.')) { const r = await post(`/workflows/${w.id}/run`, { dry_run: false }); toast(`Run: ${r.status}`, r.status === 'failed' ? 'error' : 'ok'); setRunsFor(w.id) } }}>Run now</Btn>
            <Btn sm onClick={async () => { await patch(`/workflows/${w.id}`, { enabled: !w.enabled }); wfs.reload() }}>{w.enabled ? 'Disable' : 'Enable'}</Btn>
            <Btn sm onClick={() => setEdit({ name: w.name, ...structuredClone(w.definition) })}>Edit</Btn>
            <Btn sm kind="ghost" onClick={() => setRunsFor(w.id)}>Runs</Btn>
          </td></tr>)}</tbody></table>
        {wfs.data?.length === 0 && <Empty>No workflows yet.</Empty>}
      </Card>
      {edit && <Builder wf={edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); wfs.reload() }} />}
      {runsFor && <Runs wid={runsFor} onClose={() => setRunsFor(null)} />}
    </>
  )
}

function FieldsEditor(p: { fields: F[]; obj: any; onChange: (o: any) => void }) {
  return <div class="row">{p.fields.map(([k, label, kind, options]) => {
    const v = p.obj[k]
    if (kind === 'bool') return <label class="row small"><input type="checkbox" checked={!!v} onChange={(e) => p.onChange({ ...p.obj, [k]: (e.target as HTMLInputElement).checked })} /> {label}</label>
    if (kind === 'select') return <Field label={label}><select value={v ?? ''} onChange={(e) => p.onChange({ ...p.obj, [k]: (e.target as HTMLSelectElement).value })}>
      <option value="" disabled>choose…</option>{options!.map((o) => <option>{o}</option>)}</select></Field>
    if (kind === 'json') return <Field label={label}><input class="mono" value={typeof v === 'string' ? v : JSON.stringify(v ?? {})} onInput={(e) => {
      const t = (e.target as HTMLInputElement).value
      try { p.onChange({ ...p.obj, [k]: JSON.parse(t) }) } catch { p.onChange({ ...p.obj, [k]: t }) }
    }} /></Field>
    return <Field label={label}><input type={kind === 'num' ? 'number' : 'text'} step="any" class={k === 'expression' ? 'mono' : ''} style={k === 'expression' || k === 'body' ? 'width:320px' : 'width:150px'}
      value={v ?? ''} onInput={(e) => { const t = (e.target as HTMLInputElement).value; p.onChange({ ...p.obj, [k]: kind === 'num' ? (t === '' ? undefined : Number(t)) : t }) }} /></Field>
  })}</div>
}

function Builder(p: { wf: any; onClose: () => void; onSaved: () => void }) {
  const [wf, setWf] = useState<any>(p.wf)
  const [err, setErr] = useState<unknown>(null)
  const steps: any[] = wf.steps
  const setStep = (i: number, s: any) => setWf({ ...wf, steps: steps.map((x, j) => (j === i ? s : x)) })
  const move = (i: number, d: number) => { const a = [...steps]; const [s] = a.splice(i, 1); a.splice(i + d, 0, s); setWf({ ...wf, steps: a }) }
  return (
    <Card title="Workflow builder" actions={<Btn sm kind="ghost" onClick={p.onClose}>✕</Btn>}>
      <Field label="Name (saving with an existing name creates a new version)"><input value={wf.name} onInput={(e) => setWf({ ...wf, name: (e.target as HTMLInputElement).value })} /></Field>
      <div class="card" style="background:var(--panel2)">
        <div class="row"><b>WHEN</b><select value={wf.trigger.type} onChange={(e) => setWf({ ...wf, trigger: { type: (e.target as HTMLSelectElement).value } })}>
          {Object.entries(TRIGGERS).map(([k, t]) => <option value={k}>{t.label}</option>)}</select></div>
        <FieldsEditor fields={TRIGGERS[wf.trigger.type].fields} obj={wf.trigger} onChange={(t) => setWf({ ...wf, trigger: t })} />
      </div>
      {steps.map((s, i) => <>
        <div class="muted" style="text-align:center">↓</div>
        <div class="card" style="background:var(--panel2)">
          <div class="row"><b>{i + 1}.</b>
            <select value={s.type} onChange={(e) => setStep(i, { type: (e.target as HTMLSelectElement).value, name: s.name })}>
              {Object.entries(STEPS).map(([k, t]) => <option value={k}>{t.label}</option>)}</select>
            <input class="mono" style="width:120px" title="step name, used in {{name.field}} templates" value={s.name} onInput={(e) => setStep(i, { ...s, name: (e.target as HTMLInputElement).value })} />
            {STEPS[s.type]?.effect && <Badge kind="degraded">side effect: only described in dry runs</Badge>}
            <span class="sp" />
            <Btn sm kind="ghost" disabled={i === 0} onClick={() => move(i, -1)}>↑</Btn>
            <Btn sm kind="ghost" disabled={i === steps.length - 1} onClick={() => move(i, 1)}>↓</Btn>
            <Btn sm kind="ghost" onClick={() => setWf({ ...wf, steps: steps.filter((_, j) => j !== i) })}>✕</Btn>
          </div>
          <FieldsEditor fields={STEPS[s.type]?.fields || []} obj={s} onChange={(o) => setStep(i, o)} />
        </div>
      </>)}
      <div class="row" style="margin-top:8px">
        <Btn sm onClick={() => setWf({ ...wf, steps: [...steps, { type: 'notify', name: `step${steps.length + 1}`, title: '', body: '' }] })}>+ Step</Btn>
        <span class="sp" />
        <Field label="Max runs/day"><input type="number" style="width:80px" value={wf.limits?.max_runs_per_day} onInput={(e) => setWf({ ...wf, limits: { ...wf.limits, max_runs_per_day: Number((e.target as HTMLInputElement).value) } })} /></Field>
        <Field label="Cooldown (min)"><input type="number" style="width:80px" value={wf.limits?.cooldown_minutes} onInput={(e) => setWf({ ...wf, limits: { ...wf.limits, cooldown_minutes: Number((e.target as HTMLInputElement).value) } })} /></Field>
        <Field label="Retries/step"><input type="number" min={0} max={5} style="width:70px" value={wf.limits?.max_retries} onInput={(e) => setWf({ ...wf, limits: { ...wf.limits, max_retries: Number((e.target as HTMLInputElement).value) } })} /></Field>
      </div>
      <ErrorBox error={err} />
      <div class="row"><span class="sp" /><Btn kind="primary" onClick={async () => {
        try { setErr(null); const { name, ...definition } = wf; await post('/workflows', { name, definition }); toast('Workflow saved (disabled)', 'ok'); p.onSaved() } catch (e) { setErr(e) }
      }}>Save (disabled)</Btn></div>
    </Card>
  )
}

function Runs(p: { wid: number; onClose: () => void }) {
  const runs = useLoad(() => get<any[]>(`/workflows/${p.wid}/runs`), [p.wid])
  return (
    <Card title={`Run history of workflow #${p.wid}`} actions={<><Btn sm kind="ghost" onClick={runs.reload}>Refresh</Btn><Btn sm kind="ghost" onClick={p.onClose}>✕</Btn></>}>
      {(runs.data || []).map((r) => <div style="border-top:1px solid var(--line);padding:6px 0">
        <div class="row small"><span class="muted">{fmtTime(r.started_at)}</span><Badge kind={r.status === 'completed' ? 'ok' : r.status === 'failed' ? 'error' : 'HISTORICAL'}>{r.status}</Badge>
          {r.dry_run ? <Badge kind="SIMULATED">dry run</Badge> : null}<span class="muted">v{r.version} · trigger {r.trigger_data?.type}</span></div>
        <table class="t small"><tbody>{r.log.map((l: any) => <tr><td class="mono">{l.step}</td><td>{l.type}</td><td>{l.status}</td>
          <td class="mono" style="word-break:break-all">{l.message || JSON.stringify(l.result)}</td></tr>)}</tbody></table>
      </div>)}
      {runs.data?.length === 0 && <Empty>Never run.</Empty>}
    </Card>
  )
}
