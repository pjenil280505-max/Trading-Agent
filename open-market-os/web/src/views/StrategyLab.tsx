import { useState } from 'preact/hooks'
import { del, get, post, put } from '../api'
import { Badge, Btn, Card, Empty, ErrorBox, Field, KV, LineChart, Modal, StatusBadge, Tabs, useLoad } from '../components/ui'
import { errText, fmtNum, fmtPct, fmtTime, prefs, toast, useStore } from '../store'

const STARTER = `# OMS Script strategy. Signals are read on CLOSED bars; orders fill at the NEXT bar's open.
param fast = 12 (5, 30, 1)
param slow = 26 (20, 100, 2)
f = ema(close, fast)
s = ema(close, slow)
long_entry = crossover(f, s)
long_exit = crossunder(f, s)
stop_loss_pct = 8
plot(f, "fast EMA")
plot(s, "slow EMA")
`

type Cfg = { initial_cash: number; commission_pct: number; slippage_bps: number; spread_bps: number; fill: string;
  sizing: string; size_value: number; allow_short: boolean; pyramiding: number; partial_exit_pct: number }
const DEFAULT_CFG: Cfg = { initial_cash: 10000, commission_pct: 0.1, slippage_bps: 5, spread_bps: 0, fill: 'next_open',
  sizing: 'pct_equity', size_value: 100, allow_short: false, pyramiding: 1, partial_exit_pct: 0 }

export function StrategyLab() {
  const pr = useStore(prefs)
  const list = useLoad(() => get<any[]>('/strategies'))
  const templates = useLoad(() => get<any[]>('/strategies/templates'))
  const [sid, setSid] = useState<number | null>(null)
  const [version, setVersion] = useState<number | null>(null)
  const [versions, setVersions] = useState<any[]>([])
  const [savedSource, setSavedSource] = useState('')
  const [name, setName] = useState('My strategy')
  const [src, setSrc] = useState(STARTER)
  const [check, setCheck] = useState<any>(null)
  const [checkErr, setCheckErr] = useState<unknown>(null)
  const [instrument, setInstrument] = useState('OKX:BTC-USDT')
  const [tf, setTf] = useState('1d')
  const [limit, setLimit] = useState(2000)
  const [cfg, setCfg] = useState<Cfg>(DEFAULT_CFG)
  const [params, setParams] = useState<Record<string, number>>({})
  const [res, setRes] = useState<any>(null)
  const [runErr, setRunErr] = useState<unknown>(null)
  const [tab, setTab] = useState<'result' | 'opt' | 'wf' | 'oos' | 'mc'>('result')
  const [robust, setRobust] = useState<Record<string, any>>({})
  const [showFns, setShowFns] = useState(false)

  const open = async (id: number, v?: number) => {
    const s = await get(`/strategies/${id}`, { version: v })
    setSid(id); setVersion(s.version); setVersions(s.versions); setName(s.name); setSrc(s.source); setSavedSource(s.source)
    setCheck(null); setRes(null); setRobust({}); setParams({})
  }
  const validate = async () => {
    try { setCheckErr(null); const c = await post('/strategies/validate', { source: src }); setCheck(c); return c } catch (e) { setCheck(null); setCheckErr(e); return null }
  }
  const save = async () => {
    try {
      const s = sid ? await put(`/strategies/${sid}`, { source: src, note: '' }) : await post('/strategies', { name, source: src })
      setSid(s.id); setVersion(s.version); setSavedSource(s.source); list.reload()
      const full = await get(`/strategies/${s.id}`); setVersions(full.versions)
      toast(`Saved ${s.name} v${s.version}`, 'ok')
    } catch (e) { toast(errText(e), 'error') }
  }
  const body = () => ({
    source: src, instrument, tf, limit, config: cfg, params: Object.keys(params).length ? params : undefined,
    ...(sid && src === savedSource ? { strategy_id: sid, version } : {}),
  })
  const run = async () => {
    try { setRunErr(null); setTab('result'); setRes(await post('/backtest', body())) } catch (e) { setRunErr(e); setRes(null) }
  }
  const robustRun = async (kind: 'opt' | 'wf' | 'oos' | 'mc', extra: Record<string, unknown>) => {
    const path = { opt: '/backtest/optimize', wf: '/backtest/walk_forward', oos: '/backtest/out_of_sample', mc: '/backtest/monte_carlo' }[kind]
    try { setRunErr(null); setTab(kind); const r = await post(path, { ...body(), ...extra }); setRobust((x) => ({ ...x, [kind]: r })) } catch (e) { setRunErr(e) }
  }
  const num = (k: keyof Cfg) => (e: Event) => setCfg({ ...cfg, [k]: Number((e.target as HTMLInputElement).value) })

  return (
    <>
      {pr.mode === 'beginner' && <div class="alertbox">A backtest replays a rule over past prices. Costs (commission, slippage, spread) are always charged.
        Good backtests are <b>not</b> evidence of future profit — check walk-forward and out-of-sample results, and paper-trade before anything else.</div>}
      <div class="grid" style="grid-template-columns:minmax(220px,280px) 1fr">
        <Card title="Strategies">
          <ErrorBox error={list.error} />
          {(list.data || []).map((s) => <div class="row"><Btn sm on={sid === s.id} onClick={() => open(s.id)}>{s.name}</Btn><span class="small muted">v{s.current_version}</span></div>)}
          {(list.data || []).length === 0 && <Empty>No saved strategies.</Empty>}
          <div class="col" style="margin-top:10px">
            <Btn sm onClick={() => { setSid(null); setVersion(null); setVersions([]); setName('My strategy'); setSrc(STARTER); setSavedSource(''); setRes(null); setCheck(null) }}>+ New</Btn>
            <Field label="Start from a template">
              <select onChange={(e) => { const t = (templates.data || []).find((x) => x.key === (e.target as HTMLSelectElement).value); if (t) { setSid(null); setVersions([]); setName(t.name); setSrc(t.source); setSavedSource(''); setCheck(null); setRes(null) } }}>
                <option value="">Choose…</option>{(templates.data || []).map((t) => <option value={t.key}>{t.name}</option>)}
              </select>
            </Field>
            <Btn sm kind="ghost" onClick={() => setShowFns(true)}>Function reference</Btn>
            <ImportButton onDone={(id) => { list.reload(); void open(id) }} />
          </div>
        </Card>
        <Card title={<>Editor {sid ? <span class="small muted">saved as #{sid} v{version}{src !== savedSource ? ' · unsaved changes' : ''}</span> : <span class="small muted">not saved</span>}</>}
          actions={<>
            {versions.length > 1 && <select value={version ?? ''} onChange={(e) => sid && open(sid, Number((e.target as HTMLSelectElement).value))}>
              {versions.map((v) => <option value={v.version}>v{v.version} · {new Date(v.created_at).toLocaleDateString()}</option>)}</select>}
            {sid && <Btn sm onClick={async () => {
              const d = await get(`/strategies/${sid}/export`)
              const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([JSON.stringify(d, null, 2)], { type: 'application/json' }))
              a.download = `${d.name.replace(/[^a-z0-9_-]+/gi, '_')}.omos.json`; a.click()
            }}>Export</Btn>}
            {sid && <Btn sm kind="ghost" onClick={async () => { if (confirm(`Delete strategy ${name} and all its versions?`)) { await del(`/strategies/${sid}`); setSid(null); list.reload() } }}>Delete</Btn>}
          </>}>
          <div class="row">
            <Field label="Name"><input value={name} disabled={!!sid} onInput={(e) => setName((e.target as HTMLInputElement).value)} /></Field>
            <span class="sp" />
            <Btn onClick={validate}>Check script</Btn>
            <Btn kind="primary" onClick={save}>{sid ? 'Save new version' : 'Save'}</Btn>
          </div>
          <textarea class="mono" spellcheck={false} style="width:100%;min-height:260px;margin-top:8px;font-size:13px;tab-size:2"
            value={src} onInput={(e) => setSrc((e.target as HTMLTextAreaElement).value)} aria-label="Strategy source" />
          <ErrorBox error={checkErr} />
          {check && <div class="small" style="margin-top:6px">
            <Badge kind={check.is_strategy ? 'ok' : 'degraded'}>{check.is_strategy ? 'valid strategy' : 'valid script (no long_entry/short_entry: cannot be backtested)'}</Badge>
            {' '}outputs: <span class="mono">{check.outputs.join(', ')}</span>{check.plots.length > 0 && <> · plots: {check.plots.join(', ')}</>}
          </div>}
        </Card>
      </div>

      <Card title="Run on real data">
        <div class="row">
          <Field label="Instrument (PROVIDER:SYMBOL)"><input value={instrument} onInput={(e) => setInstrument((e.target as HTMLInputElement).value.trim())} /></Field>
          <Field label="Timeframe"><select value={tf} onChange={(e) => setTf((e.target as HTMLSelectElement).value)}>{['5m', '15m', '1h', '4h', '1d', '1w'].map((t) => <option>{t}</option>)}</select></Field>
          <Field label="Bars"><input type="number" min={100} max={50000} style="width:90px" value={limit} onInput={(e) => setLimit(Number((e.target as HTMLInputElement).value))} /></Field>
          <Field label="Starting cash"><input type="number" style="width:100px" value={cfg.initial_cash} onInput={num('initial_cash')} /></Field>
          <Field label="Commission %"><input type="number" step="0.01" style="width:80px" value={cfg.commission_pct} onInput={num('commission_pct')} /></Field>
          <Field label="Slippage (bps)"><input type="number" style="width:80px" value={cfg.slippage_bps} onInput={num('slippage_bps')} /></Field>
          <Field label="Spread (bps)"><input type="number" style="width:80px" value={cfg.spread_bps} onInput={num('spread_bps')} /></Field>
        </div>
        {pr.mode === 'advanced' && <div class="row">
          <Field label="Fill"><select value={cfg.fill} onChange={(e) => setCfg({ ...cfg, fill: (e.target as HTMLSelectElement).value })}>
            <option value="next_open">next bar open (realistic)</option><option value="close">same bar close (optimistic)</option></select></Field>
          <Field label="Sizing"><select value={cfg.sizing} onChange={(e) => setCfg({ ...cfg, sizing: (e.target as HTMLSelectElement).value })}>
            <option value="pct_equity">% of equity</option><option value="fixed_cash">fixed cash</option><option value="fixed_qty">fixed quantity</option><option value="risk_pct">% equity at risk (needs a stop)</option></select></Field>
          <Field label="Size value"><input type="number" step="any" style="width:90px" value={cfg.size_value} onInput={num('size_value')} /></Field>
          <Field label="Pyramiding"><input type="number" min={1} max={50} style="width:70px" value={cfg.pyramiding} onInput={num('pyramiding')} /></Field>
          <Field label="Partial exit at target %"><input type="number" min={0} max={100} style="width:70px" value={cfg.partial_exit_pct} onInput={num('partial_exit_pct')} /></Field>
          <label class="row small"><input type="checkbox" checked={cfg.allow_short} onChange={(e) => setCfg({ ...cfg, allow_short: (e.target as HTMLInputElement).checked })} /> allow short</label>
        </div>}
        {check?.params?.length > 0 && <div class="row">
          {check.params.map((p: any) => <Field label={`${p.name}${p.min !== null ? ` (${p.min}–${p.max})` : ''}`}>
            <input type="number" step="any" style="width:80px" value={params[p.name] ?? p.default}
              onInput={(e) => setParams({ ...params, [p.name]: Number((e.target as HTMLInputElement).value) })} /></Field>)}
          <Btn sm kind="ghost" onClick={() => setParams({})}>Reset parameters</Btn>
        </div>}
        <div class="row" style="margin-top:8px">
          <Btn kind="primary" onClick={async () => { if (await validate()) await run() }}>Run backtest</Btn>
          <span class="sp" />
          <span class="small muted">Robustness:</span>
          <Btn sm onClick={() => robustRun('opt', { objective: 'sharpe', min_trades: 5 })}>Optimise</Btn>
          <Btn sm onClick={() => robustRun('wf', { folds: 4, objective: 'sharpe' })}>Walk-forward</Btn>
          <Btn sm onClick={() => robustRun('oos', { train_pct: 70, objective: 'sharpe' })}>Out-of-sample</Btn>
          <Btn sm onClick={() => robustRun('mc', { runs: 1000 })}>Monte Carlo</Btn>
        </div>
      </Card>
      <ErrorBox error={runErr} />
      {(res || Object.keys(robust).length > 0) && <>
        <Tabs tabs={[['result', 'Backtest'], ['opt', 'Optimise'], ['wf', 'Walk-forward'], ['oos', 'Out-of-sample'], ['mc', 'Monte Carlo']]} value={tab} onChange={setTab} />
        {tab === 'result' && (res ? <Result res={res} /> : <Card><Empty>Run a backtest first.</Empty></Card>)}
        {tab === 'opt' && <Optimise r={robust.opt} />}
        {tab === 'wf' && <WalkForward r={robust.wf} />}
        {tab === 'oos' && <OutOfSample r={robust.oos} />}
        {tab === 'mc' && <MonteCarlo r={robust.mc} />}
      </>}
      <RecentBacktests />
      {showFns && <FunctionsModal onClose={() => setShowFns(false)} />}
    </>
  )
}

function Result(p: { res: any }) {
  const m = p.res.metrics, d = p.res.data
  return (
    <Card title={<>Result <StatusBadge status={d.status} source={d.source} /> <span class="small muted">{d.bars} bars · {fmtTime(d.from)} → {fmtTime(d.to)}</span></>}>
      {p.res.warnings.map((w: string) => <div class="alertbox warn">{w}</div>)}
      <KV items={[
        ['Total return', <span class={m.total_return_pct >= 0 ? 'pos' : 'neg'}>{fmtPct(m.total_return_pct)}</span>],
        ['Buy & hold', fmtPct(m.buy_hold_return_pct)], ['CAGR', fmtPct(m.cagr_pct)], ['Max drawdown', fmtPct(-m.max_drawdown_pct)],
        ['Sharpe', fmtNum(m.sharpe)], ['Sortino', fmtNum(m.sortino)], ['Trades', m.trades], ['Win rate', fmtPct(m.win_rate_pct, 1).replace('+', '')],
        ['Profit factor', fmtNum(m.profit_factor)], ['Expectancy / trade', fmtPct(m.expectancy_pct)], ['Time in market', fmtPct(m.exposure_pct, 1).replace('+', '')],
        ['Fees paid', fmtNum(m.fees_paid)],
      ]} />
      <h3>Equity</h3>
      <LineChart series={[{ name: 'equity', points: p.res.equity, color: 'var(--accent)' }]} />
      <h3>Drawdown %</h3>
      <LineChart height={120} zero series={[{ name: 'drawdown', points: (p.res.drawdown as [number, number][]).map(([t, v]) => [t, -Math.abs(v)] as [number, number]), color: 'var(--bad)' }]} />
      <h3>Trades ({p.res.trades.length})</h3>
      <div class="tablewrap" style="max-height:360px;overflow:auto"><table class="t">
        <thead><tr><th>Side</th><th>Entry</th><th class="right">Entry px</th><th>Exit</th><th class="right">Exit px</th><th class="right">Qty</th><th class="right">P&L</th><th class="right">%</th><th class="right">Bars</th><th>Exit reason</th></tr></thead>
        <tbody>{[...p.res.trades].reverse().slice(0, 300).map((t: any) => <tr>
          <td>{t.side}</td><td class="small">{fmtTime(t.entry_ts)}</td><td class="right">{fmtNum(t.entry_price)}</td><td class="small">{fmtTime(t.exit_ts)}</td>
          <td class="right">{fmtNum(t.exit_price)}</td><td class="right">{fmtNum(t.qty, 4)}</td><td class={`right ${t.pnl >= 0 ? 'pos' : 'neg'}`}>{fmtNum(t.pnl)}</td>
          <td class="right">{fmtPct(t.pnl_pct)}</td><td class="right">{t.bars}</td><td class="small">{t.reason}</td></tr>)}</tbody>
      </table></div>
    </Card>
  )
}

function Optimise(p: { r: any }) {
  if (!p.r) return <Card><Empty>Press "Optimise" to test the parameter grid declared in the script.</Empty></Card>
  return (
    <Card title={`Grid search: ${p.r.valid} of ${p.r.combinations} combinations had enough trades (objective: ${p.r.objective})`}>
      <div class="alertbox warn">{p.r.warning}</div>
      <div class="tablewrap"><table class="t">
        <thead><tr><th>Parameters</th><th class="right">Score</th><th class="right">Return</th><th class="right">Sharpe</th><th class="right">Max DD</th><th class="right">Trades</th><th class="right">Win %</th></tr></thead>
        <tbody>{p.r.results.map((x: any) => <tr><td class="mono small">{Object.entries(x.params).map(([k, v]) => `${k}=${v}`).join(' ')}</td>
          <td class="right">{fmtNum(x.score)}</td><td class="right">{fmtPct(x.metrics.total_return_pct)}</td><td class="right">{fmtNum(x.metrics.sharpe)}</td>
          <td class="right">{fmtPct(-x.metrics.max_drawdown_pct)}</td><td class="right">{x.metrics.trades}</td><td class="right">{fmtNum(x.metrics.win_rate_pct, 1)}</td></tr>)}</tbody>
      </table></div>
    </Card>
  )
}

function WalkForward(p: { r: any }) {
  if (!p.r) return <Card><Empty>Walk-forward optimises on one window and tests on the next, several times. Press "Walk-forward".</Empty></Card>
  return (
    <Card title="Walk-forward analysis">
      <KV items={[['Mean out-of-sample return', fmtPct(p.r.mean_oos_return_pct)], ['Profitable OOS folds', `${p.r.profitable_oos_folds} / ${p.r.folds.length}`],
        ['Walk-forward efficiency', p.r.walk_forward_efficiency === null ? '—' : fmtNum(p.r.walk_forward_efficiency)]]} />
      <p class="small muted">Efficiency = mean out-of-sample return ÷ mean in-sample return. Far below 1 suggests the in-sample result was mostly luck/overfitting.</p>
      <table class="t"><thead><tr><th>Fold</th><th>Chosen parameters</th><th class="right">In-sample</th><th class="right">Out-of-sample</th><th class="right">OOS Sharpe</th><th class="right">OOS trades</th><th>Test window</th></tr></thead>
        <tbody>{p.r.folds.map((f: any) => f.message ? <tr><td>{f.fold}</td><td colSpan={6} class="muted">{f.message}</td></tr> : <tr>
          <td>{f.fold}</td><td class="mono small">{Object.entries(f.params).map(([k, v]) => `${k}=${v}`).join(' ')}</td>
          <td class="right">{fmtPct(f.in_sample_return_pct)}</td><td class={`right ${f.out_of_sample_return_pct >= 0 ? 'pos' : 'neg'}`}>{fmtPct(f.out_of_sample_return_pct)}</td>
          <td class="right">{fmtNum(f.out_of_sample_sharpe)}</td><td class="right">{f.out_of_sample_trades}</td>
          <td class="small">{new Date(f.test_from).toLocaleDateString()} → {new Date(f.test_to).toLocaleDateString()}</td></tr>)}</tbody></table>
    </Card>
  )
}

function OutOfSample(p: { r: any }) {
  if (!p.r) return <Card><Empty>Optimise on the first 70% of the data, then test once on the unseen 30%. Press "Out-of-sample".</Empty></Card>
  if (!p.r.out_of_sample) return <Card><Empty>{p.r.message}</Empty></Card>
  const rows: [string, string][] = [['total_return_pct', 'Return %'], ['sharpe', 'Sharpe'], ['max_drawdown_pct', 'Max drawdown %'], ['trades', 'Trades'], ['win_rate_pct', 'Win rate %'], ['profit_factor', 'Profit factor']]
  return (
    <Card title={`Out-of-sample test (split at ${new Date(p.r.split_ts).toLocaleDateString()}: ${p.r.train_bars} train / ${p.r.test_bars} test bars)`}>
      <div class="small">Best in-sample parameters: <span class="mono">{Object.entries(p.r.best_params).map(([k, v]) => `${k}=${v}`).join(' ')}</span></div>
      <table class="t"><thead><tr><th>Metric</th><th class="right">In-sample (optimised)</th><th class="right">Out-of-sample (unseen)</th></tr></thead>
        <tbody>{rows.map(([k, label]) => <tr><td>{label}</td><td class="right">{fmtNum(p.r.in_sample[k])}</td><td class="right"><b>{fmtNum(p.r.out_of_sample[k])}</b></td></tr>)}</tbody></table>
    </Card>
  )
}

function MonteCarlo(p: { r: any }) {
  if (!p.r) return <Card><Empty>Resample the backtest's trades 1,000 times to see how much the result depends on luck. Press "Monte Carlo".</Empty></Card>
  if (!p.r.final_equity) return <Card><Empty>{p.r.message || 'No trades to resample.'}</Empty></Card>
  return (
    <Card title={`Monte Carlo: ${p.r.runs} resamples of ${p.r.trades} trades`}>
      <KV items={[['Final equity, bad case (5th pct)', fmtNum(p.r.final_equity.p5)], ['Final equity, median', fmtNum(p.r.final_equity.p50)],
        ['Final equity, good case (95th pct)', fmtNum(p.r.final_equity.p95)], ['Chance of losing money', fmtPct(p.r.prob_loss_pct, 1).replace('+', '')],
        ['Max drawdown, median', fmtPct(-p.r.max_drawdown_pct.p50)], ['Max drawdown, 95th pct', fmtPct(-p.r.max_drawdown_pct.p95)], ['Worst drawdown seen', fmtPct(-p.r.max_drawdown_pct.worst)]]} />
      <p class="small muted">{p.r.note}</p>
    </Card>
  )
}

function RecentBacktests() {
  const bts = useLoad(() => get<any[]>('/backtests', { limit: 20 }))
  if (!bts.data?.length) return null
  return (
    <Card title="Recent backtests" actions={<Btn sm kind="ghost" onClick={bts.reload}>Refresh</Btn>}>
      <div class="tablewrap"><table class="t">
        <thead><tr><th>When</th><th>Instrument</th><th>TF</th><th>Strategy</th><th class="right">Return</th><th class="right">Max DD</th><th class="right">Sharpe</th><th class="right">Trades</th><th>Data</th></tr></thead>
        <tbody>{bts.data.map((b) => <tr><td class="small">{fmtTime(b.created_at)}</td><td class="mono">{b.instrument_id}</td><td>{b.timeframe}</td>
          <td class="small">{b.strategy_id ? `#${b.strategy_id} v${b.strategy_version ?? '?'}` : 'unsaved'}</td><td class="right">{fmtPct(b.metrics.total_return_pct)}</td>
          <td class="right">{fmtPct(-b.metrics.max_drawdown_pct)}</td><td class="right">{fmtNum(b.metrics.sharpe)}</td><td class="right">{b.metrics.trades}</td>
          <td><StatusBadge status={b.data_status} /></td></tr>)}</tbody>
      </table></div>
    </Card>
  )
}

function FunctionsModal(p: { onClose: () => void }) {
  const fns = useLoad(() => get<any[]>('/script/functions'))
  const [q, setQ] = useState('')
  return (
    <Modal title="OMS Script functions" onClose={p.onClose}>
      <p class="small muted">Variables: open high low close volume hl2 hlc3 ohlc4 bar_index time. <span class="mono">x[n]</span> is x n bars ago (no future values exist).
        Strategy outputs: long_entry long_exit short_entry short_exit stop_loss(_pct) take_profit(_pct) trailing_stop(_pct) position_size_pct.
        Higher timeframe: <span class="mono">tf("4h", expr)</span> uses completed 4h bars only. Plot: <span class="mono">plot(expr, "label")</span>.</p>
      <input placeholder="Filter" value={q} onInput={(e) => setQ((e.target as HTMLInputElement).value)} />
      <div style="max-height:50vh;overflow:auto;margin-top:8px">
        <table class="t"><tbody>{(fns.data || []).filter((f) => !q || f.name.includes(q) || f.doc.toLowerCase().includes(q.toLowerCase())).map((f) => (
          <tr><td class="mono">{f.name}</td><td class="small muted">{f.min_args === f.max_args ? f.min_args : `${f.min_args}–${f.max_args}`} args</td><td class="small">{f.doc}</td></tr>
        ))}</tbody></table>
      </div>
    </Modal>
  )
}

function ImportButton(p: { onDone: (id: number) => void }) {
  return (
    <label class="btn sm ghost" style="cursor:pointer">Import file
      <input type="file" accept=".json,application/json" style="display:none" onChange={async (e) => {
        const f = (e.target as HTMLInputElement).files?.[0]
        if (!f) return
        try {
          if (f.size > 1_000_000) throw new Error('file is larger than 1 MB')
          const s = await post('/strategies/import', JSON.parse(await f.text()))
          toast(`Imported ${s.name}`, 'ok'); p.onDone(s.id)
        } catch (err) { toast(errText(err), 'error') }
      }} />
    </label>
  )
}
