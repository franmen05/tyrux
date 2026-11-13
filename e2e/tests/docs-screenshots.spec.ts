/**
 * Regenerates the screenshots used by the README and the wiki (docs/images/).
 * Skipped by default; run with:  DOCS_SHOTS=1 npx playwright test docs-screenshots
 */
import { test, expect, selectConnectionInStorage } from './fixtures'
import * as path from 'node:path'
import * as fs from 'node:fs'

const OUT = path.join(__dirname, '..', '..', 'docs', 'images')
const shot = (name: string) => path.join(OUT, `${name}.png`)

test.skip(!process.env.DOCS_SHOTS, 'set DOCS_SHOTS=1 to regenerate docs screenshots')
test.use({ viewport: { width: 1440, height: 900 } })
test.beforeAll(() => fs.mkdirSync(OUT, { recursive: true }))

for (const scheme of ['light', 'dark'] as const) {
  test(`gráficos (${scheme})`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme })
    await page.goto('/charts')
    await expect(page.getByTestId('chart-top-queries').locator('svg.recharts-surface')).toBeVisible({ timeout: 20_000 })
    await page.waitForTimeout(1500)
    await page.screenshot({ path: shot(`charts-${scheme}`) })
  })
}

test('conexiones y formulario', async ({ page }) => {
  await page.goto('/connections')
  await expect(page.getByTestId('connection-row').first()).toBeVisible()
  await page.screenshot({ path: shot('connections') })
  await page.getByTestId('add-connection-btn').click()
  await page.getByTestId('conn-name').fill('Producción')
  await page.getByTestId('conn-host').fill('host.containers.internal')
  await page.getByTestId('conn-port').fill('55433')
  await page.getByTestId('conn-database').fill('shop')
  await page.getByTestId('conn-username').fill('dbmon')
  await page.getByTestId('conn-password').fill('dbmon')
  await page.getByTestId('conn-test-btn').click()
  await expect(page.getByTestId('conn-test-result')).toBeVisible()
  await page.screenshot({ path: shot('connection-form') })
})

test('técnico: activas, top, índices, tablas, bloqueos y explain', async ({ page }) => {
  await page.goto('/technical')
  await expect(page.getByTestId('active-query-row').first()).toBeVisible({ timeout: 20_000 })
  await page.screenshot({ path: shot('technical-active') })
  await page.getByTestId('tab-top').click()
  await expect(page.getByTestId('top-query-row').first()).toBeVisible()
  await page.screenshot({ path: shot('technical-top') })
  await page.getByTestId('tab-indexes').click()
  await expect(page.getByTestId('index-row').first()).toBeVisible()
  await page.screenshot({ path: shot('technical-indexes') })
  await page.getByTestId('tab-tables').click()
  await expect(page.getByTestId('table-row').first()).toBeVisible()
  await page.screenshot({ path: shot('technical-tables') })
  await page.getByTestId('tab-top').click()
  await page.getByTestId('analyze-sql-btn').click()
  await page.getByTestId('explain-sql-input').fill('SELECT * FROM orders WHERE customer_id = 42 ORDER BY created_at DESC')
  await page.getByTestId('explain-run-btn').click()
  await expect(page.getByTestId('explain-plan')).toBeVisible({ timeout: 20_000 })
  await page.waitForTimeout(500)
  await page.screenshot({ path: shot('explain') })
})

test('optimización y script', async ({ page }) => {
  await page.goto('/optimize')
  await expect(page.getByTestId('recommendation-item').first()).toBeVisible({ timeout: 20_000 })
  await page.screenshot({ path: shot('optimize') })
  await page.getByTestId('select-all-recs').check()
  await page.getByTestId('generate-script-btn').click()
  await expect(page.getByTestId('script-output')).toBeVisible()
  await page.getByTestId('script-output').scrollIntoViewIfNeeded()
  await page.waitForTimeout(300)
  await page.screenshot({ path: shot('optimize-script') })
})

test('oracle: gráficos y top consultas', async ({ page }) => {
  test.skip(!process.env.E2E_ORACLE, 'needs E2E_ORACLE=1 and connection 101')
  await selectConnectionInStorage(page, 101)
  await page.goto('/charts')
  await expect(page.getByTestId('engine-badge')).toHaveText('Oracle')
  await expect(page.getByTestId('chart-sessions').locator('svg.recharts-surface').first()).toBeVisible({ timeout: 20_000 })
  await page.waitForTimeout(1500)
  await page.screenshot({ path: shot('oracle-charts') })
  await page.goto('/technical')
  await page.getByTestId('tab-top').click()
  await expect(page.getByTestId('top-query-row').first()).toBeVisible({ timeout: 20_000 })
  await page.screenshot({ path: shot('oracle-top') })
})
