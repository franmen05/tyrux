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

test.describe('Optimización con muchas recomendaciones (lista simulada)', () => {
  const N = 120
  const all = Array.from({ length: N }, (_, i) => {
    const n = String(i).padStart(3, '0')
    return {
      id: `unused_index:public.idx_${n}`, category: 'unused_index', severity: i < 40 ? 'high' : 'low',
      title: `Índice sin uso idx_${n}`, detail: 'detalle', object_name: `public.idx_${n}`,
      sql: `DROP INDEX CONCURRENTLY IF EXISTS public.idx_${n};`, estimated_benefit: null,
    }
  })
  let scriptBody: Record<string, unknown> | null = null

  test.beforeEach(async ({ page }) => {
    scriptBody = null
    await page.route(/\/api\/connections\/\d+\/recommendations\/page/, async (route) => {
      const u = new URL(route.request().url())
      const off = Number(u.searchParams.get('offset')), lim = Number(u.searchParams.get('limit'))
      const sev = u.searchParams.get('severity')
      const m = sev ? all.filter((r) => r.severity === sev) : all
      await route.fulfill({
        json: {
          items: m.slice(off, off + lim), total: m.length, selectable_total: m.length, offset: off, limit: lim,
          total_all: N, by_severity: { high: 40, low: 80 }, by_category: { unused_index: N },
          warnings: ['Índices duplicados / sin uso: no se pudo completar (timeout).'],
          generated_at: new Date().toISOString(), elapsed_ms: 1234,
        },
      })
    })
    await page.route(/\/api\/connections\/\d+\/optimize\/script$/, async (route) => {
      scriptBody = route.request().postDataJSON()
      await route.fulfill({ json: { engine: 'postgres', script: '-- script\n', count: 1 } })
    })
    await page.goto('/optimize')
  })

  test('pagina, avisa del resultado parcial y filtra por severidad', async ({ page }) => {
    await expect(page.getByTestId('rec-warnings')).toContainText('timeout')
    await expect(page.getByTestId('recommendation-item')).toHaveCount(50)
    await expect(page.getByTestId('rec-page-info')).toContainText('Página 1 de 3')
    await page.getByTestId('rec-page-next').click()
    await expect(page.getByTestId('recommendation-item').first()).toHaveAttribute('data-rec-id', 'unused_index:public.idx_050')
    await page.getByTestId('rec-page-size').selectOption('100')
    await expect(page.getByTestId('rec-page-info')).toContainText('Página 1 de 2')
    await page.getByTestId('rec-filter-severity').selectOption('high')
    await expect(page.getByTestId('recommendation-item')).toHaveCount(40)
    await expect(page.getByTestId('rec-page-info')).toContainText('Página 1 de 1')
    await expect(page.getByTestId('rec-group-low')).toHaveCount(0)
  })

  test('selección en varias páginas y «seleccionar todas» con exclusiones', async ({ page }) => {
    await page.locator('[data-rec-id="unused_index:public.idx_001"]').getByTestId('rec-checkbox').check()
    await page.getByTestId('rec-page-next').click()
    await page.locator('[data-rec-id="unused_index:public.idx_060"]').getByTestId('rec-checkbox').check()
    await expect(page.getByTestId('selected-count')).toHaveText(`2 de ${N} seleccionadas`)
    await page.getByTestId('generate-script-btn').click()
    await expect(page.getByTestId('script-output')).toBeVisible()
    expect(scriptBody).toEqual({ recommendation_ids: ['unused_index:public.idx_001', 'unused_index:public.idx_060'] })

    await page.getByTestId('select-all-recs').check()
    await expect(page.getByTestId('selected-count')).toHaveText(`${N} de ${N} seleccionadas`)
    await page.locator('[data-rec-id="unused_index:public.idx_060"]').getByTestId('rec-checkbox').uncheck()
    await expect(page.getByTestId('selected-count')).toHaveText(`${N - 1} de ${N} seleccionadas`)
    await page.getByTestId('generate-script-btn').click()
    await expect.poll(() => scriptBody?.select_all).toBe(true)
    expect(scriptBody?.exclude_ids).toEqual(['unused_index:public.idx_060'])
    // changing the filters drops a "select all" made for other filters
    await page.getByTestId('rec-filter-severity').selectOption('high')
    await expect(page.getByTestId('selected-count')).toHaveText('0 de 40 seleccionadas')
  })
})
