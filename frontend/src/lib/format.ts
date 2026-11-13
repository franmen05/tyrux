export function fmtNumber(n: number | null | undefined, digits = 0): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  return n.toLocaleString('es-ES', { maximumFractionDigits: digits, minimumFractionDigits: 0 })
}

export function fmtBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  let v = Math.abs(n)
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  const s = i === 0 ? String(Math.round(v)) : v.toLocaleString('es-ES', { maximumFractionDigits: v >= 100 ? 0 : 1 })
  return `${n < 0 ? '-' : ''}${s} ${units[i]}`
}

export function fmtMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1) return `${ms.toLocaleString('es-ES', { maximumFractionDigits: 2 })} ms`
  if (ms < 1000) return `${Math.round(ms)} ms`
  return fmtSeconds(ms / 1000)
}

export function fmtSeconds(s: number | null | undefined): string {
  if (s === null || s === undefined) return '—'
  if (s < 1) return `${Math.round(s * 1000)} ms`
  if (s < 60) return `${s.toLocaleString('es-ES', { maximumFractionDigits: 1 })} s`
  if (s < 3600) {
    const m = Math.floor(s / 60)
    return `${m} min ${Math.round(s - m * 60)} s`
  }
  if (s < 86400) {
    const h = Math.floor(s / 3600)
    return `${h} h ${Math.round((s - h * 3600) / 60)} min`
  }
  const d = Math.floor(s / 86400)
  return `${d} d ${Math.round((s - d * 86400) / 3600)} h`
}

export function fmtPct(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined) return '—'
  return `${n.toLocaleString('es-ES', { maximumFractionDigits: digits })} %`
}

/** Backend datetimes may be naive (no tz suffix); treat those as UTC. */
export function parseTs(iso: string): Date {
  return new Date(/([zZ]|[+-]\d\d:?\d\d)$/.test(iso) ? iso : iso + 'Z')
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = parseTs(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('es-ES', { dateStyle: 'short', timeStyle: 'medium' })
}

export function fmtTime(iso: string | number): string {
  const d = typeof iso === 'number' ? new Date(iso) : parseTs(iso)
  return d.toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export function truncate(s: string, n: number): string {
  const one = s.replace(/\s+/g, ' ').trim()
  return one.length > n ? one.slice(0, n - 1) + '…' : one
}

export function downloadText(filename: string, text: string) {
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

export function safeStorageGet(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}
export function safeStorageSet(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  } catch {
    /* ignore */
  }
}
