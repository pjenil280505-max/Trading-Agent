// Small shared state with subscriptions (no framework dependency).
import { useEffect, useState } from 'preact/hooks'

type Listener = () => void

export class Store<T> {
  private value: T
  private listeners = new Set<Listener>()
  constructor(v: T) { this.value = v }
  get(): T { return this.value }
  set(v: T | ((old: T) => T)) {
    this.value = typeof v === 'function' ? (v as (o: T) => T)(this.value) : v
    this.listeners.forEach((l) => l())
  }
  subscribe(l: Listener) { this.listeners.add(l); return () => { this.listeners.delete(l) } }
}

export function useStore<T>(s: Store<T>): T {
  const [, force] = useState(0)
  useEffect(() => s.subscribe(() => force((n) => n + 1)), [s])
  return s.get()
}

function persisted<T>(key: string, fallback: T): Store<T> {
  let initial = fallback
  try {
    const raw = localStorage.getItem(key)
    if (raw) initial = { ...fallback, ...JSON.parse(raw) }
  } catch { /* corrupted local state: ignore */ }
  const s = new Store<T>(initial)
  s.subscribe(() => { try { localStorage.setItem(key, JSON.stringify(s.get())) } catch { /* quota */ } })
  return s
}

export type Prefs = { mode: 'beginner' | 'advanced'; theme: 'dark' | 'light'; sound: boolean; accountId: number | null }
export const prefs = persisted<Prefs>('omos.prefs', { mode: 'beginner', theme: 'dark', sound: true, accountId: null })

export type Toast = { id: number; kind: 'info' | 'ok' | 'warn' | 'error'; text: string }
export const toasts = new Store<Toast[]>([])
let toastId = 0
export function toast(text: string, kind: Toast['kind'] = 'info', ms = 5000) {
  const id = ++toastId
  toasts.set((t) => [...t, { id, kind, text }])
  setTimeout(() => toasts.set((t) => t.filter((x) => x.id !== id)), ms)
}

export const me = new Store<{ id: number; name: string; kind: string; scopes: string[] } | null>(null)
export const can = (scope: string) => !!me.get()?.scopes.includes(scope)

export function errText(e: unknown): string {
  if (e && typeof e === 'object' && 'message' in e) return String((e as { message: string }).message)
  return String(e)
}

export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  const abs = Math.abs(v)
  if (abs !== 0 && abs < 0.01) return v.toPrecision(3)
  if (abs >= 1e9) return (v / 1e9).toFixed(2) + 'B'
  if (abs >= 1e6) return (v / 1e6).toFixed(2) + 'M'
  return v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  return `${v >= 0 ? '+' : ''}${v.toFixed(digits)}%`
}

export function fmtTime(ms: number | null | undefined): string {
  if (!ms) return '—'
  return new Date(ms).toLocaleString()
}
