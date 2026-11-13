import { test as base, expect, type APIRequestContext, type Page } from '@playwright/test'

export { expect }

/** Demo DB reachable from inside the dbmon-app container. */
export const DEMO = {
  engine: 'postgres' as const,
  host: process.env.E2E_PG_HOST ?? 'host.containers.internal',
  port: Number(process.env.E2E_PG_PORT ?? 55433),
  database: 'shop',
  username: 'dbmon',
  password: 'dbmon',
}
export const ORACLE = {
  engine: 'oracle' as const,
  host: process.env.E2E_ORACLE_HOST ?? 'host.containers.internal',
  port: Number(process.env.E2E_ORACLE_PORT ?? 1521),
  database: 'FREEPDB1',
  username: 'shop',
  password: 'shop',
}

export const uniqueName = (p = 'e2e') => `${p}-${Date.now()}-${Math.floor(Math.random() * 1000)}`

export interface Conn { id: number; name: string; engine: string; monitoring: boolean }

export async function apiCreateConnection(
  request: APIRequestContext,
  overrides: Partial<typeof DEMO & { name: string; ssl: boolean; port: number }> = {},
): Promise<Conn> {
  const body = { ...DEMO, name: uniqueName(), ssl: false, options: {}, ...overrides }
  const r = await request.post('/api/connections', { data: body })
  expect(r.status(), await r.text()).toBeLessThan(300)
  return r.json()
}

export async function apiDeleteConnection(request: APIRequestContext, id: number) {
  await request.delete(`/api/connections/${id}/monitor`).catch(() => undefined)
  await request.delete(`/api/connections/${id}`).catch(() => undefined)
}

/** Delete every leftover connection created by a previous (crashed) run. */
export async function apiCleanupE2E(request: APIRequestContext) {
  const list: Conn[] = await (await request.get('/api/connections')).json()
  for (const c of list) if (c.name.startsWith('e2e-') && c.id !== 1) await apiDeleteConnection(request, c.id)
}

/** Restores the demo monitor (connection 1) to running @ 2 s if it is not. */
export async function ensureDemoMonitor(request: APIRequestContext) {
  const s = await (await request.get('/api/connections/1/monitor')).json()
  if (!s.running || s.interval_seconds !== 2) {
    if (s.running) await request.delete('/api/connections/1/monitor')
    const r = await request.post('/api/connections/1/monitor', { data: { interval_seconds: 2, duration_minutes: null } })
    expect(r.status(), await r.text()).toBeLessThan(300)
  }
}

export async function selectConnectionInStorage(page: Page, id: number) {
  await page.addInitScript((cid) => {
    try { localStorage.setItem('dbmonitor.connectionId', String(cid)) } catch { /* ignore */ }
  }, id)
}

/**
 * Extended test: every test collects `pageerror` and console errors and fails when any appears.
 * Browser-generated "Failed to load resource" lines (HTTP 4xx/5xx, which tests provoke on purpose)
 * are ignored; they are not JS errors. Tests may append to `allowConsole` to whitelist more.
 */
export const test = base.extend<{ errors: string[]; allowConsole: RegExp[] }>({
  allowConsole: [[], { option: false }],
  errors: [
    async ({ page, allowConsole }, use) => {
      const errors: string[] = []
      page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))
      page.on('console', (m) => {
        if (m.type() !== 'error') return
        const t = m.text()
        if (/Failed to load resource/.test(t)) return
        if (allowConsole.some((re) => re.test(t))) return
        errors.push(`console.error: ${t}`)
      })
      await use(errors)
      expect(errors, 'uncaught page / console errors').toEqual([])
    },
    { auto: true },
  ],
  // Default: demo connection selected.
  page: async ({ page }, use) => {
    await selectConnectionInStorage(page, 1)
    await use(page)
  },
})

export const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))
