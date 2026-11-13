import { test, expect, apiCreateConnection, apiDeleteConnection, selectConnectionInStorage, ensureDemoMonitor } from './fixtures'

test.describe('Gráficos (conexión demo, monitor activo)', () => {
  test.beforeEach(async ({ page }) => { await page.goto('/charts') })

  test('KPI cards muestran valores reales', async ({ page }) => {
    await expect(page.getByTestId('kpi-sessions-value')).toHaveText(/^\d+ \/ \d+$/)
    await expect(page.getByTestId('kpi-cache-value')).toHaveText(/\d/)
    await expect(page.getByTestId('kpi-cache-value')).not.toHaveText('—')
    await expect(page.getByTestId('kpi-size-value')).toHaveText(/\d.*(MB|GB|KB)/i)
    await expect(page.getByTestId('kpi-longest-value')).not.toHaveText('—')
    await expect(page.getByTestId('kpi-maxconn-value')).toHaveText('100')
    await expect(page.getByTestId('kpi-version-value')).toContainText('PostgreSQL')
  })

  test('KPI coincide con /api/connections/1/overview', async ({ page, request }) => {
    const o = await (await request.get('/api/connections/1/overview')).json()
    await expect(page.getByTestId('kpi-maxconn-value')).toHaveText(String(o.max_connections))
    await expect(page.getByTestId('kpi-version-value')).toContainText(o.version.slice(0, 20))
  })

  test('monitor de la demo: estado, indicador del sidebar y botones', async ({ page }) => {
    await expect(page.getByTestId('monitor-status')).toHaveAttribute('data-running', 'true')
    await expect(page.getByTestId('monitor-status')).toContainText('Monitoreando cada 2')
    await expect(page.getByTestId('monitoring-indicator')).toContainText('Monitoreando')
    await expect(page.getByTestId('monitor-start-btn')).toBeDisabled()
    await expect(page.getByTestId('monitor-stop-btn')).toBeEnabled()
  })

  test('todos los gráficos renderizan SVG con contenido', async ({ page }) => {
    await page.getByTestId('window-60').click()
    for (const id of ['sessions', 'tps', 'reads', 'cache', 'longest']) {
      const card = page.getByTestId(`chart-${id}`)
      await expect(card).toBeVisible()
      const svg = card.locator('svg.recharts-surface').first()
      await expect(svg).toBeVisible()
      // data series drawn
      await expect(card.locator('path.recharts-curve, path.recharts-area-curve, .recharts-line-curve').first()).toBeAttached()
      const box = await svg.boundingBox()
      expect(box!.width).toBeGreaterThan(200)
      expect(box!.height).toBeGreaterThan(100)
      expect(await card.locator('svg.recharts-surface').first().evaluate((e) => e.children.length)).toBeGreaterThan(2)
    }
    const top = page.getByTestId('chart-top-queries')
    await expect(top).toBeVisible()
    await expect(top.locator('svg.recharts-surface').first()).toBeVisible({ timeout: 15_000 })
    await expect(top.locator('.recharts-bar-rectangle').first()).toBeAttached()
  })

  test('selector de ventana cambia aria-pressed y la petición de métricas', async ({ page }) => {
    await expect(page.getByTestId('window-15')).toHaveAttribute('aria-pressed', 'true')
    for (const m of [5, 60, 360, 1440]) {
      const req = page.waitForRequest((r) => r.url().includes('/api/connections/1/metrics?') && r.url().includes(`minutes=${m}`))
      await page.getByTestId(`window-${m}`).click()
      await req
      await expect(page.getByTestId(`window-${m}`)).toHaveAttribute('aria-pressed', 'true')
      await expect(page.getByTestId('window-15')).toHaveAttribute('aria-pressed', 'false')
    }
  })

  test('layout: input de intervalo personalizado no se estira a todo el ancho', async ({ page }) => {
    const box = await page.getByTestId('monitor-interval-custom').boundingBox()
    expect(box!.width).toBeLessThan(140)
  })

  test('intervalo personalizado inválido bloquea Iniciar (monitor ya activo: controles deshabilitados)', async ({ page }) => {
    await expect(page.getByTestId('monitor-interval-1')).toBeDisabled()
    await expect(page.getByTestId('monitor-interval-custom')).toBeDisabled()
  })
})

test.describe('Gráficos: monitor sobre conexión nueva', () => {
  test('iniciar a 1 s genera puntos en ~10 s, indicador y stop funcionan', async ({ page, request }) => {
    const c = await apiCreateConnection(request)
    try {
      await selectConnectionInStorage(page, c.id)
      await page.goto('/charts')
      await expect(page.getByTestId('connection-select')).toHaveValue(String(c.id))
      // no monitoring yet
      await expect(page.getByTestId('monitor-status')).toHaveAttribute('data-running', 'false')
      await expect(page.getByTestId('monitor-status')).toContainText('Detenido')
      await expect(page.getByTestId('monitoring-indicator')).toHaveCount(0)
      await expect(page.getByTestId('monitor-stop-btn')).toBeDisabled()
      // empty state before monitoring, no charts
      await expect(page.getByTestId('empty-state')).toBeVisible()
      await expect(page.getByTestId('chart-sessions')).toHaveCount(0)

      // empty CTA focuses the start button
      await page.getByTestId('empty-start-btn').click()
      await expect(page.getByTestId('monitor-start-btn')).toBeFocused()

      await page.getByTestId('monitor-interval-1').click()
      await expect(page.getByTestId('monitor-interval-1')).toHaveAttribute('aria-pressed', 'true')
      await page.getByTestId('monitor-start-btn').click()
      await expect(page.getByTestId('toast').filter({ hasText: 'Monitoreo iniciado' })).toBeVisible()
      await expect(page.getByTestId('monitor-status')).toHaveAttribute('data-running', 'true')
      await expect(page.getByTestId('monitor-status')).toContainText('Monitoreando cada 1')
      await expect(page.getByTestId('monitoring-indicator')).toBeVisible()
      await expect(page.getByTestId('monitor-start-btn')).toBeDisabled()

      // charts with points within ~10 s
      await expect(page.getByTestId('chart-sessions')).toBeVisible({ timeout: 12_000 })
      await expect(page.getByTestId('chart-sessions').locator('svg.recharts-surface').first()).toBeVisible()
      const samples = async () => (await (await request.get(`/api/connections/${c.id}/monitor`)).json()).samples as number
      await expect.poll(samples, { timeout: 10_000 }).toBeGreaterThanOrEqual(3)
      const m = await (await request.get(`/api/connections/${c.id}/metrics?minutes=5`)).json()
      expect(m.points.length).toBeGreaterThanOrEqual(2)
      // status text eventually shows sample count
      await expect(page.getByTestId('monitor-status')).toContainText(/[1-9]\d* muestras/, { timeout: 10_000 })

      // stop
      await page.getByTestId('monitor-stop-btn').click()
      await expect(page.getByTestId('toast').filter({ hasText: 'Monitoreo detenido' })).toBeVisible()
      await expect(page.getByTestId('monitor-status')).toHaveAttribute('data-running', 'false')
      await expect(page.getByTestId('monitoring-indicator')).toHaveCount(0)
      await expect(page.getByTestId('monitor-start-btn')).toBeEnabled()
      expect((await (await request.get(`/api/connections/${c.id}/monitor`)).json()).running).toBe(false)
    } finally {
      await apiDeleteConnection(request, c.id)
    }
  })

  test('intervalo personalizado fuera de rango deshabilita Iniciar; borrar historial', async ({ page, request }) => {
    const c = await apiCreateConnection(request)
    try {
      await selectConnectionInStorage(page, c.id)
      await page.goto('/charts')
      await page.getByTestId('monitor-interval-custom').fill('5000')
      await expect(page.getByTestId('monitor-start-btn')).toBeDisabled()
      await expect(page.getByText('El intervalo debe estar entre 1 y 3600 segundos.')).toBeVisible()
      await page.getByTestId('monitor-interval-custom').fill('1')
      await expect(page.getByTestId('monitor-start-btn')).toBeEnabled()
      await page.getByTestId('monitor-duration').fill('1')
      await page.getByTestId('monitor-start-btn').click()
      await expect(page.getByTestId('monitor-status')).toHaveAttribute('data-running', 'true')
      await expect(page.getByTestId('monitor-status')).toContainText(/termina/, { timeout: 10_000 })
      await page.getByTestId('monitor-stop-btn').click()
      await expect(page.getByTestId('monitor-status')).toHaveAttribute('data-running', 'false')
      page.once('dialog', (d) => d.accept())
      await page.getByTestId('clear-metrics-btn').click()
      await expect(page.getByTestId('toast').filter({ hasText: 'Historial borrado' })).toBeVisible()
      await expect(page.getByTestId('empty-state')).toBeVisible()
    } finally {
      await apiDeleteConnection(request, c.id)
    }
  })

  test.afterAll(async ({ request }) => { await ensureDemoMonitor(request) })
})
