import { test, expect, apiCreateConnection, apiDeleteConnection, selectConnectionInStorage } from './fixtures'

test.describe('Manejo de errores', () => {
  for (const [path, label] of [['/charts', 'Gráficos'], ['/technical', 'Técnico'], ['/optimize', 'Optimización']] as const) {
    test(`${label}: conexión a puerto cerrado muestra error-banner con mensaje del backend y Reintentar`, async ({ page, request }) => {
      const c = await apiCreateConnection(request, { port: 1 })
      try {
        await selectConnectionInStorage(page, c.id)
        await page.goto(path)
        const banner = page.getByTestId('error-banner').first()
        await expect(banner).toBeVisible({ timeout: 20_000 })
        await expect(banner).toContainText('No se pudo conectar a PostgreSQL')
        await expect(banner).toContainText(/host\.containers\.internal:1/)
        const retry = banner.getByTestId('retry-btn')
        await expect(retry).toBeVisible()
        // page is not blank: header + sidebar still render
        await expect(page.getByTestId('nav-charts')).toBeVisible()
        expect((await page.locator('main').innerText()).length).toBeGreaterThan(40)
        // retry really re-issues the request
        const req = page.waitForRequest((r) => /\/api\/connections\/\d+\/(overview|active-queries|recommendations|metrics)/.test(r.url()))
        await retry.click()
        await req
        await expect(page.getByTestId('error-banner').first()).toBeVisible({ timeout: 20_000 })
      } finally {
        await apiDeleteConnection(request, c.id)
      }
    })
  }

  test('Probar conexión de la fila con puerto cerrado muestra el error en la fila', async ({ page, request }) => {
    const c = await apiCreateConnection(request, { port: 1 })
    try {
      await page.goto('/connections')
      const row = page.getByTestId('connection-row').filter({ hasText: c.name })
      await row.getByTestId('conn-row-test-btn').click()
      await expect(row.getByTestId('conn-row-test-result')).toContainText(/No se pudo conectar|connection/i, { timeout: 20_000 })
    } finally {
      await apiDeleteConnection(request, c.id)
    }
  })

  test('error 500 genérico del backend no deja la página en blanco', async ({ page }) => {
    await page.route('**/api/connections/1/recommendations', (r) => r.fulfill({ status: 500, json: { detail: 'boom-e2e' } }))
    await page.goto('/optimize')
    await expect(page.getByTestId('error-banner')).toContainText('boom-e2e')
    await expect(page.getByTestId('retry-btn')).toBeVisible()
    await page.unroute('**/api/connections/1/recommendations')
    await page.getByTestId('retry-btn').click()
    await expect(page.getByTestId('recommendation-item').first()).toBeVisible()
  })

  test('backend inalcanzable (red caída) muestra error-banner', async ({ page }) => {
    await page.route('**/api/connections/1/overview', (r) => r.abort('connectionrefused'))
    await page.goto('/charts')
    await expect(page.getByTestId('error-banner').first()).toBeVisible({ timeout: 15_000 })
  })

  test('error del backend en la lista de conexiones muestra banner con Reintentar', async ({ page }) => {
    let fail = true
    await page.route('**/api/connections', (r) => {
      if (r.request().method() !== 'GET') return r.continue()
      return fail ? r.fulfill({ status: 502, json: { detail: 'lista-caida-e2e' } }) : r.continue()
    })
    await page.goto('/connections')
    await expect(page.getByTestId('error-banner').first()).toContainText('lista-caida-e2e', { timeout: 20_000 })
    fail = false
    await page.getByTestId('retry-btn').first().click()
    await expect(page.getByTestId('connection-row').first()).toBeVisible()
  })
})
