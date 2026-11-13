import { test, expect } from './fixtures'

test.describe('Navegación y layout', () => {
  test('/ redirige a /charts y el sidebar muestra conexión, motor y navegación', async ({ page }) => {
    await page.goto('/')
    await expect(page).toHaveURL(/\/charts$/)
    await expect(page.getByTestId('app-title')).toHaveText('Tyrux')
    await expect(page.getByTestId('app-subtitle')).toHaveText('DB Monitor')
    await expect(page.getByTestId('connection-select')).toBeVisible()
    await expect(page.getByTestId('engine-badge')).toHaveText('PostgreSQL')
    for (const id of ['connections', 'charts', 'technical', 'optimize']) {
      await expect(page.getByTestId(`nav-${id}`)).toBeVisible()
    }
    await expect(page.getByTestId('nav-charts')).toHaveClass(/text-brand/)
  })

  test('cada enlace del sidebar navega a su página', async ({ page }) => {
    await page.goto('/charts')
    const targets: [string, RegExp, string][] = [
      ['connections', /\/connections$/, 'Conexiones'],
      ['technical', /\/technical$/, 'Técnico'],
      ['optimize', /\/optimize$/, 'Optimización'],
      ['charts', /\/charts$/, 'Gráficos'],
    ]
    for (const [id, url, title] of targets) {
      await page.getByTestId(`nav-${id}`).click()
      await expect(page).toHaveURL(url)
      await expect(page.getByRole('heading', { name: title, level: 1 })).toBeVisible()
    }
  })

  test('ruta desconocida vuelve a /charts y la recarga profunda funciona', async ({ page }) => {
    await page.goto('/no-existe')
    await expect(page).toHaveURL(/\/charts$/)
    await page.goto('/technical')
    await expect(page.getByTestId('tab-active')).toBeVisible()
    await page.reload()
    await expect(page.getByTestId('tab-active')).toBeVisible()
  })

  test('el selector de conexión persiste en localStorage', async ({ page }) => {
    await page.goto('/charts')
    await expect(page.getByTestId('connection-select')).toHaveValue('1')
    expect(await page.evaluate(() => localStorage.getItem('dbmonitor.connectionId'))).toBe('1')
  })

  test('sin conexiones seleccionadas las páginas muestran empty-state con acceso a Conexiones', async ({ page }) => {
    // Simulate "no connections" by intercepting the list endpoint.
    await page.route('**/api/connections', (route) => {
      if (route.request().method() === 'GET') return route.fulfill({ json: [] })
      return route.continue()
    })
    await page.goto('/charts')
    await expect(page.getByTestId('empty-state')).toBeVisible()
    await page.getByTestId('goto-connections').click()
    await expect(page).toHaveURL(/\/connections$/)
    await expect(page.getByTestId('empty-add-btn')).toBeVisible()
  })
})
