import { test, expect } from './fixtures'

test.describe('Optimización', () => {
  test.beforeEach(async ({ page }) => { await page.goto('/optimize') })

  test('muestra aviso de solo lectura y recomendaciones agrupadas incl. missing_index orders(customer_id)', async ({ page }) => {
    await expect(page.getByTestId('no-exec-notice')).toContainText('no ejecuta cambios')
    await expect(page.getByTestId('recommendation-item').first()).toBeVisible()
    await expect(page.getByTestId('rec-group-high')).toBeVisible()
    const rec = page.locator('[data-testid="recommendation-item"][data-rec-id="missing_index:public.orders(customer_id)"]')
    await expect(rec).toBeVisible()
    await expect(rec).toHaveAttribute('data-severity', 'high')
    await expect(rec).toContainText('orders')
    await expect(rec).toContainText('customer_id')
    await expect(page.getByTestId('selected-count')).toHaveText(/^0 de \d+ seleccionadas$/)
    await expect(page.getByTestId('generate-script-btn')).toBeDisabled()
    // severity attribute is consistent with the group it sits in
    for (const sev of ['high', 'medium', 'low', 'info']) {
      const g = page.getByTestId(`rec-group-${sev}`)
      if (await g.count()) {
        const bad = await g.locator('[data-testid="recommendation-item"]').evaluateAll((els, s) => els.filter((e) => e.getAttribute('data-severity') !== s).length, sev)
        expect(bad).toBe(0)
      }
    }
  })

  test('ver SQL, seleccionar, generar script con CREATE INDEX CONCURRENTLY, copiar y descargar', async ({ page, context }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write'])
    const rec = page.locator('[data-testid="recommendation-item"][data-rec-id="missing_index:public.orders(customer_id)"]')
    await rec.getByTestId('rec-sql-toggle').click()
    await expect(rec.getByTestId('rec-sql')).toContainText('CREATE INDEX CONCURRENTLY')
    await rec.getByTestId('rec-sql-toggle').click()
    await expect(rec.getByTestId('rec-sql')).toHaveCount(0)

    await rec.getByTestId('rec-checkbox').check()
    await expect(page.getByTestId('selected-count')).toHaveText(/^1 de \d+ seleccionadas$/)
    await page.locator('[data-testid="recommendation-item"][data-rec-id="unused_index:public.orders_total_idx"]').getByTestId('rec-checkbox').check()
    await expect(page.getByTestId('selected-count')).toHaveText(/^2 de /)
    await page.getByTestId('generate-script-btn').click()
    const out = page.getByTestId('script-output')
    await expect(out).toBeVisible()
    await expect(out).toContainText('CREATE INDEX CONCURRENTLY')
    await expect(out).toContainText('orders_customer_id_idx')
    await expect(out).toContainText(/DROP INDEX/i)
    await expect(page.getByText('2 cambio(s)')).toBeVisible()

    await page.getByTestId('copy-script-btn').click()
    await expect(page.getByTestId('toast').filter({ hasText: /Copiado/ })).toBeVisible()
    const clip = await page.evaluate(() => navigator.clipboard.readText()).catch(() => null)
    if (clip !== null) expect(clip).toContain('CREATE INDEX CONCURRENTLY')

    const [dl] = await Promise.all([page.waitForEvent('download'), page.getByTestId('download-script-btn').click()])
    expect(dl.suggestedFilename()).toBe('optimizacion-postgres.sql')
    const path = await dl.path()
    const fs = await import('node:fs')
    const text = fs.readFileSync(path!, 'utf8')
    expect(text).toContain('CREATE INDEX CONCURRENTLY')
    expect(text.length).toBeGreaterThan(50)
  })

  test('seleccionar todo / deseleccionar', async ({ page }) => {
    await page.getByTestId('select-all-recs').check()
    const total = await page.getByTestId('rec-checkbox').count()
    const enabled = await page.locator('[data-testid="rec-checkbox"]:not([disabled])').count()
    expect(enabled).toBeGreaterThan(0)
    await expect(page.getByTestId('selected-count')).toHaveText(new RegExp(`^${enabled} de ${total} seleccionadas$`))
    await expect(page.getByTestId('generate-script-btn')).toBeEnabled()
    await page.getByTestId('select-all-recs').uncheck()
    await expect(page.getByTestId('selected-count')).toHaveText(/^0 de/)
  })
})
