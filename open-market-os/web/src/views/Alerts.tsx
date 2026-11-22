import { useEffect, useState } from 'preact/hooks'
import { del, get, live, patch, post } from '../api'
import { Badge, Btn, Card, Empty, ErrorBox, Field, Tabs, useLoad } from '../components/ui'
import { errText, fmtNum, fmtTime, toast } from '../store'

const KIND_LABEL: Record<string, string> = {
  price_above: 'Price goes above', price_below: 'Price goes below', price_cross: 'Price crosses', pct_change: 'Moves by % over N bars', expression: 'Formula becomes true',
}

export function Alerts() {
  const [tab, setTab] = useState<'alerts' | 'history' | 'notifications'>('alerts')
  return (
    <>
      <div class="alertbox">Alerts are checked on the host about every 15 seconds while it runs; price alerts use the latest bar of the chosen timeframe.
        If this computer sleeps, alerts do not fire.</div>
      <Tabs tabs={[['alerts', 'Alerts'], ['history', 'Trigger history'], ['notifications', 'Notifications']]} value={tab} onChange={setTab} />
      {tab === 'alerts' && <AlertList />}
      {tab === 'history' && <History />}
      {tab === 'notifications' && <Notifications />}
    </>
  )
}

function AlertList() {
  const alerts = useLoad(() => get<any[]>('/alerts'))
  const [f, setF] = useState<any>({ name: '', instrument_id: 'OKX:BTC-USDT', kind: 'price_above', timeframe: '1m', frequency: 'once', level: '', pct: 5, bars: 1, expression: 'crossover(ema(close, 9), ema(close, 21))', expires: '', sound: true, desktop: false })
  const [err, setErr] = useState<unknown>(null)
  useEffect(() => live.on((e) => { if (e.topic === 'notification' && e.data?.category === 'alert') alerts.reload() }), [])
  const set = (k: string, numeric = false) => (e: Event) => {
    const t = e.target as HTMLInputElement
    setF({ ...f, [k]: t.type === 'checkbox' ? t.checked : numeric ? Number(t.value) : t.value })
  }
  const create = async () => {
    try {
      setErr(null)
      const params = f.kind === 'pct_change' ? { pct: Number(f.pct), bars: Number(f.bars) } : f.kind === 'expression' ? { expression: f.expression } : { level: Number(f.level) }
      await post('/alerts', { name: f.name, instrument_id: f.instrument_id.trim(), kind: f.kind, params, timeframe: f.timeframe, frequency: f.frequency,
        expires_at: f.expires ? new Date(f.expires).getTime() : null, notify: { sound: f.sound, desktop: f.desktop } })
      toast('Alert created', 'ok'); alerts.reload()
    } catch (e) { setErr(e) }
  }
  return (
    <>
      <Card title="New alert">
        <div class="row">
          <Field label="Instrument"><input value={f.instrument_id} onInput={set('instrument_id')} /></Field>
          <Field label="When"><select value={f.kind} onChange={set('kind')}>{Object.entries(KIND_LABEL).map(([k, v]) => <option value={k}>{v}</option>)}</select></Field>
          {['price_above', 'price_below', 'price_cross'].includes(f.kind) && <Field label="Price level"><input type="number" step="any" style="width:120px" value={f.level} onInput={set('level')} /></Field>}
          {f.kind === 'pct_change' && <>
            <Field label="Change % (negative = drop)"><input type="number" step="any" style="width:90px" value={f.pct} onInput={set('pct', true)} /></Field>
            <Field label="Over bars"><input type="number" min={1} max={1000} style="width:80px" value={f.bars} onInput={set('bars', true)} /></Field></>}
          {f.kind === 'expression' && <Field label="OMS Script formula"><input class="mono" style="width:320px" value={f.expression} onInput={set('expression')} /></Field>}
          <Field label="Bar timeframe"><select value={f.timeframe} onChange={set('timeframe')}>{['1m', '5m', '15m', '1h', '4h', '1d'].map((t) => <option>{t}</option>)}</select></Field>
        </div>
        <div class="row">
          <Field label="Name (optional)"><input value={f.name} onInput={set('name')} /></Field>
          <Field label="Fire"><select value={f.frequency} onChange={set('frequency')}><option value="once">once, then stop</option><option value="once_per_bar">at most once per bar</option><option value="every">every time it re-arms</option></select></Field>
          <Field label="Expires (optional)"><input type="datetime-local" value={f.expires} onInput={set('expires')} /></Field>
          <label class="row small"><input type="checkbox" checked={f.sound} onChange={set('sound')} /> sound</label>
          <label class="row small"><input type="checkbox" checked={f.desktop} onChange={set('desktop')} /> desktop notification</label>
          <span class="sp" /><Btn kind="primary" onClick={create}>Create alert</Btn>
        </div>
        <ErrorBox error={err} />
      </Card>
      <Card title="Alerts">
        <ErrorBox error={alerts.error} />
        <div class="tablewrap"><table class="t">
          <thead><tr><th>Name</th><th>Instrument</th><th>Condition</th><th>State</th><th>Last fired</th><th class="right">Fired</th><th /></tr></thead>
          <tbody>{(alerts.data || []).map((a) => <tr>
            <td>{a.name}</td><td class="mono small">{a.instrument_id}</td>
            <td class="small">{KIND_LABEL[a.kind]} {a.params.level ?? ''}{a.params.pct !== undefined ? `${a.params.pct}% / ${a.params.bars} bars` : ''}
              {a.params.expression ? <span class="mono">{a.params.expression}</span> : ''} <span class="muted">({a.timeframe}, {a.frequency})</span></td>
            <td><Badge kind={a.state === 'active' ? 'ok' : a.state === 'triggered' ? 'running' : 'HISTORICAL'}>{a.state}</Badge>{a.snoozed_until ? <span class="small muted"> until {fmtTime(a.snoozed_until)}</span> : ''}</td>
            <td class="small">{fmtTime(a.last_triggered_at)}</td><td class="right">{a.trigger_count}</td>
            <td class="row" style="flex-wrap:nowrap">
              {a.state !== 'active' ? <Btn sm onClick={async () => { await patch(`/alerts/${a.id}`, { state: 'active' }); alerts.reload() }}>Enable</Btn>
                : <Btn sm onClick={async () => { await patch(`/alerts/${a.id}`, { state: 'disabled' }); alerts.reload() }}>Disable</Btn>}
              <Btn sm onClick={async () => { await patch(`/alerts/${a.id}`, { state: 'snoozed', snooze_minutes: 60 }); alerts.reload() }}>Snooze 1h</Btn>
              <Btn sm onClick={async () => { try { await post(`/alerts/${a.id}/test`); toast('Test notification sent', 'ok') } catch (e) { toast(errText(e), 'error') } }}>Test</Btn>
              <Btn sm kind="ghost" onClick={async () => { await del(`/alerts/${a.id}`); alerts.reload() }}>✕</Btn>
            </td></tr>)}</tbody>
        </table></div>
        {alerts.data?.length === 0 && <Empty>No alerts yet.</Empty>}
      </Card>
    </>
  )
}

function History() {
  const h = useLoad(() => get<any[]>('/alerts/history'))
  return (
    <Card title="Trigger history" actions={<Btn sm kind="ghost" onClick={h.reload}>Refresh</Btn>}>
      <table class="t"><tbody>{(h.data || []).map((e) => <tr class={e.acknowledged ? '' : 'sel'}>
        <td class="small">{fmtTime(e.ts)}</td><td>{e.message}</td><td class="right">{fmtNum(e.value)}</td>
        <td>{!e.acknowledged && <Btn sm onClick={async () => { await post(`/alert_events/${e.id}/ack`); h.reload() }}>Acknowledge</Btn>}</td></tr>)}</tbody></table>
      {h.data?.length === 0 && <Empty>No alert has fired yet.</Empty>}
    </Card>
  )
}

function Notifications() {
  const n = useLoad(() => get<any[]>('/notifications'))
  const [perm, setPerm] = useState(typeof Notification === 'undefined' ? 'unsupported' : Notification.permission)
  return (
    <Card title="Notifications" actions={<>
      {perm === 'default' && <Btn sm onClick={async () => setPerm(await Notification.requestPermission())}>Allow browser notifications</Btn>}
      {perm === 'denied' && <span class="small muted">browser notifications blocked in this browser</span>}
      <Btn sm onClick={async () => { await post('/notifications/read', {}); n.reload() }}>Mark all read</Btn></>}>
      {(n.data || []).map((x) => <div class="row small" style="border-top:1px solid var(--line);padding:6px 0">
        <span class="muted" style="white-space:nowrap">{fmtTime(x.ts)}</span><Badge kind={x.level === 'warning' ? 'degraded' : x.level === 'error' ? 'error' : 'HISTORICAL'}>{x.category}</Badge>
        <b style={x.read ? 'font-weight:normal' : ''}>{x.title}</b><span>{x.body}</span></div>)}
      {n.data?.length === 0 && <Empty>No notifications.</Empty>}
    </Card>
  )
}
