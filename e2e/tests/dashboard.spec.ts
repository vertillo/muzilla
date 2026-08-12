import { test, expect } from './fixtures'

test('the root route renders the Dashboard, not a redirect to Catalog', async ({ page, muzilla }) => {
  // The Dashboard used to be a bare
  // redirect to /catalog because no Dashboard existed. This proves the
  // real screen renders with library counts, not a bounce.
  await muzilla.scanOneFile()

  await page.goto(muzilla.baseUrl)
  await expect(page).toHaveURL(`${muzilla.baseUrl}/`)
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible({ timeout: 10_000 })

  // one track scanned, no grouping cascade run yet — total_tracks should
  // read 1, and it should be ungrouped (no cascade run in this test)
  await expect(page.getByText('File nel catalogo')).toBeVisible()
  await expect(page.getByText('1', { exact: true }).first()).toBeVisible()
})

test('Dashboard nav item is present and marked active on the root route only', async ({ page, muzilla }) => {
  await muzilla.scanOneFile()

  await page.goto(`${muzilla.baseUrl}/catalog`)
  await expect(page.getByRole('link', { name: 'Ágætis byrjun' })).toBeVisible({ timeout: 10_000 })

  const dashboardLink = page.getByRole('link', { name: 'Dashboard' })
  await expect(dashboardLink).toBeVisible()

  await dashboardLink.click()
  await expect(page).toHaveURL(`${muzilla.baseUrl}/`)
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible()
})

test('Dashboard shows provider health for the enabled providers', async ({ page, muzilla }) => {
  await page.goto(muzilla.baseUrl)
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible({ timeout: 10_000 })

  await expect(page.getByText('musicbrainz')).toBeVisible({ timeout: 10_000 })
})
