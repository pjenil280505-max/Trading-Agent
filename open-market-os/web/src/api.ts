// HTTP + WebSocket client for the local host. The token lives in localStorage of THIS origin only.

const TOKEN_KEY = 'omos.token'

export class ApiError extends Error {
  code: string
  status: number
  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(t: string | null) {
  if (t) localStorage.setItem(TOKEN_KEY, t)
  else localStorage.removeItem(TOKEN_KEY)
}

type Query = Record<string, string | number | boolean | undefined | null>

function qs(q?: Query): string {
  if (!q) return ''
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== null && v !== '') p.set(k, String(v))
  const s = p.toString()
  return s ? `?${s}` : ''
}

export async function api<T = any>(method: string, path: string, body?: unknown, query?: Query): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  const tok = getToken()
  if (tok) headers.Authorization = `Bearer ${tok}`
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const res = await fetch(`/api${path}${qs(query)}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const ct = res.headers.get('content-type') || ''
  if (!res.ok) {
    let code = 'error'
    let message = `HTTP ${res.status}`
    if (ct.includes('json')) {
      const d = await res.json()
      code = d.error || code
      message = d.message || message
    }
    if (res.status === 401) window.dispatchEvent(new CustomEvent('omos:auth-required'))
    throw new ApiError(res.status, code, message)
  }
  if (ct.includes('json')) return res.json()
  return (await res.text()) as unknown as T
}

export const get = <T = any>(path: string, query?: Query) => api<T>('GET', path, undefined, query)
export const post = <T = any>(path: string, body: unknown = {}, query?: Query) => api<T>('POST', path, body, query)
export const put = <T = any>(path: string, body: unknown = {}) => api<T>('PUT', path, body)
export const patch = <T = any>(path: string, body: unknown = {}) => api<T>('PATCH', path, body)
export const del = <T = any>(path: string, query?: Query) => api<T>('DELETE', path, undefined, query)

export async function exchangeCode(code: string): Promise<void> {
  const res = await fetch('/api/auth/exchange', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code, device_name: deviceName() }),
  })
  const d = await res.json()
  if (!res.ok) throw new ApiError(res.status, d.error, d.message)
  setToken(d.token)
}

function deviceName(): string {
  const ua = navigator.userAgent
  const kind = /Android/i.test(ua) ? 'Android' : /iPhone|iPad/i.test(ua) ? 'iOS' : /Windows/i.test(ua) ? 'Windows'
    : /Mac OS/i.test(ua) ? 'macOS' : /Linux/i.test(ua) ? 'Linux' : 'Browser'
  return `${kind} browser`
}

// ---------------------------------------------------------------- live events
export type Evt = { topic: string; ts: number; data: any }
type Handler = (e: Evt) => void

class Live {
  ws: WebSocket | null = null
  handlers = new Set<Handler>()
  subs = new Map<string, number>() // "instrument|tf" -> refcount
  retry = 0
  connected = false
  statusListeners = new Set<(c: boolean) => void>()

  connect() {
    const tok = getToken()
    if (!tok || this.ws) return
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${proto}://${location.host}/api/ws`)
    this.ws = ws
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: 'auth', token: tok }))
      this.retry = 0
    }
    ws.onmessage = (m) => {
      let e: Evt
      try { e = JSON.parse(m.data) } catch { return }
      if (e.topic === 'hello') {
        this.setConnected(true)
        for (const key of this.subs.keys()) {
          const [instrument, tf] = key.split('|')
          ws.send(JSON.stringify({ type: 'subscribe', instrument, tf }))
        }
      }
      this.handlers.forEach((h) => h(e))
    }
    ws.onclose = () => {
      this.ws = null
      this.setConnected(false)
      const delay = Math.min(30000, 1000 * 2 ** this.retry++)
      setTimeout(() => this.connect(), delay)
    }
  }

  setConnected(c: boolean) {
    this.connected = c
    this.statusListeners.forEach((l) => l(c))
  }

  on(h: Handler) {
    this.handlers.add(h)
    return () => this.handlers.delete(h)
  }

  subscribe(instrument: string, tf: string) {
    const key = `${instrument}|${tf}`
    this.subs.set(key, (this.subs.get(key) || 0) + 1)
    if (this.ws && this.connected) this.ws.send(JSON.stringify({ type: 'subscribe', instrument, tf }))
    return () => {
      const n = (this.subs.get(key) || 1) - 1
      if (n <= 0) {
        this.subs.delete(key)
        if (this.ws && this.connected) this.ws.send(JSON.stringify({ type: 'unsubscribe', instrument, tf }))
      } else this.subs.set(key, n)
    }
  }
}

export const live = new Live()
