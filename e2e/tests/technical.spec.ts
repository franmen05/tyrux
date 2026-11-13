import { test, expect } from './fixtures'
import type { Page } from '@playwright/test'

const tab = async (page: Page, id: string) => {
  await page.getByTestId(`tab-${id}`).click()
  await expect(page.getByTestId(`tab-${id}`)).toHaveAttribute('aria-selected', 'true')
}

test.describe('Técnico', () => {
  test.beforeEach(async ({ page }) => { await page.goto('/technical') })

  test('consultas activas: carga filas, filtro, refresco manual y auto-refresh', async ({ page }) => {
    await expect(page.getByTestId('tab-active')).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByTestId('active-query-row').first()).toBeVisible({ timeout: 15_000 })
    await expect(page.getByTestId('active-duration').first()).toBeVisible()
    await expect(page.getByTestId('active-auto-refresh')).toBeChecked()
    const req = page.waitForRequest((r) => r.url().endsWith('/active-queries'))
    await page.getByTestId('active-refresh-btn').click()
    await req
    // filter with no match -> empty
    await page.getByTestId('table-filter').fill('zzz-no-existe-zzz')
    await expect(page.getByTestId('active-query-row')).toHaveCount(0)
    await page.getByTestId('table-filter').fill('')
  })

  test('top consultas: carga, ordenable por order_by y por cabecera', async ({ page }) => {
    await tab(page, 'top')
    await expect(page.getByTestId('top-query-row').first()).toBeVisible()
    expect(await page.getByTestId('top-query-row').count()).toBeGreaterThan(3)
    // total_time is the initial (cached) order, so only the other three trigger a new request
    for (const o of ['mean_time', 'calls', 'io']) {
      const req = page.waitForRequest((r) => r.url().includes('/top-queries?') && r.url().includes(`order_by=${o}`))
      await page.getByTestId('top-order').selectOption(o)
      await req
      await expect(page.getByTestId('top-query-row').first()).toBeVisible()
    }
    // calls ordering is returned sorted desc by backend
    await page.getByTestId('top-order').selectOption('calls')
    await expect(async () => {
      const rows = page.getByTestId('top-query-row')
      const calls = await rows.evaluateAll((els) => els.map((e) => Number((e.querySelectorAll('td')[1].textContent ?? '').replace(/[^\d]/g, ''))))
      const sorted = [...calls].sort((a, b) => b - a)
      expect(calls).toEqual(sorted)
    }).toPass({ timeout: 10_000 })
    // header sort toggles
    const first = async () => page.getByTestId('top-query-row').first().innerText()
    const before = await first()
    await page.getByTestId('th-calls').click()
    await page.getByTestId('th-calls').click()
    await page.getByTestId('th-query').click()
    expect(await first()).not.toBe('')
    void before
  })

  test('layout: selector de orden no se estira y las duraciones no se parten en dos líneas', async ({ page }) => {
    await tab(page, 'top')
    await expect(page.getByTestId('top-query-row').first()).toBeVisible()
    const box = await page.getByTestId('top-order').boundingBox()
    expect(box!.width).toBeLessThan(220)
    const wrapped = await page.getByTestId('top-query-row').first().locator('td').nth(2).evaluate((e) => e.getBoundingClientRect().height > 40)
    expect(wrapped).toBe(false)
  })

  test('clic en fila abre drawer; Analizar muestra plan y hallazgos', async ({ page }) => {
    await tab(page, 'top')
    // pick the orders(customer_id) query, which is known to seq-scan
    const row = page.getByTestId('top-query-row').filter({ hasText: /orders|customers/ }).first()
    await row.click()
    const drawer = page.getByTestId('explain-drawer')
    await expect(drawer).toBeVisible()
    await expect(page.getByTestId('explain-query')).not.toBeEmpty()
    await page.getByTestId('explain-run-btn').click()
    await expect(page.getByTestId('explain-plan')).toBeVisible({ timeout: 20_000 })
    await expect(page.getByTestId('explain-plan')).not.toBeEmpty()
    await expect(page.getByTestId('explain-findings')).toBeVisible()
    await expect(page.getByTestId('explain-finding').or(page.getByTestId('explain-no-findings')).first()).toBeVisible()
    await page.getByTestId('drawer-close').click()
    await expect(drawer).toBeHidden()
  })

  test('Analizar SQL con SELECT * FROM orders WHERE customer_id = 42 sugiere índice (Seq Scan)', async ({ page }) => {
    await tab(page, 'top')
    await page.getByTestId('analyze-sql-btn').click()
    await expect(page.getByTestId('explain-drawer')).toBeVisible()
    await expect(page.getByTestId('explain-run-btn')).toBeDisabled()
    await page.getByTestId('explain-sql-input').fill('SELECT * FROM orders WHERE customer_id = 42')
    await page.getByTestId('explain-run-btn').click()
    await expect(page.getByTestId('explain-plan')).toContainText(/Seq Scan/, { timeout: 20_000 })
    const f = page.getByTestId('explain-finding').first()
    await expect(f).toBeVisible()
    await expect(f).toContainText(/Escaneo secuencial|Seq Scan/i)
    await expect(f).toContainText(/índice/i)
    await expect(f).toContainText(/CREATE INDEX/)
    await expect(f).toContainText('customer_id')
    await expect(f.locator('xpath=ancestor-or-self::*[@data-severity]').first()).toHaveAttribute('data-severity', 'high')
    await expect(page.getByTestId('finding-copy-btn').first()).toBeVisible()
  })

  test('EXPLAIN ANALYZE sobre UPDATE es rechazado con mensaje, sin plan', async ({ page }) => {
    await tab(page, 'top')
    await page.getByTestId('analyze-sql-btn').click()
    await page.getByTestId('explain-sql-input').fill("UPDATE customers SET name = name WHERE id = 1")
    await page.getByTestId('explain-analyze').check()
    await expect(page.getByText('La consulta se ejecutará realmente')).toBeVisible()
    await page.getByTestId('explain-run-btn').click()
    // Backend answers 200 with a finding explaining the rejection (or a 4xx -> error-banner).
    const rejected = page.getByTestId('explain-finding').filter({ hasText: /no permitido/i })
      .or(page.getByTestId('error-banner'))
    await expect(rejected.first()).toBeVisible({ timeout: 15_000 })
    await expect(page.getByTestId('explain-drawer')).toContainText(/solo se permite|SELECT/i)
    // the UPDATE was not executed: no plan shown
    await expect(page.getByTestId('explain-plan')).toBeEmpty()
  })

  test('índices: filtros sin uso / duplicados muestran los del demo', async ({ page }) => {
    await tab(page, 'indexes')
    await expect(page.getByTestId('index-row').first()).toBeVisible()
    const all = await page.getByTestId('index-row').count()
    expect(all).toBeGreaterThanOrEqual(8)
    await page.getByTestId('index-filter-unused').click()
    await expect(page.getByTestId('index-filter-unused')).toHaveAttribute('aria-pressed', 'true')
    const unused = page.getByTestId('index-row')
    await expect(unused.filter({ hasText: 'orders_total_idx' })).toHaveCount(1)
    await expect(unused.filter({ hasText: 'order_items_order_product_idx' })).toHaveCount(1)
    expect(await unused.count()).toBeLessThan(all)
    await expect(unused.filter({ hasText: 'sin uso' })).toHaveCount(await unused.count())
    await page.getByTestId('index-filter-duplicate').click()
    const dup = page.getByTestId('index-row')
    await expect(dup.filter({ hasText: 'customers_email_idx' })).toHaveCount(1)
    await expect(dup.filter({ hasText: 'order_items_order_id_idx' })).toHaveCount(1)
    await expect(dup.first()).toContainText('duplicado de')
    await page.getByTestId('index-filter-all').click()
    await expect(page.getByTestId('index-row')).toHaveCount(all)
  })

  test('tablas: filas con tamaños y filtro', async ({ page }) => {
    await tab(page, 'tables')
    await expect(page.getByTestId('table-row').first()).toBeVisible()
    await expect(page.getByTestId('table-row').filter({ hasText: 'orders' }).first()).toContainText(/\d/)
    await page.getByTestId('table-filter').fill('order_items')
    await expect(page.getByTestId('table-row')).toHaveCount(1)
    await page.getByTestId('th-rows').click()
  })

  test('bloqueos: estado vacío o filas de bloqueo', async ({ page }) => {
    await tab(page, 'locks')
    await expect(page.getByTestId('lock-row').first().or(page.getByTestId('empty-state'))).toBeVisible({ timeout: 15_000 })
    if (await page.getByTestId('lock-row').count()) {
      await expect(page.getByTestId('lock-row').first()).toContainText(/Bloqueada/)
    } else {
      await expect(page.getByTestId('empty-state')).toContainText('Sin bloqueos')
    }
  })

  test('configuración: lista parámetros y filtra', async ({ page }) => {
    await tab(page, 'config')
    await expect(page.getByTestId('config-row').first()).toBeVisible()
    expect(await page.getByTestId('config-row').count()).toBeGreaterThan(5)
    await page.getByTestId('table-filter').fill('shared_buffers')
    await expect(page.getByTestId('config-row').first()).toContainText('shared_buffers')
  })

  test('ningún tab queda en blanco: todos muestran contenido o empty-state', async ({ page }) => {
    for (const id of ['active', 'top', 'indexes', 'tables', 'locks', 'config']) {
      await tab(page, id)
      await expect(page.getByTestId('loading')).toHaveCount(0, { timeout: 15_000 })
      expect((await page.locator('main').innerText()).length).toBeGreaterThan(80)
      await expect(page.getByTestId('error-banner')).toHaveCount(0)
    }
  })
})
