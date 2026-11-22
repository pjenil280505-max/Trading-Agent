import { useEffect, useState } from 'preact/hooks'
import { get, live, post, put } from '../api'
import { Badge, Btn, Card, Confirm, Empty, ErrorBox, Field, KV, Tabs, useLoad } from '../components/ui'
import { errText, fmtNum, fmtTime, prefs, toast, useStore } from '../store'

const RUNNABLE = new Set(['validated', 'backtested', 'paper_tested', 'approved', 'paused', 'stopped', 'error', 'recovering'])
const ARCHIVABLE = new Set(['created', 'configured', 'validated', 'backtested', 'paper_tested', 'approved', 'paused', 'error', 'stopped'])
const FLOW = ['configured', 'validated', 'backtested', 'running', 'paper_tested', 'approved']

export function Bots() {
  const pr = useStore(prefs)
  const bots = useLoad(() => get<any[]>('/bots'))
  const [sel, setSel] = useState<number | null>(null)
  const [creating, setCreating] = useState(false)
  useEffect(() => live.on((e) => { if (e.topic === 'bot_state') bots.reload() }), [])
  return (
    <>
      <div class="alertbox">Bots trade in <b>PAPER</b> accounts with simulated money, act only on <b>closed</b> bars, and every order passes the risk engine.
        They run only while this computer is on, awake and online. {pr.mode === 'beginner' && <>Steps: configure → validate → backtest → start (paper).</>}</div>
      <Card title="Your bots" actions={<Btn sm kind="primary" onClick={() => setCreating(!creating)}>{creating ? 'Close' : '+ New bot'}</Btn>}>
        <ErrorBox error={bots.error} />
        {creating && <CreateBot onDone={(b) => { setCreating(false); bots.reload(); setSel(b.id) }} />}
        <div class="tablewrap"><table class="t">
          <thead><tr><th>Name</th><th>Type</th><th>Instrument</th><th>TF</th><th>State</th><th>Mode</th><th /></tr></thead>
          <tbody>{(bots.data || []).map((b) => <tr class={sel === b.id ? 'sel' : ''}>
            <td>{b.name}</td><td>{b.kind}</td><td class="mono small">{b.instrument_id}</td><td>{b.timeframe}</td>
            <td><Badge kind={b.state} /></td><td><Badge kind={b.mode === 'paper' ? 'PAPER' : 'LIVE'} /></td>
            <td><Btn sm onClick={() => setSel(b.id)}>Open</Btn></td></tr>)}</tbody>
        </table></div>
        {bots.data?.length === 0 && !creating && <Empty>No bots yet.</Empty>}
      </Card>
      {sel && <BotDetail id={sel} onChange={bots.reload} onClose={() => setSel(null)} />}
    </>
  )
}

function CreateBot(p: { onDone: (b: any) => void }) {
  const accts = useLoad(() => get<any[]>('/accounts'))
  const strategies = useLoad(() => get<any[]>('/strategies'))
  const templates = useLoad(() => get<any[]>('/strategies/templates'))
  const [f, setF] = useState<any>({ name: 'My bot', kind: 'rule', instrument_id: 'OKX:BTC-USDT', timeframe: '1h', account_id: prefs.get().accountId })
  const [rule, setRule] = useState<any>({ source: 'template:ema_cross', script: '', sizing: 'pct_equity', size_value: 10, allow_short: false, lookback_bars: 300 })
  const [dca, setDca] = useState<any>({ sizing: 'fixed_cash', size_value: 50, every_bars: 24, max_invested: 1000, condition: '' })
  const [grid, setGrid] = useState<any>({ lower: 0, upper: 0, levels: 10, qty_per_level: 0 })
  const [err, setErr] = useState<unknown>(null)
  const set = (o: any, setter: (x: any) => void, k: string, numeric = false) => (e: Event) => {
    const t = e.target as HTMLInputElement
    setter({ ...o, [k]: t.type === 'checkbox' ? t.checked : numeric ? Number(t.value) : t.value })
  }
  const submit = async () => {
    try {
      setErr(null)
      let config: any, strategy_id: number | undefined
      if (f.kind === 'rule') {
        const [kind, key] = String(rule.source).split(':')
        let script = rule.script
        if (kind === 'template') script = (templates.data || []).find((t) => t.key === key)?.source
        if (kind === 'strategy') strategy_id = Number(key)
        config = { sizing: rule.sizing, size_value: rule.size_value, allow_short: rule.allow_short, lookback_bars: rule.lookback_bars, ...(kind === 'strategy' ? {} : { script }) }
      } else if (f.kind === 'dca') {
        config = { ...dca, condition: dca.condition || undefined }
      } else config = { ...grid, sizing: 'fixed_qty', size_value: grid.qty_per_level }
      p.onDone(await post('/bots', { ...f, account_id: Number(f.account_id), config, strategy_id }))
    } catch (e) { setErr(e) }
  }
  return (
    <div class="card" style="background:var(--panel2)">
      <div class="row">
        <Field label="Name"><input value={f.name} onInput={set(f, setF, 'name')} /></Field>
        <Field label="Type"><select value={f.kind} onChange={set(f, setF, 'kind')}>
          <option value="rule">Rule (strategy script)</option><option value="dca">DCA (regular buys)</option><option value="grid">Grid (buy low / sell high in a range)</option></select></Field>
        <Field label="Instrument"><input value={f.instrument_id} onInput={set(f, setF, 'instrument_id')} /></Field>
        <Field label="Timeframe"><select value={f.timeframe} onChange={set(f, setF, 'timeframe')}>{['1m', '5m', '15m', '1h', '4h', '1d'].map((t) => <option>{t}</option>)}</select></Field>
        <Field label="Paper account"><select value={f.account_id ?? ''} onChange={set(f, setF, 'account_id')}>
          {(accts.data || []).filter((a) => a.kind === 'paper').map((a) => <option value={a.id}>{a.name} ({fmtNum(a.equity)})</option>)}</select></Field>
      </div>
      {f.kind === 'rule' && <div class="row">
        <Field label="Strategy"><select value={rule.source} onChange={set(rule, setRule, 'source')}>
          <optgroup label="Templates">{(templates.data || []).map((t) => <option value={`template:${t.key}`}>{t.name}</option>)}</optgroup>
          <optgroup label="Saved strategies">{(strategies.data || []).map((s) => <option value={`strategy:${s.id}`}>{s.name} (v{s.current_version})</option>)}</optgroup>
          <option value="custom:">Custom script…</option></select></Field>
        <Field label="Position size"><select value={rule.sizing} onChange={set(rule, setRule, 'sizing')}>
          <option value="pct_equity">% of equity</option><option value="fixed_cash">fixed cash</option><option value="fixed_qty">fixed quantity</option><option value="risk_pct">% equity at risk (needs stop)</option></select></Field>
        <Field label="Size value"><input type="number" step="any" style="width:90px" value={rule.size_value} onInput={set(rule, setRule, 'size_value', true)} /></Field>
        <Field label="Bars of history"><input type="number" min={50} max={5000} style="width:90px" value={rule.lookback_bars} onInput={set(rule, setRule, 'lookback_bars', true)} /></Field>
        <label class="row small"><input type="checkbox" checked={rule.allow_short} onChange={set(rule, setRule, 'allow_short')} /> allow short</label>
      </div>}
      {f.kind === 'rule' && rule.source === 'custom:' && <textarea class="mono" style="width:100%;min-height:140px" value={rule.script} onInput={set(rule, setRule, 'script')}
        placeholder="long_entry = crossover(ema(close, 12), ema(close, 26))&#10;long_exit = crossunder(ema(close, 12), ema(close, 26))" />}
      {f.kind === 'dca' && <div class="row">
        <Field label="Amount per buy"><select value={dca.sizing} onChange={set(dca, setDca, 'sizing')}><option value="fixed_cash">fixed cash</option><option value="pct_equity">% of equity</option></select></Field>
        <Field label="Value"><input type="number" step="any" style="width:90px" value={dca.size_value} onInput={set(dca, setDca, 'size_value', true)} /></Field>
        <Field label="Buy every N bars"><input type="number" min={1} style="width:80px" value={dca.every_bars} onInput={set(dca, setDca, 'every_bars', true)} /></Field>
        <Field label="Stop after investing"><input type="number" step="any" style="width:100px" value={dca.max_invested} onInput={set(dca, setDca, 'max_invested', true)} /></Field>
        <Field label="Only when (optional formula)"><input class="mono" style="width:260px" placeholder="rsi(close, 14) < 40" value={dca.condition} onInput={set(dca, setDca, 'condition')} /></Field>
      </div>}
      {f.kind === 'grid' && <div class="row">
        <Field label="Lower price"><input type="number" step="any" style="width:100px" value={grid.lower} onInput={set(grid, setGrid, 'lower', true)} /></Field>
        <Field label="Upper price"><input type="number" step="any" style="width:100px" value={grid.upper} onInput={set(grid, setGrid, 'upper', true)} /></Field>
        <Field label="Levels (2–100)"><input type="number" min={2} max={100} style="width:80px" value={grid.levels} onInput={set(grid, setGrid, 'levels', true)} /></Field>
        <Field label="Quantity per level"><input type="number" step="any" style="width:100px" value={grid.qty_per_level} onInput={set(grid, setGrid, 'qty_per_level', true)} /></Field>
        {grid.upper > grid.lower && grid.levels >= 2 && <span class="small muted">Spacing {fmtNum((grid.upper - grid.lower) / (grid.levels - 1))} ·
          buying every level below the price needs up to ~{fmtNum(grid.qty_per_level * grid.levels * (grid.lower + grid.upper) / 2)} cash. Grids lose when price leaves the range.</span>}
      </div>}
      <ErrorBox error={err} />
      <div class="row" style="margin-top:8px"><span class="sp" /><Btn kind="primary" onClick={submit}>Create (paper)</Btn></div>
    </div>
  )
}

function BotDetail(p: { id: number; onChange: () => void; onClose: () => void }) {
  const pr = useStore(prefs)
  const bot = useLoad(() => get(`/bots/${p.id}`), [p.id])
  const logs = useLoad(() => get<any[]>(`/bots/${p.id}/logs`, { limit: 200 }), [p.id])
  const [tab, setTab] = useState<'overview' | 'config' | 'logs' | 'history'>('overview')
  const [cfgText, setCfgText] = useState('')
  const [bt, setBt] = useState<any>(null)
  const [approve, setApprove] = useState(false)
  useEffect(() => { if (bot.data) setCfgText(JSON.stringify(bot.data.config, null, 2)) }, [bot.data?.version])
  useEffect(() => live.on((e) => {
    if ((e.topic === 'bot_log' || e.topic === 'bot_state') && e.data?.bot_id === p.id) { logs.reload(); if (e.topic === 'bot_state') bot.reload() }
  }), [p.id])
  const b = bot.data
  const act = async (action: string, body: any = {}) => {
    try {
      const r = await post(`/bots/${p.id}/${action}`, body)
      if (action === 'backtest') { setBt(r); toast('Backtest finished', 'ok') } else toast(`${action}: done`, 'ok')
      bot.reload(); logs.reload(); p.onChange()
    } catch (e) { toast(errText(e), 'error') }
  }
  if (bot.error) return <Card><ErrorBox error={bot.error} /></Card>
  if (!b) return <Card><Empty>Loading…</Empty></Card>
  const perf = b.performance
  const st = b.state as string
  const runtimeH = (b.flags?.paper_run_ms || 0) / 3_600_000
  return (
    <Card title={<>{b.name} <Badge kind={st} /> <Badge kind={perf.label} /></>} actions={<Btn sm kind="ghost" onClick={p.onClose}>✕</Btn>}>
      <div class="row small" style="flex-wrap:wrap">{FLOW.map((s, i) => <><span class={`badge ${st === s ? 'b-ok' : 'b-HISTORICAL'}`}>{s.replace('_', ' ')}</span>{i < FLOW.length - 1 && '→'}</>)}</div>
      <div class="row" style="margin-top:10px;flex-wrap:wrap">
        <Btn sm disabled={st !== 'configured'} onClick={() => act('validate')}>Validate</Btn>
        {b.kind === 'rule' && <Btn sm onClick={() => act('backtest')}>Backtest</Btn>}
        <Btn sm kind="primary" disabled={!RUNNABLE.has(st)} onClick={() => act('start')}>Start</Btn>
        <Btn sm disabled={st !== 'running' && st !== 'recovering'} onClick={() => act('pause')}>Pause</Btn>
        <Btn sm disabled={st !== 'paused'} onClick={() => act('resume')}>Resume</Btn>
        <Btn sm kind="danger" disabled={!['running', 'paused', 'recovering', 'error'].includes(st)} onClick={() => act('stop')}>Stop &amp; cancel orders</Btn>
        <Btn sm onClick={() => act('clone', { name: `${b.name} copy` })}>Clone</Btn>
        <Btn sm disabled={!(st === 'backtested' || st === 'stopped')} title={`needs 24 h of paper running (has ${runtimeH.toFixed(1)} h)`} onClick={() => act('mark_paper_tested')}>Mark paper-tested</Btn>
        {pr.mode === 'advanced' && <Btn sm disabled={st !== 'paper_tested'} onClick={() => setApprove(true)}>Approve for live…</Btn>}
        <Btn sm kind="ghost" disabled={!ARCHIVABLE.has(st)} onClick={async () => { if (confirm(`Archive bot ${b.name}?`)) { await act('archive'); p.onClose() } }}>Archive</Btn>
      </div>
      <Tabs tabs={[['overview', 'Overview'], ['config', 'Configuration'], ['logs', `Logs (${logs.data?.length ?? 0})`], ['history', 'State history']]} value={tab} onChange={setTab} />
      {tab === 'overview' && <>
        <KV items={[['Fills', perf.fills], ['Realised P&L', <span class={perf.realized_pnl >= 0 ? 'pos' : 'neg'}>{fmtNum(perf.realized_pnl)}</span>],
          ['Unrealised P&L', <span class={perf.unrealized_pnl >= 0 ? 'pos' : 'neg'}>{fmtNum(perf.unrealized_pnl)}</span>], ['Fees', fmtNum(perf.fees)],
          ['Open quantity', fmtNum(perf.open_qty, 6)], ['Paper runtime', `${runtimeH.toFixed(1)} h`], ['Instrument', <span class="mono small">{b.instrument_id}</span>], ['Timeframe', b.timeframe]]} />
        {b.flags?.backtest && <p class="small">Last backtest ({fmtTime(b.flags.backtest.at)}, {b.flags.backtest.bars} {b.flags.backtest.data_status} bars):
          return {fmtNum(b.flags.backtest.metrics.total_return_pct)}%, max drawdown {fmtNum(b.flags.backtest.metrics.max_drawdown_pct)}%,
          Sharpe {fmtNum(b.flags.backtest.metrics.sharpe)}, {b.flags.backtest.metrics.trades} trades.</p>}
        {bt && <div class="alertbox">{bt.warnings.join(' ')} Return {fmtNum(bt.metrics.total_return_pct)}% vs buy &amp; hold {fmtNum(bt.metrics.buy_hold_return_pct)}%.</div>}
        {b.runtime && Object.keys(b.runtime).length > 0 && <details><summary class="small muted">Runtime state</summary><pre class="small">{JSON.stringify(b.runtime, null, 2)}</pre></details>}
      </>}
      {tab === 'config' && <>
        <p class="small muted">Edit JSON, then save. Changing a bot sends it back to "configured": it must be validated again. Pause or stop it first.</p>
        <textarea class="mono" style="width:100%;min-height:220px" value={cfgText} onInput={(e) => setCfgText((e.target as HTMLTextAreaElement).value)} />
        <div class="row"><span class="sp" /><Btn kind="primary" disabled={st === 'running' || st === 'recovering'} onClick={async () => {
          try { await put(`/bots/${p.id}`, { config: JSON.parse(cfgText) }); toast('Configuration saved', 'ok'); bot.reload(); p.onChange() } catch (e) { toast(errText(e), 'error') }
        }}>Save configuration</Btn></div>
      </>}
      {tab === 'logs' && <div class="tablewrap" style="max-height:380px;overflow:auto"><table class="t"><tbody>
        {(logs.data || []).map((l) => <tr><td class="small muted" style="white-space:nowrap">{fmtTime(l.ts)}</td><td><Badge kind={l.level === 'error' ? 'error' : l.level === 'warning' ? 'degraded' : 'HISTORICAL'}>{l.level}</Badge></td><td class="small">{l.message}</td></tr>)}
      </tbody></table>{logs.data?.length === 0 && <Empty>No log lines yet.</Empty>}</div>}
      {tab === 'history' && <table class="t"><tbody>{(b.history || []).map((h: any) => <tr><td class="small muted">{fmtTime(h.ts)}</td><td>{h.from_state || '—'} → <b>{h.to_state}</b></td><td class="small">{h.reason}</td></tr>)}</tbody></table>}
      {approve && <Confirm phrase={`APPROVE ${b.name}`} onClose={() => setApprove(false)} onConfirm={() => act('approve', { confirm: `APPROVE ${b.name}` })}
        text="Approval is recorded in the audit log. This release ships no verified live broker adapter, so the bot keeps trading on PAPER." />}
    </Card>
  )
}
