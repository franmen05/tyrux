import { test, expect, DEMO, uniqueName, apiCreateConnection, apiDeleteConnection, apiCleanupE2E } from './fixtures'
import type { Page } from '@playwright/test'

async function fillDemo(page: Page, name: string, over: Partial<typeof DEMO> = {}) {
  const d = { ...DEMO, ...over }
  await page.getByTestId('conn-name').fill(name)
  await page.getByTestId('conn-host').fill(d.host)
  await page.getByTestId('conn-port').fill(String(d.port))
  await page.getByTestId('conn-database').fill(d.database)
  await page.getByTestId('conn-username').fill(d.username)
  await page.getByTestId('conn-password').fill(d.password)
}

test.describe('Conexiones', () => {
  test.beforeEach(async ({ page }) => { await page.goto('/connections') })

  test('lista la conexión demo con acciones', async ({ page }) => {
    const row = page.getByTestId('connection-row').filter({ hasText: 'Demo Postgres' })
    await expect(row).toBeVisible()
    await expect(row.getByTestId('conn-edit-btn')).toBeVisible()
    await expect(row.getByTestId('conn-row-test-btn')).toBeVisible()
    await expect(row.getByText('PostgreSQL')).toBeVisible()
    await expect(row.getByText('Monitoreando')).toBeVisible()
  })

  test('validación: nombre vacío deshabilita Guardar y Probar', async ({ page }) => {
    await page.getByTestId('add-connection-btn').click()
    await expect(page.getByTestId('connection-form')).toBeVisible()
    await page.getByTestId('conn-database').fill('shop')
    await page.getByTestId('conn-username').fill('dbmon')
    await expect(page.getByTestId('conn-name')).toHaveValue('')
    await expect(page.getByTestId('conn-save-btn')).toBeDisabled()
    await expect(page.getByTestId('conn-test-btn')).toBeDisabled()
    // Enter in the form must not submit while invalid
    await page.getByTestId('conn-username').press('Enter')
    await expect(page.getByTestId('connection-form')).toBeVisible()
    await page.getByTestId('conn-name').fill('algo')
    await expect(page.getByTestId('conn-save-btn')).toBeEnabled()
    await page.getByTestId('conn-name').fill('   ')
    await expect(page.getByTestId('conn-save-btn')).toBeDisabled()
  })

  test('validación: puerto fuera de rango deshabilita Guardar', async ({ page }) => {
    await page.getByTestId('add-connection-btn').click()
    await fillDemo(page, 'x')
    await page.getByTestId('conn-port').fill('70000')
    await expect(page.getByTestId('conn-save-btn')).toBeDisabled()
    await page.getByTestId('conn-port').fill('5432')
    await expect(page.getByTestId('conn-save-btn')).toBeEnabled()
  })

  test('toggle de motor cambia puerto por defecto 5432<->1521 y la etiqueta', async ({ page }) => {
    await page.getByTestId('add-connection-btn').click()
    const port = page.getByTestId('conn-port')
    await expect(page.getByTestId('conn-engine-postgres')).toHaveAttribute('aria-pressed', 'true')
    await expect(port).toHaveValue('5432')
    await expect(page.getByText('Base de datos', { exact: true })).toBeVisible()
    await page.getByTestId('conn-engine-oracle').click()
    await expect(page.getByTestId('conn-engine-oracle')).toHaveAttribute('aria-pressed', 'true')
    await expect(port).toHaveValue('1521')
    await expect(page.getByText('Service name', { exact: true })).toBeVisible()
    await expect(page.getByText('Base de datos', { exact: true })).toHaveCount(0)
    await page.getByTestId('conn-engine-postgres').click()
    await expect(port).toHaveValue('5432')
    await expect(page.getByText('Base de datos', { exact: true })).toBeVisible()
    // customised port is preserved on toggle
    await port.fill('6543')
    await page.getByTestId('conn-engine-oracle').click()
    await expect(port).toHaveValue('6543')
  })

  test('Probar conexión: éxito muestra versión y latencia', async ({ page }) => {
    await page.getByTestId('add-connection-btn').click()
    await fillDemo(page, 'e2e-test-ok')
    await page.getByTestId('conn-test-btn').click()
    const res = page.getByTestId('conn-test-result')
    await expect(res).toContainText('Conexión correcta')
    await expect(res).toContainText(/PostgreSQL \d+/)
    await expect(res).toContainText(/Latencia: [\d.,]+ ms/)
  })

  test('Probar conexión: contraseña incorrecta muestra el error', async ({ page }) => {
    await page.getByTestId('add-connection-btn').click()
    await fillDemo(page, 'e2e-test-bad', { password: 'incorrecta-xyz' })
    await page.getByTestId('conn-test-btn').click()
    const res = page.getByTestId('conn-test-result')
    await expect(res).toContainText('Falló la conexión')
    await expect(res).toContainText(/password|contraseña|authentication|autenticaci/i)
    await expect(page.getByTestId('connection-form')).toBeVisible()
  })

  test('crear, editar conservando contraseña y eliminar con confirmación', async ({ page, request }) => {
    const name = uniqueName()
    const edited = `${name}-ed`
    try {
      // create
      await page.getByTestId('add-connection-btn').click()
      await fillDemo(page, name)
      await page.getByTestId('conn-save-btn').click()
      await expect(page.getByTestId('connection-form')).toBeHidden()
      await expect(page.getByTestId('toast').filter({ hasText: 'Conexión guardada' })).toBeVisible()
      const row = page.getByTestId('connection-row').filter({ hasText: name })
      await expect(row).toBeVisible()
      await expect(row.getByText('Seleccionada')).toBeVisible()
      // new connection is the selected one
      await expect(page.getByTestId('connection-select').locator('option:checked')).toHaveText(name)

      // edit keeping password (field left empty)
      await row.getByTestId('conn-edit-btn').click()
      await expect(page.getByTestId('conn-password')).toHaveAttribute('placeholder', 'sin cambios')
      await expect(page.getByTestId('conn-password')).toHaveValue('')
      await expect(page.getByTestId('conn-name')).toHaveValue(name)
      await page.getByTestId('conn-name').fill(edited)
      await page.getByTestId('conn-save-btn').click()
      await expect(page.getByTestId('toast').filter({ hasText: 'Conexión actualizada' })).toBeVisible()
      const row2 = page.getByTestId('connection-row').filter({ hasText: edited })
      await expect(row2).toBeVisible()

      // the stored password still works
      await row2.getByTestId('conn-row-test-btn').click()
      await expect(row2.getByTestId('conn-row-test-result')).toContainText(/PostgreSQL|correcta|OK/i)
      await expect(row2.getByTestId('conn-row-test-result')).not.toContainText(/fall|error|password/i)

      // delete: cancel first, then accept
      await row2.getByTestId('conn-delete-btn').click()
      await expect(page.getByTestId('confirm-dialog')).toContainText(edited)
      await page.getByTestId('confirm-cancel').click()
      await expect(page.getByTestId('confirm-dialog')).toBeHidden()
      await expect(row2).toBeVisible()
      await row2.getByTestId('conn-delete-btn').click()
      await page.getByTestId('confirm-accept').click()
      await expect(page.getByTestId('toast').filter({ hasText: 'Conexión eliminada' })).toBeVisible()
      await expect(page.getByTestId('connection-row').filter({ hasText: edited })).toHaveCount(0)
    } finally {
      await apiCleanupE2E(request)
    }
  })

  test('"Usar" selecciona otra conexión y actualiza el sidebar', async ({ page, request }) => {
    const c = await apiCreateConnection(request)
    try {
      await page.reload()
      const row = page.getByTestId('connection-row').filter({ hasText: c.name })
      await row.getByTestId('conn-select-btn').click()
      await expect(page.getByTestId('connection-select')).toHaveValue(String(c.id))
      await expect(row.getByText('Seleccionada')).toBeVisible()
    } finally {
      await apiDeleteConnection(request, c.id)
    }
  })

  test('Cancelar cierra el formulario sin crear nada', async ({ page }) => {
    const before = await page.getByTestId('connection-row').count()
    await page.getByTestId('add-connection-btn').click()
    await page.getByTestId('conn-name').fill('e2e-no-guardar')
    await page.getByRole('button', { name: 'Cancelar' }).click()
    await expect(page.getByTestId('connection-form')).toBeHidden()
    await expect(page.getByTestId('connection-row')).toHaveCount(before)
  })
})
