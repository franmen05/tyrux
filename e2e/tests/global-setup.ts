import { request } from '@playwright/test'
import { apiCleanupE2E, ensureDemoMonitor } from './fixtures'

export default async function globalSetup() {
  const ctx = await request.newContext({ baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:8099' })
  const h = await ctx.get('/api/health')
  if (!h.ok()) throw new Error(`App not healthy at baseURL: ${h.status()}`)
  await apiCleanupE2E(ctx)
  await ensureDemoMonitor(ctx)
  await ctx.dispose()
}
