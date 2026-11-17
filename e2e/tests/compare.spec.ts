import { test, expect, ORACLE, apiCreateConnection, apiDeleteConnection, selectConnectionInStorage } from './fixtures'

test.describe('Comparar índices', () => {
  test('misma base como origen y destino: todo «Igual», nada copiable', async ({ page, request }) => {
    const copy = await apiCreateConnection(request)   // second connection to the same demo Postgres
    try {
      await selectConnectionInStorage(page, 1)
      await page.goto('/')
      await page.getByTestId('nav-compare').click()
      await expect(page).toHaveURL(/\/compare$/)
      await expect(page.getByTestId('no-exec-notice')).toContainText('no ejecuta cambios')

      await page.getByTestId('compare-source').selectOption('1')
      await page.getByTestId('compare-target').selectOption('1')
      await expect(page.getByTestId('compare-same-warning')).toBeVisible()
      await expect(page.getByTestId('compare-run-btn')).toBeDisabled()

      await page.getByTestId('compare-target').selectOption(String(copy.id))
      await page.getByTestId('compare-run-btn').click()
      await expect(page.getByTestId('compare-row').first()).toBeVisible({ timeout: 30_000 })
      await expect(page.getByTestId('compare-count-missing')).toContainText(': 0')
      await expect(page.getByTestId('compare-count-extra')).toContainText(': 0')
      await expect(page.locator('[data-testid="compare-status"]:not([data-status="same"])')).toHaveCount(0)
      await expect(page.getByTestId('compare-select-all')).toBeDisabled()
      await expect(page.getByTestId('compare-generate-btn')).toBeDisabled()
    } finally {
      await apiDeleteConnection(request, copy.id)
    }
  })

  test('PostgreSQL → Oracle (E2E_ORACLE=1): seleccionar todos y generar SQL ONLINE', async ({ page, request }) => {
    test.skip(!process.env.E2E_ORACLE, 'set E2E_ORACLE=1 to run the Oracle demo tests')
    const ora = await apiCreateConnection(request, { ...ORACLE })
    try {
      await selectConnectionInStorage(page, 1)
      await page.goto('/compare')
      await page.getByTestId('compare-source').selectOption('1')
      await page.getByTestId('compare-target').selectOption(String(ora.id))
      await page.getByTestId('compare-run-btn').click()
      await expect(page.getByTestId('compare-row').first()).toBeVisible({ timeout: 60_000 })

      await page.getByTestId('compare-filter-missing').click()
      await expect(page.locator('[data-testid="compare-status"]:not([data-status="missing"])')).toHaveCount(0)
      await page.getByTestId('compare-select-all').check()
      await expect(page.getByTestId('compare-selected-count')).not.toHaveText('0 seleccionados')
      await page.getByTestId('compare-generate-btn').click()
      const out = page.getByTestId('compare-script-output')
      await expect(out).toContainText('Script de copia de índices', { timeout: 60_000 })
      await expect(out).toContainText(/CREATE (UNIQUE )?INDEX .* ONLINE;/)

      const [dl] = await Promise.all([page.waitForEvent('download'), page.getByTestId('compare-download-btn').click()])
      expect(dl.suggestedFilename()).toBe('copiar-indices-oracle.sql')
    } finally {
      await apiDeleteConnection(request, ora.id)
    }
  })
})
