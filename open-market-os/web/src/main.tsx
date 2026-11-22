import { render } from 'preact'
import { useEffect, useState } from 'preact/hooks'
import './styles.css'
import { exchangeCode, get, getToken, live, setToken } from './api'
import { Btn, Field, Toasts } from './components/ui'
import { setDeviceTier } from './chart/adapter'
import { errText, me, prefs, toast, useStore } from './store'
import { Dashboard } from './views/Dashboard'
import { Charts } from './views/Charts'
import { Watchlists } from './views/Watchlists'
import { Screener } from './views/Screener'
import { Research } from './views/Research'
import { StrategyLab } from './views/StrategyLab'
import { Bots } from './views/Bots'
import { Portfolio } from './views/Portfolio'
import { Risk } from './views/Risk'
import { Alerts } from './views/Alerts'
import { AIWorkspace } from './views/AI'
import { Automation } from './views/Automation'
import { Settings } from './views/Settings'
import { Status } from './views/Status'
import { SetupWizard } from './views/Setup'

type Route = { path: string; label: string; icon: string; view: () => any; advanced?: boolean; section?: string; mobile?: boolean }
const ROUTES: Route[] = [
  { path: '/', label: 'Dashboard', icon: '◧', view: Dashboard, section: 'Markets', mobile: true },
  { path: '/charts', label: 'Charts', icon: '📈', view: Charts, mobile: true },
  { path: '/watchlists', label: 'Watchlists', icon: '★', view: Watchlists, mobile: true },
  { path: '/screener', label: 'Screener', icon: '⌕', view: Screener },
  { path: '/research', label: 'News & research', icon: '📰', view: Research },
  { path: '/strategy', label: 'Strategy lab', icon: '⚗', view: StrategyLab, section: 'Trading' },
  { path: '/bots', label: 'Bots', icon: '🤖', view: Bots, mobile: true },
  { path: '/portfolio', label: 'Portfolio', icon: '💼', view: Portfolio },
  { path: '/risk', label: 'Risk', icon: '🛡', view: Risk },
  { path: '/alerts', label: 'Alerts', icon: '🔔', view: Alerts, mobile: true },
  { path: '/ai', label: 'AI workspace', icon: '✦', view: AIWorkspace, section: 'Intelligence' },
  { path: '/automation', label: 'Automation', icon: '⚙', view: Automation, advanced: true },
  { path: '/settings', label: 'Settings', icon: '☰', view: Settings, section: 'System' },
  { path: '/status', label: 'System status', icon: '◉', view: Status },
]

function navigate(path: string) {
  history.pushState({}, '', path)
  window.dispatchEvent(new PopStateEvent('popstate'))
}
;(window as any).omosNavigate = navigate

function useRoute(): string {
  const [p, setP] = useState(location.pathname)
  useEffect(() => {
    const f = () => setP(location.pathname)
    window.addEventListener('popstate', f)
    return () => window.removeEventListener('popstate', f)
  }, [])
  return p
}

function Link(p: { href: string; children: any; onClick?: () => void }) {
  const cur = useRoute()
  return (
    <a href={p.href} class={cur === p.href ? 'active' : ''}
      onClick={(e) => { e.preventDefault(); navigate(p.href); p.onClick?.() }}>{p.children}</a>
  )
}

function SignIn(p: { onDone: () => void }) {
  const [code, setCode] = useState('')
  const [err, setErr] = useState('')
  return (
    <div style="display:flex;align-items:center;justify-content:center;min-height:100%;padding:16px">
      <div class="card" style="max-width:460px;width:100%">
        <h2>Sign in to Open Market OS</h2>
        <p class="muted">This app runs on your own computer. On that computer, open <b>Open Market OS</b> from the Start menu,
          Applications folder or app launcher: it signs you in automatically.
          On a phone or tablet, ask for a <b>pairing code</b> in Settings → Devices on the computer and type it here.</p>
        <Field label="Pairing or sign-in code">
          <input value={code} autocomplete="one-time-code" onInput={(e) => setCode((e.target as HTMLInputElement).value)} placeholder="e.g. 7K9MXQ2P" />
        </Field>
        {err && <div class="alertbox error" style="margin-top:8px">{err}</div>}
        <div class="row" style="margin-top:12px"><span class="sp" />
          <Btn kind="primary" onClick={async () => {
            try { await exchangeCode(code); setErr(''); p.onDone() } catch (e) { setErr(errText(e)) }
          }}>Sign in</Btn>
        </div>
      </div>
    </div>
  )
}

function App() {
  const route = useRoute()
  const pr = useStore(prefs)
  const who = useStore(me)
  const [authed, setAuthed] = useState<boolean | null>(null)
  const [menu, setMenu] = useState(false)
  const [online, setOnline] = useState(false)
  const [setup, setSetup] = useState(false)

  useEffect(() => { document.documentElement.dataset.theme = pr.theme }, [pr.theme])

  const boot = async () => {
    const t = /token=(omos_[A-Za-z0-9_-]+)/.exec(location.hash)
    if (t) {  // handed over by the Android app, which signed this device in with a pairing code
      setToken(t[1])
      history.replaceState({}, '', location.pathname)
    }
    const m = /code=([A-Za-z0-9-]+)/.exec(location.hash)
    if (m) {
      try { await exchangeCode(m[1]); toast('Signed in on this device', 'ok') } catch (e) { toast(errText(e), 'error') }
      history.replaceState({}, '', location.pathname)
    }
    if (!getToken()) { setAuthed(false); return }
    try {
      const who = await get('/auth/me')
      me.set(who)
      if (who.scopes.includes('admin')) {
        // first run on this computer: show the setup assistant (phones and limited devices never see it)
        get('/setup').then((st) => setSetup(!st.completed)).catch(() => {})
      }
      setAuthed(true)
      live.connect()
      get('/device').then((d) => setDeviceTier(d.profile.tier)).catch(() => {})
      if (!prefs.get().accountId) {
        const accts = await get<any[]>('/accounts')
        if (accts.length) prefs.set((x) => ({ ...x, accountId: accts[0].id }))
      }
    } catch { setToken(null); setAuthed(false) }
  }

  useEffect(() => {
    void boot()
    const onAuth = () => { setToken(null); setAuthed(false) }
    window.addEventListener('omos:auth-required', onAuth)
    // a sign-in link opened in a tab that is already showing the app only changes the #hash
    const onHash = () => { if (/(code|token)=/.test(location.hash)) void boot() }
    window.addEventListener('hashchange', onHash)
    live.statusListeners.add(setOnline)
    const off = live.on((e) => {
      if (e.topic === 'notification') {
        toast(`${e.data.title}${e.data.body ? ' — ' + e.data.body : ''}`, e.data.level === 'warning' ? 'warn' : 'info', 8000)
        if (prefs.get().sound && e.data?.data?.sound !== false) beep()
        if ('Notification' in window && Notification.permission === 'granted') new Notification(e.data.title, { body: e.data.body })
      }
      if (e.topic === 'risk' && e.data?.breach) toast(`Risk limit: ${e.data.breach} — trading halted`, 'error', 15000)
      if (e.topic === 'proposal') toast('An AI/automation order proposal is waiting for your approval (Portfolio → Proposals)', 'warn', 10000)
    })
    if ('serviceWorker' in navigator && location.protocol !== 'file:') navigator.serviceWorker.register('/sw.js').catch(() => {})
    return () => { window.removeEventListener('omos:auth-required', onAuth); window.removeEventListener('hashchange', onHash); live.statusListeners.delete(setOnline); off() }
  }, [])

  if (authed === null) return <div class="muted" style="padding:24px">Loading…</div>
  if (!authed) return <><SignIn onDone={() => void boot()} /><Toasts /></>
  if (setup) return <><SetupWizard onDone={() => { setSetup(false); navigate('/') }} /><Toasts /></>

  const visible = ROUTES.filter((r) => !r.advanced || pr.mode === 'advanced')
  const current = ROUTES.find((r) => r.path === route) || ROUTES[0]
  const View = current.view
  return (
    <div class="shell">
      <aside class={`side ${menu ? 'open' : ''}`}>
        <div class="brand">◆ Open Market OS <small>v0.1</small></div>
        <nav class="nav">
          {visible.map((r) => (
            <>
              {r.section && <div class="sep">{r.section}</div>}
              <Link href={r.path} onClick={() => setMenu(false)}><span>{r.icon}</span>{r.label}</Link>
            </>
          ))}
        </nav>
        <div class="sp" />
        <div class="small muted" style="padding:12px 16px">
          {navigator.userAgent.includes('OpenMarketOSApp') && <div style="margin-bottom:8px"><a href="omos-app://settings">⚙ Phone app settings (alerts, pairing)</a></div>}
          <div>{who?.name} · {who?.scopes.join(', ')}</div>
          <div>{online ? '● live updates' : '○ reconnecting…'}</div>
          <div style="margin-top:6px" class="row">
            <Btn sm on={pr.mode === 'beginner'} onClick={() => prefs.set((x) => ({ ...x, mode: 'beginner' }))}>Beginner</Btn>
            <Btn sm on={pr.mode === 'advanced'} onClick={() => prefs.set((x) => ({ ...x, mode: 'advanced' }))}>Advanced</Btn>
          </div>
        </div>
      </aside>
      <main class="main">
        <div class="topbar">
          <h1>{current.icon} {current.label}</h1>
          <span class={`badge ${online ? 'b-ok' : 'b-degraded'}`}>{online ? 'connected' : 'offline'}</span>
          <Btn sm kind="ghost" title="Switch colour theme" onClick={() => prefs.set((x) => ({ ...x, theme: x.theme === 'dark' ? 'light' : 'dark' }))}>{pr.theme === 'dark' ? 'Light theme' : 'Dark theme'}</Btn>
        </div>
        <div class="content"><View /></div>
        <nav class="bottomnav">
          {ROUTES.filter((r) => r.mobile).map((r) => <Link href={r.path}><span class="ico">{r.icon}</span>{r.label.split(' ')[0]}</Link>)}
          <button onClick={() => setMenu(!menu)}><span class="ico">☰</span>More</button>
        </nav>
      </main>
      <Toasts />
    </div>
  )
}

function beep() {
  try {
    const ctx = new AudioContext()
    const o = ctx.createOscillator(), g = ctx.createGain()
    o.frequency.value = 880
    g.gain.value = 0.05
    o.connect(g).connect(ctx.destination)
    o.start()
    setTimeout(() => { o.stop(); void ctx.close() }, 180)
  } catch { /* audio unavailable */ }
}

render(<App />, document.getElementById('app')!)
