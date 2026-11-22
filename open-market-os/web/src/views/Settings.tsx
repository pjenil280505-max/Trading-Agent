import { useEffect, useState } from 'preact/hooks'
import { del, get, patch, post, put } from '../api'
import { Badge, Btn, Card, Confirm, Empty, ErrorBox, Field, Modal, StatusBadge, Tabs, useLoad } from '../components/ui'
import { can, errText, fmtNum, fmtTime, prefs, toast, useStore } from '../store'
import { LocalAIPanel, SystemControls, Updates } from './Setup'
import qrcode from 'qrcode-generator'

type Tab = 'general' | 'data' | 'devices' | 'secrets' | 'ai' | 'mcp' | 'plugins' | 'backups' | 'live' | 'audit'

export function Settings() {
  const pr = useStore(prefs)
  const [tab, setTab] = useState<Tab>('general')
  const admin = can('admin')
  const tabs: [Tab, string][] = [['general', 'General'], ['data', 'Data sources'], ['devices', 'Devices & access'], ['secrets', 'Secrets'], ['ai', 'AI'],
    ['mcp', 'MCP'], ['plugins', 'Plugins'], ['backups', 'Backups'], ['live', 'Live trading'], ...(pr.mode === 'advanced' ? [['audit', 'Audit log'] as [Tab, string]] : [])]
  return (
    <>
      {!admin && <div class="alertbox warn">This device lacks the admin permission: you can view settings but most changes are refused. Pair it with the "full" preset to manage settings.</div>}
      <Tabs tabs={tabs} value={tab} onChange={setTab} />
      {tab === 'general' && <General />}
      {tab === 'data' && <DataSources />}
      {tab === 'devices' && <Devices />}
      {tab === 'secrets' && <Secrets />}
      {tab === 'ai' && <AISettings />}
      {tab === 'mcp' && <MCPSettings />}
      {tab === 'plugins' && <Plugins />}
      {tab === 'backups' && <Backups />}
      {tab === 'live' && <LiveTrading />}
      {tab === 'audit' && <Audit />}
    </>
  )
}

function useSettings() {
  const s = useLoad(() => get('/settings'))
  const save = async (changes: Record<string, unknown>) => {
    try { await put('/settings', changes); s.reload(); toast('Saved', 'ok') } catch (e) { toast(errText(e), 'error') }
  }
  return { s, save }
}

function General() {
  const pr = useStore(prefs)
  const { s, save } = useSettings()
  const dev = useLoad(() => get('/device'))
  const [poll, setPoll] = useState<number | null>(null)
  return (
    <>
    <Card title="General">
      <div class="row">
        <Field label="Theme (this device)"><select value={pr.theme} onChange={(e) => prefs.set((x) => ({ ...x, theme: (e.target as HTMLSelectElement).value as 'dark' }))}><option value="dark">Dark</option><option value="light">Light</option></select></Field>
        <Field label="Interface (this device)"><select value={pr.mode} onChange={(e) => prefs.set((x) => ({ ...x, mode: (e.target as HTMLSelectElement).value as 'beginner' }))}>
          <option value="beginner">Beginner — fewer options, more explanations</option><option value="advanced">Advanced — everything</option></select></Field>
        <label class="row small"><input type="checkbox" checked={pr.sound} onChange={(e) => prefs.set((x) => ({ ...x, sound: (e.target as HTMLInputElement).checked }))} /> sound on notifications (this device)</label>
      </div>
      <ErrorBox error={s.error} />
      {s.data && <div class="row" style="margin-top:10px">
        <Field label="Live price polling interval (seconds, 2–3600)" hint={`lower = fresher prices, more requests to free APIs${s.data['data.poll_seconds'] == null && dev.data ? ` · default for this computer (${dev.data.profile.tier} tier): ${dev.data.profile.poll_seconds} s` : ''}`}>
          <div class="row"><input type="number" min={2} max={3600} style="width:90px" value={poll ?? s.data['data.poll_seconds'] ?? dev.data?.profile?.poll_seconds ?? ''} onInput={(e) => setPoll(Number((e.target as HTMLInputElement).value))} />
            <Btn sm onClick={() => poll !== null && save({ 'data.poll_seconds': poll })}>Save</Btn></div></Field>
        <label class="row small"><input type="checkbox" checked={s.data['notify.desktop'] ?? false} onChange={(e) => save({ 'notify.desktop': (e.target as HTMLInputElement).checked })} /> OS desktop notifications on the host computer</label>
      </div>}
    </Card>
    {can('admin') && <SystemControls />}
    {can('admin') && <Updates />}
    </>
  )
}

function DataSources() {
  const prov = useLoad(() => get<any[]>('/data/providers'))
  const cache = useLoad(() => get<any[]>('/data/cache'))
  const { s, save } = useSettings()
  const [contact, setContact] = useState('')
  const [fred, setFred] = useState('')
  const [csv, setCsv] = useState<{ name: string; timeframe: string; text: string }>({ name: '', timeframe: '1d', text: '' })
  useEffect(() => { if (s.data) setContact(s.data['sec.contact'] || '') }, [s.data])
  return (
    <>
      <Card title="Market data providers">
        <p class="small muted">All providers are free public endpoints. Their status label says how fresh the data is; nothing is ever relabelled as live.</p>
        <ErrorBox error={prov.error} />
        <div class="tablewrap"><table class="t">
          <thead><tr><th>Provider</th><th>Markets</th><th>Freshness</th><th>Key</th><th>Terms / limits</th><th>On</th></tr></thead>
          <tbody>{(prov.data || []).map((p) => <tr>
            <td><b>{p.display_name}</b><div class="small muted">{p.name}</div></td><td class="small">{p.markets.join(', ')}</td>
            <td><StatusBadge status={p.live_status} /></td><td class="small">{p.requires_key ? 'needs key' : p.works_without_account ? 'none' : 'account'}</td>
            <td class="small" style="max-width:360px">{p.terms_note} <span class="muted">{p.rate_limit_note}</span> {p.homepage && <a href={p.homepage} target="_blank" rel="noopener noreferrer">site</a>}</td>
            <td><input type="checkbox" checked={p.enabled} aria-label={`enable ${p.name}`} onChange={async (e) => {
              try { prov.reload(); await put('/data/providers', { [p.name]: { enabled: (e.target as HTMLInputElement).checked } }); prov.reload() } catch (err) { toast(errText(err), 'error') }
            }} /></td></tr>)}</tbody>
        </table></div>
      </Card>
      <Card title="Credentials some free sources ask for">
        <Field label="SEC EDGAR contact e-mail (required by the SEC for fundamentals; stays on this computer, sent only to sec.gov)">
          <div class="row"><input type="email" style="width:280px" value={contact} onInput={(e) => setContact((e.target as HTMLInputElement).value)} /><Btn sm onClick={() => save({ 'sec.contact': contact })}>Save</Btn></div></Field>
        <Field label="FRED API key (optional, free; only for the upcoming-release calendar). Stored encrypted in the secret store.">
          <div class="row"><input type="password" autocomplete="off" style="width:280px" value={fred} onInput={(e) => setFred((e.target as HTMLInputElement).value)} />
            <Btn sm onClick={async () => { try { await put('/secrets/fred.api_key', { value: fred }); setFred(''); toast('FRED key stored', 'ok') } catch (e) { toast(errText(e), 'error') } }}>Store</Btn></div></Field>
      </Card>
      <Card title="Import your own data (CSV)">
        <p class="small muted">Columns: time (ISO date or unix seconds/ms), open, high, low, close, volume. Imported data is labelled HISTORICAL and appears as CSV:&lt;name&gt;.</p>
        <div class="row">
          <Field label="Name"><input value={csv.name} onInput={(e) => setCsv({ ...csv, name: (e.target as HTMLInputElement).value })} placeholder="MYFUND" /></Field>
          <Field label="Timeframe"><select value={csv.timeframe} onChange={(e) => setCsv({ ...csv, timeframe: (e.target as HTMLSelectElement).value })}>{['1m', '5m', '15m', '1h', '4h', '1d', '1w'].map((t) => <option>{t}</option>)}</select></Field>
          <Field label="File"><input type="file" accept=".csv,text/csv" onChange={async (e) => { const f = (e.target as HTMLInputElement).files?.[0]; if (f) setCsv({ ...csv, text: await f.text(), name: csv.name || f.name.replace(/\.csv$/i, '') }) }} /></Field>
          <Btn kind="primary" disabled={!csv.text || !csv.name} onClick={async () => {
            try { const r = await post('/data/import_csv', { name: csv.name, timeframe: csv.timeframe, csv: csv.text }); toast(`Imported ${r.imported} bars as ${r.instrument.id}`, 'ok'); cache.reload() } catch (e) { toast(errText(e), 'error') }
          }}>Import</Btn>
        </div>
      </Card>
      <Card title="Local candle cache" actions={<Btn sm kind="ghost" onClick={cache.reload}>Refresh</Btn>}>
        <div class="tablewrap" style="max-height:300px;overflow:auto"><table class="t">
          <thead><tr><th>Instrument</th><th>TF</th><th class="right">Bars</th><th>From</th><th>To</th><th>Last fetch</th></tr></thead>
          <tbody>{(cache.data || []).map((c) => <tr><td class="mono small">{c.instrument_id}</td><td>{c.timeframe}</td><td class="right">{c.bars}</td>
            <td class="small">{fmtTime(c.first_ts)}</td><td class="small">{fmtTime(c.last_ts)}</td><td class="small">{fmtTime(c.fetched_at)}</td></tr>)}</tbody>
        </table></div>
      </Card>
    </>
  )
}

/** QR code as an SVG (scanned by the Android app to pair: addresses, one-time code, certificate). */
function QR(p: { text: string; size?: number }) {
  const q = qrcode(0, 'M')
  q.addData(p.text)
  q.make()
  const n = q.getModuleCount(), cell = Math.floor((p.size || 220) / (n + 8))
  const dim = cell * (n + 8)
  let path = ''
  for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (q.isDark(r, c)) path += `M${(c + 4) * cell},${(r + 4) * cell}h${cell}v${cell}h-${cell}z`
  return <svg width={dim} height={dim} viewBox={`0 0 ${dim} ${dim}`} role="img" aria-label="Pairing QR code" style="background:#fff;border-radius:8px">
    <path d={path} fill="#000" /></svg>
}

function Devices() {
  const meta = useLoad(() => get('/system/meta'))
  const lan = useLoad(() => get('/system/lan'))
  const tokens = useLoad(() => get<any[]>('/auth/tokens'))
  const [preset, setPreset] = useState('paper-control')
  const [label, setLabel] = useState('My phone')
  const [code, setCode] = useState<any>(null)
  const [now, setNow] = useState(Date.now())
  const [switching, setSwitching] = useState(false)
  useEffect(() => {
    if (!code) return
    const t = setInterval(() => setNow(Date.now()), 500)
    return () => clearInterval(t)
  }, [code])
  const left = code ? Math.max(0, Math.round((code.expiresAt - now) / 1000)) : 0
  const l = lan.data
  const toggle = async (enabled: boolean) => {
    setSwitching(true)
    try {
      const r = await put('/system/lan', { enabled })
      if (r.restarting) {
        toast(enabled ? 'Turning on network access; the app restarts…' : 'Turning off network access; the app restarts…', 'info', 6000)
        setTimeout(() => { const t = setInterval(async () => { try { if ((await fetch('/api/health')).ok) { clearInterval(t); location.reload() } } catch { /* restarting */ } }, 1000) }, 2500)
      } else { lan.reload(); setSwitching(false) }
    } catch (e) { toast(errText(e), 'error'); setSwitching(false) }
  }
  return (
    <>
      <Card title="Pair a phone, tablet or another computer">
        {l && <label class="row"><input type="checkbox" checked={l.enabled} disabled={switching || !can('admin')} onChange={(e) => toggle((e.target as HTMLInputElement).checked)} />
          <span>Allow devices on my network (Wi-Fi or cable) to connect
            <span class="small muted"> — encrypted with this computer's own certificate; each device needs a one-time code. There is no cloud relay.</span></span></label>}
        {l && !l.enabled && <p class="small muted">Codes also work for another browser on this computer. Phones and tablets need the switch above.</p>}
        {l?.enabled && !l.active && <div class="alertbox warn">Network access is switched on but not running yet{switching ? ' — restarting…' : '. Restart the app (Settings → General).'}</div>}
        {l?.active && <div class="alertbox ok small">
          <div>Address{l.urls.length > 1 ? 'es' : ''} for other devices: {l.urls.length ? l.urls.map((u: string) => <code style="margin-right:8px">{u}</code>) : 'no network connection found'}</div>
          {l.tls && <div>Certificate check: <b class="mono">{l.fingerprint_short}</b> — a device that shows a different value is not talking to this computer.</div>}
          {!l.tls && <div class="neg">Encryption is off (lan_tls = false): sign-in tokens cross the network unencrypted.</div>}
        </div>}
        <div class="row" style="margin-top:8px">
          <Field label="Permissions"><select value={preset} onChange={(e) => setPreset((e.target as HTMLSelectElement).value)}>
            {Object.entries(meta.data?.pairing_presets || {}).map(([k, v]) => <option value={k}>{k}: {(v as string[]).join(', ')}</option>)}</select></Field>
          <Field label="Device label"><input value={label} onInput={(e) => setLabel((e.target as HTMLInputElement).value)} /></Field>
          <Btn kind="primary" onClick={async () => { try { const r = await post('/auth/pairing', { preset, label }); setNow(Date.now()); setCode({ ...r, expiresAt: Date.now() + r.expires_in_s * 1000 }); tokens.reload() } catch (e) { toast(errText(e), 'error') } }}>Create one-time code</Btn>
        </div>
        {code && <div class="alertbox ok" style="margin-top:8px">
          <div class="row" style="align-items:flex-start;gap:16px">
            {left > 0 && code.link && <QR text={code.link} />}
            <div class="stack">
              <div style="font-size:28px;letter-spacing:4px" class="mono">{left > 0 ? code.code : '— expired —'}</div>
              <div class="small">{left > 0 ? `valid for ${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}, one use only` : 'create a new code'} · grants: {code.scopes.join(', ')}</div>
              {left > 0 && <ol class="small">
                <li><b>Android app:</b> open Open Market OS on the phone and tap <i>Scan pairing code</i>, then point it at this square.</li>
                <li><b>Any browser:</b> open {code.lan?.urls?.[0] ? <code>{code.lan.urls[0]}</code> : "this computer's address"}, accept the certificate warning
                  {code.lan?.fingerprint_short ? <> after checking that the certificate's SHA-256 fingerprint starts with <b class="mono">{code.lan.fingerprint_short}</b></> : null}, and type the code.</li>
              </ol>}
            </div>
          </div>
        </div>}
      </Card>
      <Card title="Signed-in devices and tokens" actions={<Btn sm kind="ghost" onClick={tokens.reload}>Refresh</Btn>}>
        <ErrorBox error={tokens.error} />
        <div class="tablewrap"><table class="t">
          <thead><tr><th>#</th><th>Name</th><th>Kind</th><th>Permissions</th><th>Created</th><th>Last used</th><th>Expires</th><th /></tr></thead>
          <tbody>{(tokens.data || []).map((t) => <tr style={t.revoked_at ? 'opacity:.5' : ''}>
            <td>{t.id}</td><td>{t.name}</td><td>{t.kind}</td><td class="small">{t.scopes.join(', ')}</td><td class="small">{fmtTime(t.created_at)}</td>
            <td class="small">{fmtTime(t.last_used_at)}</td><td class="small">{fmtTime(t.expires_at)}</td>
            <td>{t.revoked_at ? <span class="small muted">revoked</span> : <Btn sm kind="ghost" onClick={async () => { if (confirm(`Revoke "${t.name}"? That device is signed out immediately.`)) { try { await del(`/auth/tokens/${t.id}`); tokens.reload() } catch (e) { toast(errText(e), 'error') } } }}>Revoke</Btn>}</td>
          </tr>)}</tbody>
        </table></div>
      </Card>
    </>
  )
}

function Secrets() {
  const s = useLoad(() => get('/secrets'))
  const [pass, setPass] = useState('')
  const [name, setName] = useState('')
  const [value, setValue] = useState('')
  const st = s.data?.status
  return (
    <Card title="Secret store">
      <ErrorBox error={s.error} />
      {st && <p class="small">Backend: <b>{st.backend === 'keyring' ? 'operating-system keychain' : 'encrypted vault file (AES-256-GCM, key derived with scrypt)'}</b> ·
        {st.unlocked ? <Badge kind="ok">unlocked</Badge> : <Badge kind="error">locked</Badge>} · {st.count} secret(s). Values are never shown again and are never sent to AI models.</p>}
      {st && !st.unlocked && <div class="row">
        <Field label="Vault passphrase"><input type="password" autocomplete="current-password" value={pass} onInput={(e) => setPass((e.target as HTMLInputElement).value)} /></Field>
        <Btn kind="primary" onClick={async () => { try { await post('/secrets/unlock', { passphrase: pass }); setPass(''); s.reload() } catch (e) { toast(errText(e), 'error') } }}>Unlock</Btn>
      </div>}
      <table class="t"><tbody>{(s.data?.names || []).map((n: string) => <tr><td class="mono">{n}</td><td class="muted">••••••••</td>
        <td><Btn sm kind="ghost" onClick={async () => { if (confirm(`Delete secret ${n}?`)) { await del(`/secrets/${n}`); s.reload() } }}>Delete</Btn></td></tr>)}</tbody></table>
      <div class="row" style="margin-top:8px">
        <Field label="Name (lowercase, e.g. fred.api_key)"><input value={name} onInput={(e) => setName((e.target as HTMLInputElement).value)} /></Field>
        <Field label="Value"><input type="password" autocomplete="off" value={value} onInput={(e) => setValue((e.target as HTMLInputElement).value)} /></Field>
        <Btn onClick={async () => { try { await put(`/secrets/${encodeURIComponent(name)}`, { value }); setValue(''); setName(''); s.reload(); toast('Stored', 'ok') } catch (e) { toast(errText(e), 'error') } }}>Store</Btn>
      </div>
    </Card>
  )
}

function AISettings() {
  const presets = useLoad(() => get<Record<string, any>>('/ai/presets'))
  const provs = useLoad(() => get<any[]>('/ai/providers'))
  const { s, save } = useSettings()
  const [f, setF] = useState<any>({ preset: 'ollama', name: 'Local Ollama', base_url: '', model: '', api_key: '', price_in: 0, price_out: 0 })
  const [ack, setAck] = useState<any>(null)
  const [budget, setBudget] = useState<number | null>(null)
  const pre = presets.data?.[f.preset]
  const levels: string[] = s.data?.['ai.tool_levels'] || ['read', 'analyze', 'propose']
  const toggle = (lv: string) => save({ 'ai.tool_levels': levels.includes(lv) ? levels.filter((x) => x !== lv) : [...levels, lv] })
  return (
    <>
      {can('admin') && <Card title="Free local AI (runs on this computer)"><LocalAIPanel onReady={() => provs.reload()} /></Card>}
      <Card title="AI providers (optional)">
        <ErrorBox error={provs.error} />
        <table class="t"><tbody>{(provs.data || []).map((p) => <tr>
          <td><b>{p.name}</b> <span class="small muted">{p.kind} · {p.model} · {p.base_url}</span></td>
          <td>{p.is_local ? <Badge kind="ok">local · free</Badge> : <Badge kind="error">hosted · {p.options.cost_note}</Badge>}</td>
          <td>{p.has_key ? <span class="small">key stored</span> : ''}</td>
          <td><Btn sm on={!!p.enabled} onClick={async () => {
            try { await patch(`/ai/providers/${p.id}`, { enabled: !p.enabled }); provs.reload() } catch (e) { if (!p.is_local) setAck(p); else toast(errText(e), 'error') }
          }}>{p.enabled ? 'Enabled' : 'Enable'}</Btn></td>
          <td><Btn sm kind="ghost" onClick={async () => { if (confirm(`Remove ${p.name} and its stored key?`)) { await del(`/ai/providers/${p.id}`); provs.reload() } }}>Remove</Btn></td></tr>)}</tbody></table>
        {provs.data?.length === 0 && <Empty>No providers yet. The free local AI above is the recommended start; hosted models need your own API key.</Empty>}
        <h3>Add a provider</h3>
        <div class="row">
          <Field label="Preset"><select value={f.preset} onChange={(e) => { const k = (e.target as HTMLSelectElement).value; setF({ ...f, preset: k, name: k }) }}>
            {Object.entries(presets.data || {}).map(([k, v]) => <option value={k}>{k} — {(v as any).cost}</option>)}</select></Field>
          <Field label="Name"><input value={f.name} onInput={(e) => setF({ ...f, name: (e.target as HTMLInputElement).value })} /></Field>
          <Field label="Base URL"><input style="width:240px" placeholder={pre?.base_url} value={f.base_url} onInput={(e) => setF({ ...f, base_url: (e.target as HTMLInputElement).value })} /></Field>
          <Field label="Model"><input placeholder={pre?.model} value={f.model} onInput={(e) => setF({ ...f, model: (e.target as HTMLInputElement).value })} /></Field>
          {pre?.needs_key && <Field label="API key (stored encrypted)"><input type="password" autocomplete="off" value={f.api_key} onInput={(e) => setF({ ...f, api_key: (e.target as HTMLInputElement).value })} /></Field>}
          {pre && !pre.is_local && <>
            <Field label="$ per 1M input tokens"><input type="number" step="any" style="width:90px" value={f.price_in} onInput={(e) => setF({ ...f, price_in: Number((e.target as HTMLInputElement).value) })} /></Field>
            <Field label="$ per 1M output tokens"><input type="number" step="any" style="width:90px" value={f.price_out} onInput={(e) => setF({ ...f, price_out: Number((e.target as HTMLInputElement).value) })} /></Field></>}
          <Btn kind="primary" onClick={async () => {
            try { await post('/ai/providers', { ...f, base_url: f.base_url || undefined, model: f.model || undefined, api_key: f.api_key || undefined }); setF({ ...f, api_key: '' }); provs.reload(); toast('Added (disabled until you enable it)', 'ok') } catch (e) { toast(errText(e), 'error') }
          }}>Add</Btn>
        </div>
        {pre && !pre.is_local && <p class="small neg">Hosted providers bill you directly and receive the evidence pack (prices, indicators, headlines) — never your secrets or account credentials.
          Enter their real prices so the daily budget works; the budget blocks calls once today's estimated spend reaches it.</p>}
      </Card>
      {s.data && <Card title="What AI agents may do">
        <div class="row">{(['read', 'analyze', 'propose'] as const).map((lv) => <label class="row small"><input type="checkbox" checked={levels.includes(lv)} onChange={() => toggle(lv)} />
          {lv === 'read' ? 'read market data' : lv === 'analyze' ? 'run analysis/backtests' : 'propose orders (you approve each one)'}</label>)}</div>
        <p class="small muted">There is no level that lets AI place, modify or cancel orders, change settings or read secrets.</p>
        <label class="row small"><input type="checkbox" checked={s.data['ai.failover'] ?? true} onChange={(e) => save({ 'ai.failover': (e.target as HTMLInputElement).checked })} />
          if a provider does not answer, try my other enabled providers (local ones first)</label>
        <label class="row small"><input type="checkbox" checked={s.data['ai.failover_to_hosted'] ?? false} onChange={(e) => save({ 'ai.failover_to_hosted': (e.target as HTMLInputElement).checked })} />
          …including paid hosted providers when a free local one fails (off: a free request never becomes a paid one)</label>
        <Field label="Daily budget for hosted providers (USD)"><div class="row"><input type="number" step="0.1" min={0} style="width:90px" value={budget ?? s.data['ai.daily_budget_usd'] ?? 1}
          onInput={(e) => setBudget(Number((e.target as HTMLInputElement).value))} /><Btn sm onClick={() => budget !== null && save({ 'ai.daily_budget_usd': budget })}>Save</Btn></div></Field>
      </Card>}
      {ack && <Confirm phrase="I ACCEPT THE COST" text={`${ack.name} is a hosted service: ${ack.options.cost_note}. Requests are billed by that company to your account. Enable it?`}
        onClose={() => setAck(null)} onConfirm={async () => { try { await patch(`/ai/providers/${ack.id}`, { enabled: true, acknowledge_cost: true }); provs.reload() } catch (e) { toast(errText(e), 'error') } }} />}
    </>
  )
}

function MCPSettings() {
  const perms = useLoad(() => get<any[]>('/mcp/permissions'))
  const servers = useLoad(() => get<any[]>('/mcp/servers'))
  const [tokenInfo, setTokenInfo] = useState<any>(null)
  const [f, setF] = useState<any>({ name: '', transport: 'stdio', command: '', args: '', url: '', timeout_s: 30 })
  const [call, setCall] = useState<any>(null)
  return (
    <>
      <Card title="Our MCP server: what external AI clients may do" actions={<Btn sm onClick={async () => { try { setTokenInfo(await post('/mcp/token')) } catch (e) { toast(errText(e), 'error') } }}>Create bridge token</Btn>}>
        <p class="small muted">Claude Desktop, Claude Code or any MCP client can connect with <code>omos mcp</code>. The HOST enforces this table on every request; there is no live-trading tool.</p>
        {tokenInfo && <div class="alertbox ok">Token saved to <code>{tokenInfo.saved_to}</code> (readable only by you). Client configuration:<pre>{JSON.stringify(tokenInfo.client_config, null, 2)}</pre></div>}
        <ErrorBox error={perms.error} />
        <table class="t"><thead><tr><th>Tool</th><th>Level</th><th>What it can reach</th><th>Allowed</th></tr></thead>
          <tbody>{(perms.data || []).map((t) => <tr><td class="mono">{t.name}<div class="small muted" style="font-family:var(--font)">{t.description}</div></td>
            <td><Badge kind={t.level === 'read' ? 'ok' : t.level === 'private' ? 'degraded' : 'error'}>{t.level}</Badge></td><td class="mono small">{t.routes.join(', ')}</td>
            <td><input type="checkbox" checked={t.enabled} aria-label={`allow ${t.name}`} onChange={async (e) => { try { await put('/mcp/permissions', { [t.name]: (e.target as HTMLInputElement).checked }); perms.reload() } catch (err) { toast(errText(err), 'error') } }} /></td></tr>)}</tbody></table>
      </Card>
      <Card title="External MCP servers (tools we can call)">
        <p class="small muted">External servers are untrusted: their tools start DISABLED, calls have timeouts and size limits, and results are treated as data. A stdio server runs a program on this computer — only add programs you trust.</p>
        {(servers.data || []).map((sv) => <div class="card" style="background:var(--panel2)">
          <div class="row"><b>{sv.name}</b><span class="small muted mono">{sv.transport === 'stdio' ? `${sv.command} ${sv.args.join(' ')}` : sv.url}</span><span class="sp" />
            <Btn sm onClick={async () => { try { const r = await post(`/mcp/servers/${sv.id}/refresh`); toast(`${r.discovered?.tools?.length ?? 0} tool(s) discovered`, 'ok'); servers.reload() } catch (e) { toast(errText(e), 'error') } }}>Discover tools</Btn>
            <Btn sm kind="ghost" onClick={async () => { if (confirm(`Remove ${sv.name}?`)) { await del(`/mcp/servers/${sv.id}`); servers.reload() } }}>Remove</Btn></div>
          <table class="t small"><tbody>{sv.tools.map((t: any) => <tr><td class="mono">{t.tool_name}</td><td>{t.description}</td>
            <td><input type="checkbox" checked={!!t.enabled} aria-label={`enable ${t.tool_name}`} onChange={async (e) => { await put(`/mcp/servers/${sv.id}/tools/${encodeURIComponent(t.tool_name)}`, { enabled: (e.target as HTMLInputElement).checked }); servers.reload() }} /></td>
            <td>{t.enabled ? <Btn sm onClick={() => setCall({ sid: sv.id, tool: t.tool_name, args: '{}', out: null })}>Call…</Btn> : null}</td></tr>)}</tbody></table>
        </div>)}
        <h3>Add a server</h3>
        <div class="row">
          <Field label="Name"><input value={f.name} onInput={(e) => setF({ ...f, name: (e.target as HTMLInputElement).value })} /></Field>
          <Field label="Transport"><select value={f.transport} onChange={(e) => setF({ ...f, transport: (e.target as HTMLSelectElement).value })}><option value="stdio">stdio (local program)</option><option value="http">HTTP (streamable)</option></select></Field>
          {f.transport === 'stdio' ? <>
            <Field label="Program"><input class="mono" placeholder="npx" value={f.command} onInput={(e) => setF({ ...f, command: (e.target as HTMLInputElement).value })} /></Field>
            <Field label="Arguments (space separated)"><input class="mono" style="width:260px" value={f.args} onInput={(e) => setF({ ...f, args: (e.target as HTMLInputElement).value })} /></Field>
          </> : <Field label="URL (https, or http on localhost)"><input class="mono" style="width:280px" value={f.url} onInput={(e) => setF({ ...f, url: (e.target as HTMLInputElement).value })} /></Field>}
          <Btn kind="primary" onClick={async () => {
            try { await post('/mcp/servers', { ...f, args: f.args.split(' ').filter(Boolean) }); servers.reload(); toast('Added. Press "Discover tools", then enable the ones you want.', 'ok') } catch (e) { toast(errText(e), 'error') }
          }}>Add</Btn>
        </div>
      </Card>
      {call && <Modal title={`Call ${call.tool}`} onClose={() => setCall(null)}>
        <Field label="Arguments (JSON)"><textarea class="mono" style="width:100%;min-height:90px" value={call.args} onInput={(e) => setCall({ ...call, args: (e.target as HTMLTextAreaElement).value })} /></Field>
        <div class="row"><span class="sp" /><Btn kind="primary" onClick={async () => {
          try { setCall({ ...call, out: await post(`/mcp/servers/${call.sid}/call`, { tool: call.tool, args: JSON.parse(call.args) }) }) } catch (e) { setCall({ ...call, out: { error: errText(e) } }) }
        }}>Call</Btn></div>
        {call.out && <pre>{JSON.stringify(call.out, null, 2)}</pre>}
        <p class="small muted">The output comes from an external program and is untrusted.</p>
      </Modal>}
    </>
  )
}

function Plugins() {
  const pl = useLoad(() => get<any[]>('/plugins'))
  const [path, setPath] = useState('')
  const [grant, setGrant] = useState<any>(null)
  return (
    <Card title="Plugins">
      <p class="small muted">Install from a folder on this computer. Files are fingerprinted (SHA-256) at install; a plugin whose files change afterwards is refused.
        "process" plugins run as a separate program with a scrubbed environment and resource limits, but their network access cannot be blocked — install only code you trust.</p>
      <ErrorBox error={pl.error} />
      {(pl.data || []).map((p) => <div class="card" style="background:var(--panel2)">
        <div class="row"><b>{p.name}</b><span class="small muted">{p.id} v{p.version} · {p.kind}</span><Badge kind={p.enabled ? 'ok' : 'HISTORICAL'}>{p.enabled ? 'enabled' : 'disabled'}</Badge><span class="sp" />
          {p.enabled ? <Btn sm onClick={async () => { await post(`/plugins/${p.id}/disable`); pl.reload() }}>Disable</Btn>
            : <Btn sm kind="primary" onClick={() => p.requested_permissions.length ? setGrant(p) : post(`/plugins/${p.id}/enable`, { grant: [] }).then(pl.reload).catch((e) => toast(errText(e), 'error'))}>Enable…</Btn>}
          <Btn sm kind="ghost" onClick={async () => { if (confirm(`Uninstall ${p.name}? Its files are deleted from the plugins folder.`)) { await del(`/plugins/${p.id}`); pl.reload() } }}>Uninstall</Btn></div>
        <div class="small">{p.manifest.description}</div>
        <div class="small muted">Isolation: {p.isolation} · requests: {p.requested_permissions.join(', ') || 'nothing'} · sha256 {String(p.sha256).slice(0, 16)}…</div>
      </div>)}
      {pl.data?.length === 0 && <Empty>No plugins installed. Examples ship in <code>examples/plugins/</code>.</Empty>}
      <div class="row" style="margin-top:8px"><Field label="Plugin folder path on this computer"><input class="mono" style="width:360px" value={path} onInput={(e) => setPath((e.target as HTMLInputElement).value)} /></Field>
        <Btn onClick={async () => { try { const r = await post('/plugins/install', { path }); toast(`Installed ${r.name} (disabled)`, 'ok'); pl.reload() } catch (e) { toast(errText(e), 'error') } }}>Install</Btn></div>
      {grant && <Confirm text={`${grant.name} asks for: ${grant.requested_permissions.join(', ')}. Grant all of them and enable it?`} onClose={() => setGrant(null)}
        onConfirm={async () => { try { await post(`/plugins/${grant.id}/enable`, { grant: grant.requested_permissions }); pl.reload() } catch (e) { toast(errText(e), 'error') } }} />}
    </Card>
  )
}

function Backups() {
  const b = useLoad(() => get<any[]>('/backups'))
  const { s, save } = useSettings()
  const [restore, setRestore] = useState<any>(null)
  return (
    <Card title="Backups and export" actions={<>
      <Btn sm onClick={async () => { try { const r = await post('/backups'); toast(`Backup written: ${r.file}`, 'ok'); b.reload() } catch (e) { toast(errText(e), 'error') } }}>Back up now</Btn>
      <Btn sm onClick={async () => { try { const r = await post('/export'); toast(`Export written to ${r.path}`, 'ok', 12000) } catch (e) { toast(errText(e), 'error') } }}>Export everything (zip)</Btn></>}>
      <p class="small muted">Backups are consistent snapshots of all your data (strategies, accounts, bots, settings) in the data folder. Secrets are NOT included
        in exports: they stay in the keychain or vault. Restoring replaces all current data with the backup; the current data is saved first, so a restore can be undone.</p>
      {s.data && <div class="row">
        <label class="row small"><input type="checkbox" checked={s.data['backup.auto'] ?? true} onChange={(e) => save({ 'backup.auto': (e.target as HTMLInputElement).checked })} /> daily automatic backup</label>
        <Field label="Keep last"><input type="number" min={1} max={365} style="width:70px" value={s.data['backup.keep'] ?? 14} onChange={(e) => save({ 'backup.keep': Number((e.target as HTMLInputElement).value) })} /></Field>
      </div>}
      <table class="t"><tbody>{(b.data || []).map((x) => <tr><td class="small">{fmtTime(x.modified)}</td><td class="small">{x.kind}</td>
        <td class="mono small">{x.file}</td><td class="right">{fmtNum(x.bytes / 1024, 0)} KB</td>
        <td class="right"><Btn sm onClick={() => setRestore(x)}>Restore…</Btn></td></tr>)}</tbody></table>
      {b.data?.length === 0 && <Empty>No backups yet.</Empty>}
      {restore && <Confirm phrase="RESTORE" text={`Replace ALL current data with the backup from ${fmtTime(restore.modified)} (${restore.kind})? Running bots stop
        while the app restarts. Your current data is saved first as a "before a restore" backup, so you can undo this.`}
        onClose={() => setRestore(null)} onConfirm={async () => {
          try {
            const r = await post('/backups/restore', { file: restore.file })
            toast(r.message, 'info', 10000)
            if (r.restarting) setTimeout(() => { const t = setInterval(async () => { try { if ((await fetch('/api/health')).ok) { clearInterval(t); location.reload() } } catch { /* restarting */ } }, 1000) }, 3000)
          } catch (e) { toast(errText(e), 'error') }
        }} />}
    </Card>
  )
}

function LiveTrading() {
  const { s } = useSettings()
  const [confirm, setConfirm] = useState(false)
  const enabled = !!s.data?.['trading.live_enabled']
  return (
    <Card title={<>Live trading {s.data && <Badge kind={enabled ? 'LIVE' : 'PAPER'}>{enabled ? 'switch ON' : 'off (paper only)'}</Badge>}</>}>
      <div class="alertbox error">This release ships <b>no verified live broker adapter</b>. Turning the switch on records your intent and unlocks nothing that can send real orders:
        every account stays PAPER. A live adapter, when added, must also pass the risk engine, require the trade:live permission, a bot approval and this switch.</div>
      <div class="row" style="margin-top:8px"><span class="sp" />
        {enabled ? <Btn onClick={async () => { await post('/settings/live-trading', { enabled: false }); s.reload() }}>Turn off</Btn>
          : <Btn kind="danger" onClick={() => setConfirm(true)}>Turn on…</Btn>}</div>
      {confirm && <Confirm phrase="I UNDERSTAND LIVE TRADING USES REAL MONEY" text="Live trading can lose real money quickly. Only continue if you understand the risks and have tested on paper."
        onClose={() => setConfirm(false)} onConfirm={async () => { try { const r = await post('/settings/live-trading', { enabled: true, confirm: 'I UNDERSTAND LIVE TRADING USES REAL MONEY' }); toast(`Live switch on; verified live brokers: ${r.verified_live_brokers.length || 'none'}`, 'warn'); s.reload() } catch (e) { toast(errText(e), 'error') } }} />}
    </Card>
  )
}

function Audit() {
  const a = useLoad(() => get<any[]>('/audit', { limit: 300 }))
  return (
    <Card title="Audit log (who changed what)" actions={<Btn sm kind="ghost" onClick={a.reload}>Refresh</Btn>}>
      <ErrorBox error={a.error} />
      <div class="tablewrap" style="max-height:520px;overflow:auto"><table class="t">
        <thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Target</th><th>Detail</th></tr></thead>
        <tbody>{(a.data || []).map((r) => <tr><td class="small">{fmtTime(r.ts)}</td><td class="mono small">{r.actor}</td><td class="mono small">{r.action}</td><td class="small">{r.target}</td>
          <td class="mono small" style="word-break:break-all">{r.detail ? JSON.stringify(r.detail).slice(0, 200) : ''}</td></tr>)}</tbody>
      </table></div>
    </Card>
  )
}
