import { test, expect, ORACLE, apiCreateConnection, apiDeleteConnection, selectConnectionInStorage, uniqueName } from './fixtures'

// Run with:  E2E_ORACLE=1 npx playwright test tests/oracle.spec.ts
test.describe('Oracle (E2E_ORACLE=1)', () => {
  test.skip(!process.env.E2E_ORACLE, 'set E2E_ORACLE=1 to run the Oracle demo tests')

  test('Probar conexión desde el formulario (motor Oracle, puerto 1521, Service name)', async ({ page }) => {
    await page.goto('/connections')
    await page.getByTestId('add-connection-btn').click()
    await page.getByTestId('conn-engine-oracle').click()
    await expect(page.getByTestId('conn-port')).toHaveValue('1521')
    await page.getByTestId('conn-name').fill(uniqueName())
    await page.getByTestId('conn-host').fill(ORACLE.host)
    await page.getByTestId('conn-port').fill(String(ORACLE.port))
    await page.getByTestId('conn-database').fill(ORACLE.database)
    await page.getByTestId('conn-username').fill(ORACLE.username)
    await page.getByTestId('conn-password').fill(ORACLE.password)
    await page.getByTestId('conn-test-btn').click()
    await expect(page.getByTestId('conn-test-result')).toContainText('Conexión correcta', { timeout: 30_000 })
    await expect(page.getByTestId('conn-test-result')).toContainText(/Oracle/i)
  })

  test.describe('con conexión Oracle', () => {
    let id = 0
    test.beforeEach(async ({ request, page }) => {
      const c = await apiCreateConnection(request, { ...ORACLE })
      id = c.id
      await selectConnectionInStorage(page, id)
    })
    test.afterEach(async ({ request }) => { await apiDeleteConnection(request, id) })

    test('badge Oracle y KPIs reales en Gráficos', async ({ page }) => {
      await page.goto('/charts')
      await expect(page.getByTestId('engine-badge')).toHaveText('Oracle')
      await expect(page.getByTestId('kpi-sessions-value')).toHaveText(/^\d+ \/ \d+$/, { timeout: 30_000 })
      await expect(page.getByTestId('kpi-version-value')).toContainText('Oracle')
      await expect(page.getByTestId('kpi-size-value')).toHaveText(/\d.*(MB|GB)/)
      await expect(page.getByTestId('kpi-cache-value')).not.toHaveText('—')
      await expect(page.getByTestId('kpi-maxconn-value')).not.toHaveText('—')
    })

    test('monitor a 1 s produce puntos, todos los gráficos con SVG, y se detiene', async ({ page }) => {
      await page.goto('/charts')
      await page.getByTestId('monitor-interval-1').click()
      await page.getByTestId('monitor-start-btn').click()
      await expect(page.getByTestId('chart-sessions')).toBeVisible({ timeout: 20_000 })
      await expect(page.getByTestId('monitoring-indicator')).toBeVisible()
      for (const c of ['sessions', 'tps', 'reads', 'cache', 'longest']) {
        await expect(page.getByTestId(`chart-${c}`).locator('svg.recharts-surface').first()).toBeVisible()
      }
      await page.getByTestId('monitor-stop-btn').click()
      await expect(page.getByTestId('monitor-status')).toHaveAttribute('data-running', 'false')
    })

    test('Técnico: todas las pestañas cargan; campos nulos de Oracle se muestran como "—"', async ({ page }) => {
      await page.goto('/technical')
      await expect(page.getByTestId('active-query-row').first().or(page.getByTestId('empty-state'))).toBeVisible({ timeout: 30_000 })
      for (const t of ['active', 'top', 'indexes', 'tables', 'locks', 'config']) {
        await page.getByTestId(`tab-${t}`).click()
        await expect(page.getByTestId('loading')).toHaveCount(0, { timeout: 30_000 })
        await expect(page.getByTestId('error-banner')).toHaveCount(0)
        expect((await page.locator('main').innerText()).length).toBeGreaterThan(80)
      }
      await page.getByTestId('tab-top').click()
      const row = page.getByTestId('top-query-row').first()
      await expect(row).toBeVisible()
      // max_time_ms is null in Oracle -> "Máx" column (6th td, index 4) renders a dash
      await expect(row.locator('td').nth(4)).toHaveText('—')
      await expect(row).not.toContainText(/null|undefined|NaN/)
      await page.getByTestId('tab-tables').click()
      const trow = page.getByTestId('table-row').first()
      await expect(trow).toBeVisible()
      await expect(trow).not.toContainText(/null|undefined|NaN/)
      await expect(trow.locator('td').nth(7)).toHaveText('—') // dead_rows
      await expect(trow.locator('td').nth(10)).toHaveText('—') // last_vacuum
      await page.getByTestId('tab-indexes').click()
      await expect(page.getByTestId('index-row').first()).toBeVisible()
      await page.getByTestId('index-filter-unused').click()
      expect(await page.getByTestId('index-row').count()).toBeGreaterThan(0)
    })

    test('explain de una top query (DISPLAY_CURSOR) devuelve texto de plan', async ({ page }) => {
      await page.goto('/technical')
      await page.getByTestId('tab-top').click()
      await page.getByTestId('top-query-row').filter({ hasText: /SELECT/i }).first().click()
      await expect(page.getByTestId('explain-drawer')).toBeVisible()
      await page.getByTestId('explain-run-btn').click()
      await expect(page.getByTestId('explain-plan')).toContainText(/Plan hash value|Id\s+\|\s+Operation/i, { timeout: 30_000 })
      await expect(page.getByTestId('error-banner')).toHaveCount(0)
    })

    test('Analizar SQL pegado (EXPLAIN PLAN) sugiere índice sobre ORDERS', async ({ page }) => {
      await page.goto('/technical')
      await page.getByTestId('tab-top').click()
      await page.getByTestId('analyze-sql-btn').click()
      await page.getByTestId('explain-sql-input').fill('SELECT * FROM orders WHERE customer_id = 42')
      await page.getByTestId('explain-run-btn').click()
      await expect(page.getByTestId('explain-plan')).toContainText(/TABLE ACCESS|ORDERS/i, { timeout: 30_000 })
      await expect(page.getByTestId('explain-finding').first()).toContainText(/CUSTOMER_ID/i)
    })

    test('Optimización: missing_index SHOP.ORDERS(CUSTOMER_ID) y script con CREATE INDEX ... ONLINE', async ({ page }) => {
      await page.goto('/optimize')
      const rec = page.locator('[data-testid="recommendation-item"][data-rec-id="missing_index:SHOP.ORDERS(CUSTOMER_ID)"]')
      await expect(rec).toBeVisible({ timeout: 30_000 })
      await rec.getByTestId('rec-checkbox').check()
      await page.getByTestId('generate-script-btn').click()
      await expect(page.getByTestId('script-output')).toContainText(/CREATE INDEX[\s\S]*ONLINE/i)
      const [dl] = await Promise.all([page.waitForEvent('download'), page.getByTestId('download-script-btn').click()])
      expect(dl.suggestedFilename()).toBe('optimizacion-oracle.sql')
    })

    test('Bloqueos: filas de bloqueo o estado vacío sin errores', async ({ page }) => {
      await page.goto('/technical')
      await page.getByTestId('tab-locks').click()
      await expect(page.getByTestId('lock-row').first().or(page.getByTestId('empty-state'))).toBeVisible({ timeout: 30_000 })
    })
  })
})
