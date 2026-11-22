import { useEffect } from 'preact/hooks'
import { get } from '../api'
import { Badge, Btn, Card, ErrorBox, KV, useLoad } from '../components/ui'
import { fmtNum, fmtTime } from '../store'

export function Status() {
  const s = useLoad(() => get('/system/status'))
  useEffect(() => { const t = setInterval(s.reload, 10000); return () => clearInterval(t) }, [])
  if (s.error) return <Card><ErrorBox error={s.error} /></Card>
  if (!s.data) return <Card>Loading…</Card>
  const d = s.data
  const up = d.uptime_s
  const dev = d.device
  return (
    <>
      <div class="alertbox">{d.note_24x7} For a full diagnostic report run <code>omos doctor</code> on the host computer.</div>
      <Card title="Host" actions={<Btn sm kind="ghost" onClick={s.reload}>Refresh</Btn>}>
        <KV items={[['Version', d.version], ['Uptime', `${Math.floor(up / 3600)} h ${Math.floor((up % 3600) / 60)} min`], ['Listening on', `${d.bind}:${d.port}`],
          ['Other devices', d.remote_access ? 'allowed (LAN)' : 'this computer only'], ['Database schema', `v${d.schema_version}`], ['Live-chart subscriptions', d.subscriptions],
          ['Automation', d.automation_enabled ? 'on' : 'OFF'], ['Live trading switch', d.live_trading_enabled ? 'ON' : 'off']]} />
        {d.emergency_stop?.halted && <div class="alertbox error">EMERGENCY STOP active: {d.emergency_stop.reason}</div>}
        <div class="small muted" style="margin-top:6px">Data folder: <code>{d.data_dir}</code></div>
      </Card>
      <Card title="Background loops (watchdog restarts any that stop)">
        <table class="t"><thead><tr><th>Loop</th><th>State</th><th class="right">Every</th><th>Last success</th><th class="right">Runs</th><th class="right">Restarts</th><th>Last error</th></tr></thead>
          <tbody>{d.loops.map((l: any) => <tr><td>{l.name}</td><td><Badge kind={l.alive ? 'ok' : 'down'}>{l.alive ? 'running' : 'stopped'}</Badge></td>
            <td class="right">{l.interval_s} s</td><td class="small">{fmtTime(l.last_ok)}</td><td class="right">{l.runs}</td><td class="right">{l.restarts}</td>
            <td class="small neg">{l.last_error || ''}</td></tr>)}</tbody></table>
      </Card>
      <Card title="Data providers">
        <table class="t"><thead><tr><th>Provider</th><th>Status</th><th class="right">Latency</th><th class="right">Requests</th><th class="right">Failures</th><th class="right">Rate-limited</th><th>Last OK</th><th>Last error</th></tr></thead>
          <tbody>{d.providers.map((p: any) => <tr><td>{p.provider}</td><td><Badge kind={p.status === 'unknown' ? 'HISTORICAL' : p.status}>{p.status}</Badge></td>
            <td class="right">{p.latency_ms ? `${fmtNum(p.latency_ms, 0)} ms` : '—'}</td><td class="right">{p.requests}</td><td class="right">{p.failures}</td><td class="right">{p.rate_limited}</td>
            <td class="small">{fmtTime(p.last_ok)}</td><td class="small neg">{p.last_error || ''}</td></tr>)}</tbody></table>
      </Card>
      <div class="grid g2">
        <Card title={`This computer (performance tier: ${dev.profile.tier})`}>
          <KV items={[['System', `${dev.os} ${dev.machine}`], ['CPU threads', dev.cpu_logical], ['RAM', `${dev.ram_available_gb} / ${dev.ram_total_gb} GB free`], ['Disk free', `${dev.disk_free_gb} GB`]]} />
          <p class="small muted">Adapted defaults: up to {dev.profile.chart_bars.toLocaleString()} bars per chart, {dev.profile.max_charts} charts, polling every {dev.profile.poll_seconds} s.
            AI: {dev.profile.ai_model_hint}.{dev.profile.on_battery ? ' On battery: polling slowed down.' : ''}</p>
        </Card>
        <Card title="Security and backups">
          <p class="small">Secrets: {d.secrets.backend} · {d.secrets.unlocked ? 'unlocked' : 'LOCKED'} · {d.secrets.count} stored · plaintext storage: {d.secrets.plaintext ? 'YES' : 'no'}</p>
          <table class="t"><tbody>{d.backups.map((b: any) => <tr><td class="mono small">{b.file}</td><td class="small">{fmtTime(b.modified)}</td></tr>)}</tbody></table>
          {d.backups.length === 0 && <p class="small neg">No backups yet.</p>}
        </Card>
      </div>
    </>
  )
}
