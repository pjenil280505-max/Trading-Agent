import { useEffect, useState } from 'preact/hooks'
import { get, post, put } from '../api'
import { Badge, Btn, Card, Confirm, Empty, ErrorBox, Field, KV, useLoad } from '../components/ui'
import { errText, fmtPct, fmtTime, prefs, toast, useStore } from '../store'

type Lim = Record<string, number | boolean | string[]>
const GROUPS: [string, [string, string, string?][]][] = [
  ['Per trade', [['max_risk_per_trade_pct', 'Max equity at risk per trade (%)'], ['max_position_risk_pct', 'Max risk of one position, all layers (%)'],
    ['assumed_stop_pct', 'Risk assumed when an order has no stop (%)'], ['max_order_value_pct', 'Max single order value (% of equity)'],
    ['max_order_qty', 'Max order quantity (0 = no cap)'], ['max_price_deviation_pct', 'Limit/stop price band around last price (%)'], ['require_stop_loss', 'Require a stop-loss on every order', 'bool']]],
  ['Exposure', [['max_total_exposure_pct', 'Max total exposure (% of equity)'], ['max_instrument_exposure_pct', 'Max exposure to one instrument (%)'],
    ['max_leverage', 'Max leverage (1 = none)'], ['max_open_positions', 'Max open positions'], ['max_layers_per_position', 'Safety ceiling: layers in one position (layered bots adapt below it)'],
    ['high_volatility_atr_pct', 'High volatility: ATR % of price above which the lower ceiling applies'],
    ['high_volatility_max_layers', 'Layer ceiling in high volatility'], ['max_layer_distance_pct', 'Deepest layer of a basket: max % from the price'],
    ['allow_short', 'Allow short selling', 'bool']]],
  ['Circuit breakers (halt trading when hit)', [['max_daily_loss_pct', 'Daily loss limit (%)'], ['max_weekly_loss_pct', 'Weekly loss limit (%)'], ['max_drawdown_pct', 'Max drawdown from peak (%)']]],
  ['Activity', [['max_orders_per_minute', 'Max orders per minute'], ['max_trades_per_day', 'Max trades per day'], ['max_simultaneous_bots', 'Max bots running at once'],
    ['trading_hours_only', 'Only trade while the market session is open', 'bool']]],
  ['Instruments', [['allowed_instruments', 'Allow only these (comma separated, empty = all)', 'list'], ['blocked_instruments', 'Never trade these', 'list']]],
]

export function Risk() {
  const pr = useStore(prefs)
  const aid = pr.accountId
  const status = useLoad(() => get('/system/status'))
  const state = useLoad(() => aid ? get('/risk/state', { account_id: aid }) : Promise.resolve(null), [aid])
  const events = useLoad(() => get<any[]>('/risk/events', { account_id: aid || undefined }), [aid])
  const [stop, setStop] = useState<null | 'stop' | 'clear'>(null)
  const [flatten, setFlatten] = useState(false)
  const halt = status.data?.emergency_stop
  const reloadAll = () => { status.reload(); state.reload(); events.reload() }
  return (
    <>
      <Card title="Emergency stop">
        {halt?.halted
          ? <div class="alertbox error">ACTIVE since {fmtTime(halt.at)} by {halt.by}: {halt.reason}. No order can be placed by anyone until a person clears it.</div>
          : <p class="small muted">Pauses every bot, cancels every open order and blocks all new orders (paper and live). Only a person can clear it.</p>}
        <div class="row">
          <label class="row small"><input type="checkbox" checked={flatten} onChange={(e) => setFlatten((e.target as HTMLInputElement).checked)} /> also close all positions</label>
          <span class="sp" />
          {halt?.halted && <Btn onClick={() => setStop('clear')}>Clear emergency stop…</Btn>}
          <Btn kind="danger" onClick={() => setStop('stop')}>■ EMERGENCY STOP</Btn>
        </div>
      </Card>
      {aid && <Card title={`Account #${aid} circuit breakers`}>
        <ErrorBox error={state.error} />
        {state.data && <>
          {state.data.state.trading_halted ? <div class="alertbox error row">Trading halted: {state.data.state.halt_reason}<span class="sp" />
            <Btn kind="primary" onClick={async () => { try { await post('/risk/resume', { account_id: aid }); toast('Trading resumed', 'ok'); reloadAll() } catch (e) { toast(errText(e), 'error') } }}>Resume trading</Btn></div>
            : <Badge kind="ok">trading allowed</Badge>}
          <KV items={[['Today', fmtPct(state.data.monitor.day_pnl_pct)], ['This week', fmtPct(state.data.monitor.week_pnl_pct)],
            ['Drawdown from peak', fmtPct(-state.data.monitor.drawdown_pct)], ['Peak equity', state.data.monitor.peak_equity?.toFixed(2)]]} />
        </>}
      </Card>}
      {aid ? <LimitsEditor aid={aid} /> : <Card><Empty>Select an account in Portfolio.</Empty></Card>}
      <Card title="Risk decisions log" actions={<Btn sm kind="ghost" onClick={events.reload}>Refresh</Btn>}>
        <div class="tablewrap" style="max-height:420px;overflow:auto"><table class="t">
          <thead><tr><th>Time</th><th>Decision</th><th>Origin</th><th>Rule</th><th>Message</th></tr></thead>
          <tbody>{(events.data || []).map((e) => <tr><td class="small">{fmtTime(e.ts)}</td>
            <td><Badge kind={e.decision === 'approved' || e.decision === 'resume' ? 'ok' : e.decision === 'rejected' ? 'degraded' : 'error'}>{e.decision}</Badge></td>
            <td class="small">{e.origin}</td><td class="mono small">{e.rule}</td><td class="small">{e.message}</td></tr>)}</tbody>
        </table></div>
        {events.data?.length === 0 && <Empty>No risk events yet.</Empty>}
      </Card>
      {stop === 'stop' && <Confirm text={`Stop ALL trading now?${flatten ? ' Every open position will also be closed with market orders.' : ''}`}
        onClose={() => setStop(null)} onConfirm={async () => { const r = await post('/risk/emergency_stop', { flatten }); toast(`Emergency stop: ${r.paused_bots} bot(s) paused, ${r.cancelled_orders} order(s) cancelled, ${r.flattened} position(s) closed`, 'warn', 12000); reloadAll() }} />}
      {stop === 'clear' && <Confirm phrase="RESUME TRADING" text="Clear the emergency stop? Bots stay paused until you resume them one by one."
        onClose={() => setStop(null)} onConfirm={async () => { try { await post('/risk/clear_emergency'); toast('Emergency stop cleared', 'ok'); reloadAll() } catch (e) { toast(errText(e), 'error') } }} />}
    </>
  )
}

function LimitsEditor(p: { aid: number }) {
  const lim = useLoad(() => get<Lim>('/risk/limits', { account_id: p.aid }), [p.aid])
  const [draft, setDraft] = useState<Lim | null>(null)
  useEffect(() => { if (lim.data) setDraft(lim.data) }, [lim.data])
  if (lim.error) return <Card><ErrorBox error={lim.error} /></Card>
  if (!draft) return null
  const changed = JSON.stringify(draft) !== JSON.stringify(lim.data)
  return (
    <Card title="Risk limits" actions={<>
      {changed && <Btn sm kind="ghost" onClick={() => setDraft(lim.data!)}>Undo changes</Btn>}
      <Btn sm kind="primary" disabled={!changed} onClick={async () => {
        try { const r = await put('/risk/limits', { account_id: p.aid, limits: draft }); setDraft(r); lim.reload(); toast('Risk limits saved', 'ok') } catch (e) { toast(errText(e), 'error') }
      }}>Save limits</Btn></>}>
      <p class="small muted">These rules are checked on every order from every source (you, bots, workflows, AI proposals). They cannot be bypassed from the AI or MCP side.</p>
      <div class="grid g2">{GROUPS.map(([title, fields]) => (
        <div><h3>{title}</h3>
          {fields.map(([k, label, kind]) => kind === 'bool'
            ? <label class="row small" style="margin:6px 0"><input type="checkbox" checked={!!draft[k]} onChange={(e) => setDraft({ ...draft, [k]: (e.target as HTMLInputElement).checked })} /> {label}</label>
            : kind === 'list'
              ? <Field label={label}><input value={(draft[k] as string[]).join(', ')} onInput={(e) => setDraft({ ...draft, [k]: (e.target as HTMLInputElement).value.split(',').map((s) => s.trim()).filter(Boolean) })} /></Field>
              : <Field label={label}><input type="number" step="any" style="width:120px" value={draft[k] as number} onInput={(e) => setDraft({ ...draft, [k]: Number((e.target as HTMLInputElement).value) })} /></Field>)}
        </div>
      ))}</div>
    </Card>
  )
}
