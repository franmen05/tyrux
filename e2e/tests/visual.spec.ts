import { test, expect } from './fixtures'
import * as path from 'node:path'
import * as fs from 'node:fs'

const SHOTS = path.join(__dirname, '..', 'screenshots')
fs.mkdirSync(SHOTS, { recursive: true })

const PAGES: [string, string, string][] = [
  ['connections', '/connections', 'connection-row'],
  ['charts', '/charts', 'chart-sessions'],
  ['technical-active', '/technical', 'active-query-row'],
  ['optimize', '/optimize', 'recommendation-item'],
]

for (const scheme of ['light', 'dark'] as const) {
  test.describe(`tema ${scheme}`, () => {
    // The app scrolls inside <main> (h-screen), so use a tall viewport to capture the whole page.
    test.use({ colorScheme: scheme, viewport: { width: 1440, height: 2300 } })

    for (const [name, url, ready] of PAGES) {
      test(`${name}: sin errores de consola y captura`, async ({ page }) => {
        await page.goto(url)
        await expect(page.getByTestId(ready).first()).toBeVisible({ timeout: 20_000 })
        if (name === 'charts') await expect(page.getByTestId('chart-top-queries').locator('svg.recharts-surface')).toBeVisible({ timeout: 15_000 })
        await page.waitForTimeout(500)
        // background and text must differ in luminance (basic readability sanity check)
        const { bg, fg } = await page.evaluate(() => {
          const cs = getComputedStyle(document.body)
          return { bg: cs.backgroundColor, fg: cs.color }
        })
        expect(bg).not.toBe(fg)
        const lum = (c: string) => { const m = c.match(/\d+(\.\d+)?/g)!.map(Number); return (0.2126 * m[0] + 0.7152 * m[1] + 0.0722 * m[2]) / 255 }
        const bgL = lum(bg), fgL = lum(fg)
        expect(Math.abs(bgL - fgL)).toBeGreaterThan(0.5)
        if (scheme === 'dark') expect(bgL).toBeLessThan(0.3)
        else expect(bgL).toBeGreaterThan(0.7)
        await page.screenshot({ path: path.join(SHOTS, `${name}-${scheme}.png`), fullPage: true })
      })
    }

    test('técnico (top, índices, tablas) y drawer de explain', async ({ page }) => {
      await page.goto('/technical')
      await page.getByTestId('tab-top').click()
      await expect(page.getByTestId('top-query-row').first()).toBeVisible()
      await page.screenshot({ path: path.join(SHOTS, `technical-top-${scheme}.png`), fullPage: true })
      await page.getByTestId('tab-indexes').click()
      await expect(page.getByTestId('index-row').first()).toBeVisible()
      await page.screenshot({ path: path.join(SHOTS, `technical-indexes-${scheme}.png`), fullPage: true })
      await page.getByTestId('tab-tables').click()
      await expect(page.getByTestId('table-row').first()).toBeVisible()
      await page.screenshot({ path: path.join(SHOTS, `technical-tables-${scheme}.png`), fullPage: true })
      await page.getByTestId('tab-top').click()
      await page.getByTestId('analyze-sql-btn').click()
      await page.getByTestId('explain-sql-input').fill('SELECT * FROM orders WHERE customer_id = 42')
      await page.getByTestId('explain-run-btn').click()
      await expect(page.getByTestId('explain-plan')).toBeVisible({ timeout: 20_000 })
      await page.screenshot({ path: path.join(SHOTS, `explain-drawer-${scheme}.png`) })
    })

    test('formulario de conexión y script generado', async ({ page }) => {
      await page.goto('/connections')
      await page.getByTestId('add-connection-btn').click()
      await page.getByTestId('conn-name').fill('e2e-visual')
      await page.getByTestId('conn-host').fill('host.containers.internal')
      await page.getByTestId('conn-port').fill('55433')
      await page.getByTestId('conn-database').fill('shop')
      await page.getByTestId('conn-username').fill('dbmon')
      await page.getByTestId('conn-password').fill('dbmon')
      await page.getByTestId('conn-test-btn').click()
      await expect(page.getByTestId('conn-test-result')).toBeVisible()
      await page.screenshot({ path: path.join(SHOTS, `connection-form-${scheme}.png`) })
      await page.goto('/optimize')
      await page.getByTestId('select-all-recs').check()
      await page.getByTestId('generate-script-btn').click()
      await expect(page.getByTestId('script-output')).toBeVisible()
      await page.screenshot({ path: path.join(SHOTS, `optimize-script-${scheme}.png`), fullPage: true })
    })
  })
}
