import { useEffect, useState } from 'preact/hooks'
import { del, get, post, put } from '../api'
import { live } from '../api'
import { Badge, Btn, Card, Confirm, ErrorBox, Field, KV, StatusBadge, useLoad } from '../components/ui'
import { errText, toast } from '../store'

type Job = { id: string; kind: string; title: string; status: string; progress: number; message: string; result: any; error: string | null }

/** Follows a background job: live updates over the WebSocket, with polling as a fallback. */
export function useJob(id: string | null): Job | null {
  const [job, setJob] = useState<Job | null>(null)
  useEffect(() => {
    if (!id) { setJob(null); return }
    let alive = true
    const off = live.on((e) => { if (e.topic === 'job' && e.data.id === id && alive) setJob(e.data) })
    const poll = async () => {
      try { const j = await get<Job>(`/jobs/${id}`); if (alive) setJob(j); if (j.status === 'running' && alive) setTimeout(poll, 1500) }
      catch { if (alive) setTimeout(poll, 3000) }
    }
    void poll()
    return () => { alive = false; off() }
  }, [id])
  return job
}

export function Progress(p: { job: Job }) {
  const j = p.job
  return (
    <div class="jobprogress" role="status" aria-live="polite">
      <div class="row"><b>{j.title}</b><span class="sp" />
        <Badge kind={j.status === 'succeeded' ? 'ok' : j.status === 'running' ? 'degraded' : 'down'}>{j.status}</Badge></div>
      <div class="bar" aria-label="progress"><span style={`width:${Math.max(2, j.progress)}%`} /></div>
      <div class="small muted">{j.status === 'failed' || j.status === 'cancelled' ? j.error : j.message}</div>
    </div>
  )
}

const gb = (x?: number) => (x == null ? '—' : `${x.toFixed(1)} GB`)

// ============================================================================ local AI
export function LocalAIPanel(p: { onReady?: () => void; wizard?: boolean }) {
  const s = useLoad(() => get('/ai/local'))
  const [model, setModel] = useState('')
  const [jobId, setJobId] = useState<string | null>(null)
  const job = useJob(jobId)
  useEffect(() => { if (s.data?.running_job && !jobId) setJobId(s.data.running_job.id) }, [s.data])
  useEffect(() => {
    if (!job || job.status === 'running') return
    s.reload()
    if (job.status === 'succeeded') { toast(`${job.title}: done`, 'ok'); if (job.kind === 'local_ai') p.onReady?.() }
  }, [job?.status])
  if (s.error) return <ErrorBox error={s.error} />
  if (!s.data) return <div class="muted">Checking this computer…</div>
  const d = s.data
  const rec = d.recommended
  const chosen = model || rec.model
  const active = d.system || d.managed
  const running = job?.status === 'running'
  return (
    <div class="stack">
      {d.system && <div class="alertbox ok">Found Ollama {d.system.version} already running on this computer — it will be used, nothing extra is downloaded.</div>}
      {!d.system && d.managed_running && <div class="alertbox ok">The local AI runtime (Ollama {d.managed.version}) is running, managed by Open Market OS.</div>}
      {!active && !d.managed_installed && (
        <p class="small">Open Market OS can install a free, private AI that runs entirely on this computer: no account, no API key, no cost,
          and your data never leaves the machine. It downloads the official Ollama runtime
          (about {gb(rec.runtime_download_gb)}, checked against its published SHA-256 checksum) and one model.</p>
      )}
      <KV items={[['Recommended model', rec.model], ['Model download', gb(rec.download_gb)], ['This computer', rec.reason],
        ['Speed', rec.accelerated ? 'GPU-accelerated' : 'CPU only (slower answers)']]} />
      {!rec.fits && <div class="alertbox warn">This computer may not have enough free disk space or memory for local AI. You can still use a hosted provider in Settings → AI.</div>}
      <div class="row wrap">
        <Field label="Model">
          <select value={chosen} onChange={(e) => setModel((e.target as HTMLSelectElement).value)} disabled={running}>
            {rec.options.map((o: any) => <option value={o.model}>{o.model} — {gb(o.download_gb)}, needs ~{o.min_ram_gb} GB RAM{o.model === rec.model ? ' (recommended)' : ''}</option>)}
          </select>
        </Field>
        <span class="sp" />
        <Btn kind="primary" disabled={running} onClick={async () => {
          try { const j = await post<Job>('/ai/local/setup', { model: chosen }); setJobId(j.id) } catch (e) { toast(errText(e), 'error') }
        }}>{active ? (active.models.includes(chosen) ? 'Use this model' : 'Download and use') : 'Set up free local AI'}</Btn>
      </div>
      {job && <Progress job={job} />}
      {running && <Btn sm kind="ghost" onClick={() => post(`/jobs/${job!.id}/cancel`).catch((e) => toast(errText(e), 'error'))}>Cancel download</Btn>}
      {!p.wizard && active && (
        <Card title="Installed models">
          {active.models.length === 0 && <p class="small muted">No models yet.</p>}
          <table class="t"><tbody>{active.models.map((m: string) => (
            <tr><td class="mono">{m}</td><td class="right">
              <Btn sm kind="danger" disabled={running} onClick={async () => {
                try { await del(`/ai/local/models/${encodeURIComponent(m)}`); s.reload(); toast(`Removed ${m}`, 'ok') } catch (e) { toast(errText(e), 'error') }
              }}>Remove</Btn></td></tr>))}</tbody></table>
        </Card>
      )}
      {!p.wizard && !d.system && d.managed_installed && (
        <div class="row">
          <span class="small muted">Runtime {d.runtime_version || ''} · models stored in <code>{d.models_dir}</code></span><span class="sp" />
          {d.managed_running
            ? <Btn sm onClick={async () => { await post('/ai/local/stop').catch((e) => toast(errText(e), 'error')); s.reload() }}>Stop local AI</Btn>
            : <Btn sm onClick={async () => { await post('/ai/local/start').catch((e) => toast(errText(e), 'error')); s.reload() }}>Start local AI</Btn>}
        </div>
      )}
    </div>
  )
}

// ============================================================================ quit / restart / login / power
function waitForHost(then: () => void) {
  let tries = 0
  const t = setInterval(async () => {
    tries++
    try { const r = await fetch('/api/health', { cache: 'no-store' }); if (r.ok) { clearInterval(t); then() } } catch { /* still restarting */ }
    if (tries > 120) clearInterval(t)
  }, 1000)
}

export function SystemControls() {
  const auto = useLoad(() => get('/system/autostart'))
  const power = useLoad(() => get('/system/power'))
  const [confirm, setConfirm] = useState<null | 'quit' | 'restart'>(null)
  const [stopped, setStopped] = useState<'' | 'quit' | 'restart'>('')
  if (stopped === 'quit') return (
    <div class="alertbox">Open Market OS has stopped. Bots, alerts and automations are paused until you open it again from
      your Start menu, Applications folder or app launcher.</div>)
  if (stopped === 'restart') return <div class="alertbox">Restarting… this page reloads by itself when the app is back.</div>
  return (
    <Card title="This app on this computer">
      <div class="stack">
        <label class="row"><input type="checkbox" checked={!!auto.data?.enabled} disabled={!auto.data?.supported}
          onChange={async (e) => {
            try { await put('/system/autostart', { enabled: (e.target as HTMLInputElement).checked }); auto.reload(); toast('Saved', 'ok') } catch (err) { toast(errText(err), 'error') }
          }} />
          <span>Start Open Market OS in the background when I sign in to this computer
            <span class="small muted"> ({auto.data?.method || 'checking…'}) — bots and alerts keep running without opening a window</span></span></label>
        <label class="row"><input type="checkbox" checked={!!power.data?.enabled}
          onChange={async (e) => {
            try { await put('/system/power', { keep_awake: (e.target as HTMLInputElement).checked }); power.reload() } catch (err) { toast(errText(err), 'error') }
          }} />
          <span>Keep this computer awake while bots are running
            <span class="small muted"> (the screen may still turn off) — {power.data ? (power.data.active ? `active now (${power.data.method})`
              : power.data.method === 'unsupported' ? 'not supported on this system' : `inactive: ${power.data.running_bots} bot(s) running`) : '…'}</span></span></label>
        <div class="row">
          <Btn onClick={() => setConfirm('restart')}>Restart</Btn>
          <Btn kind="danger" onClick={() => setConfirm('quit')}>Quit Open Market OS</Btn>
          <span class="sp" />
          <Btn sm kind="ghost" onClick={async () => { await post('/setup/reset'); location.reload() }}>Run the setup assistant again</Btn>
        </div>
      </div>
      {confirm && <Confirm text={confirm === 'quit'
        ? `Quit the app? ${power.data?.running_bots ? `${power.data.running_bots} running bot(s) and all` : 'All'} alerts and automations stop until you open it again.`
        : 'Restart the app? Bots resume automatically after the restart.'}
        onClose={() => setConfirm(null)}
        onConfirm={async () => {
          try {
            await post(confirm === 'quit' ? '/system/shutdown' : '/system/restart')
            setStopped(confirm)
            if (confirm === 'restart') setTimeout(() => waitForHost(() => location.reload()), 2500)
          } catch (e) { toast(errText(e), 'error') }
        }} />}
    </Card>
  )
}

// ============================================================================ first-run wizard
const STEPS = ['Welcome', 'Market data', 'Local AI', 'Background', 'Done'] as const

export function SetupWizard(p: { onDone: () => void }) {
  const [step, setStep] = useState(0)
  const st = useLoad(() => get('/setup'))
  const [sources, setSources] = useState<any>(null)
  const [checking, setChecking] = useState(false)
  const [autostart, setAutostart] = useState(true)
  const [keepAwake, setKeepAwake] = useState(true)
  const [err, setErr] = useState<unknown>(null)
  useEffect(() => {
    if (step !== 1 || sources || checking) return
    setChecking(true)
    post('/setup/check_sources').then(setSources).catch(setErr).finally(() => setChecking(false))
  }, [step])
  useEffect(() => { if (st.data) setKeepAwake(st.data.keep_awake) }, [st.data])
  const finish = async (opts: Record<string, unknown>) => {
    try { await post('/setup/complete', opts); p.onDone() } catch (e) { setErr(e) }
  }
  const sys = st.data?.system
  return (
    <div class="wizard">
      <div class="card wizcard">
        <div class="row"><b>◆ Open Market OS — setup</b><span class="sp" />
          <Btn sm kind="ghost" onClick={() => finish({})}>Skip setup</Btn></div>
        <ol class="steps">{STEPS.map((s, i) => <li class={i === step ? 'on' : i < step ? 'done' : ''}>{s}</li>)}</ol>
        <ErrorBox error={err || st.error} />
        {step === 0 && (
          <div class="stack">
            <h2>Welcome</h2>
            <p>Open Market OS is a market workstation that runs on this computer: charts, research, strategy testing, paper trading and bots.
              It is free, works without an account, and keeps your data here.</p>
            <div class="alertbox">Everything starts in <b>paper trading</b> with simulated money. Real-money trading is off and needs several deliberate steps to turn on.</div>
            {sys ? <KV items={[['System', `${sys.os} (${sys.machine})`], ['Processor threads', sys.cpu_count], ['Memory', gb(sys.ram_total_gb)],
              ['Free disk', gb(sys.disk_free_gb)], ['Graphics', sys.gpu?.name || (sys.gpu?.apple_silicon ? 'Apple Silicon' : sys.gpu?.nvidia ? 'NVIDIA' : 'integrated / none')],
              ['Performance profile', sys.tier], ['Price refresh', `every ${sys.poll_seconds} s`], ['Data folder', <code class="small">{sys.data_dir}</code>]]} />
              : <p class="muted">Detecting this computer…</p>}
            <p class="small muted">Settings were chosen for this computer's performance profile; you can change them later in Settings.</p>
          </div>
        )}
        {step === 1 && (
          <div class="stack">
            <h2>Market data</h2>
            <p class="small">Free public sources are checked from this computer. Each price in the app carries a label saying how current it is.</p>
            {checking && <p class="muted">Checking sources (a few seconds)…</p>}
            {sources && (
              <table class="t"><thead><tr><th>Source</th><th>Result</th><th>Label</th><th class="right">Time</th></tr></thead>
                <tbody>{sources.results.map((r: any) => (
                  <tr><td>{r.label}</td><td>{r.ok ? <Badge kind="ok">reachable</Badge> : <Badge kind="down">{r.status === 'DISABLED' ? 'off' : 'unavailable'}</Badge>}
                    <div class="small muted">{r.ok ? '' : r.detail}</div></td>
                    <td>{r.ok ? <StatusBadge status={r.status} /> : '—'}</td><td class="right small">{r.latency_ms} ms</td></tr>))}</tbody></table>
            )}
            {sources && sources.ok === 0 && <div class="alertbox warn">No source answered. Check this computer's internet connection or firewall; you can continue and the app retries by itself.</div>}
            {sources && sources.ok > 0 && sources.ok < sources.total && <p class="small muted">Unavailable sources are retried automatically; some are blocked in certain countries or networks.</p>}
            {sources && <Btn sm kind="ghost" onClick={() => { setSources(null); setStep(1); setChecking(true); post('/setup/check_sources').then(setSources).catch(setErr).finally(() => setChecking(false)) }}>Check again</Btn>}
          </div>
        )}
        {step === 2 && (
          <div class="stack">
            <h2>Local AI (optional)</h2>
            <LocalAIPanel wizard onReady={() => toast('Local AI is ready', 'ok')} />
            <p class="small muted">You can skip this and set it up later in Settings → AI, or use a hosted model (Claude, OpenAI, Gemini and others) with your own API key.
              The download keeps going if you continue.</p>
          </div>
        )}
        {step === 3 && (
          <div class="stack">
            <h2>Running in the background</h2>
            <label class="row"><input type="checkbox" checked={autostart} disabled={st.data && !st.data.autostart?.supported} onChange={(e) => setAutostart((e.target as HTMLInputElement).checked)} />
              <span>Start Open Market OS when I sign in to this computer, so bots and alerts keep running without opening a window</span></label>
            <label class="row"><input type="checkbox" checked={keepAwake} onChange={(e) => setKeepAwake((e.target as HTMLInputElement).checked)} />
              <span>Keep this computer awake while bots are running (the screen may still turn off)</span></label>
            <div class="alertbox">Bots only run while this computer is on, awake and online. On a laptop, keep it plugged in. You can change both options any time in Settings → General.</div>
          </div>
        )}
        {step === 4 && (
          <div class="stack">
            <h2>Ready</h2>
            <p>Suggested first steps:</p>
            <ol class="small">
              <li>Open <b>Charts</b> and pick an instrument from the search box.</li>
              <li>Try a template in the <b>Strategy lab</b> and run a backtest with walk-forward testing.</li>
              <li>Place a paper order, then create a <b>bot</b>: Validate → Backtest → Start.</li>
              <li>Find the red <b>emergency stop</b> on the Risk page.</li>
            </ol>
          </div>
        )}
        <div class="row" style="margin-top:16px">
          {step > 0 && <Btn onClick={() => setStep(step - 1)}>Back</Btn>}
          <span class="sp" />
          {step < 4 && <Btn kind="primary" onClick={() => setStep(step + 1)}>{step === 2 ? 'Continue' : 'Next'}</Btn>}
          {step === 4 && <Btn kind="primary" onClick={() => finish({ autostart, keep_awake: keepAwake })}>Start using Open Market OS</Btn>}
        </div>
      </div>
    </div>
  )
}

// ============================================================================ updates
export function Updates() {
  const [res, setRes] = useState<any>(null)
  const [err, setErr] = useState<unknown>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const job = useJob(jobId)
  const check = async () => { setErr(null); try { setRes(await get('/updates/check')) } catch (e) { setErr(e) } }
  useEffect(() => {
    if (job?.status === 'succeeded' && job.result?.how === 'restart') setTimeout(() => waitForHost(() => location.reload()), 3000)
  }, [job?.status])
  return (
    <Card title="Updates">
      <div class="row"><span>This is version <b>{res?.current || '…'}</b>.</span><span class="sp" />
        <Btn sm onClick={check}>Check for updates</Btn></div>
      <ErrorBox error={err} />
      {res && !res.available && <p class="small muted">{res.message || `You have the newest published version${res.latest ? ` (${res.latest})` : ''}.`}</p>}
      {res?.available && <div class="stack">
        <div class="alertbox ok">Version <b>{res.latest}</b> is available{res.published_at ? ` (published ${new Date(res.published_at).toLocaleDateString()})` : ''}:
          {' '}{res.asset.name}, {(res.asset.size / 1e6).toFixed(0)} MB.</div>
        {res.notes && <details><summary class="small">What's new</summary><pre class="small" style="white-space:pre-wrap">{res.notes}</pre></details>}
        <p class="small muted">The download is checked against the release's published SHA-256 checksums, and all your data is backed up first
          ("before an update" in Settings → Backups). To go back, restore that backup and install the previous version from the release page.</p>
        <div class="row"><Btn kind="primary" disabled={job?.status === 'running'} onClick={async () => {
          try { setJobId((await post('/updates/install', {})).id) } catch (e) { toast(errText(e), 'error') }
        }}>Download, verify and install</Btn>{res.page && <a class="small" href={res.page} target="_blank" rel="noopener noreferrer">Release page</a>}</div>
      </div>}
      {job && <Progress job={job} />}
      {job?.status === 'succeeded' && <div class="alertbox">{job.result?.message}</div>}
    </Card>
  )
}
