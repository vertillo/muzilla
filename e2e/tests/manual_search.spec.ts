import { test, expect } from './fixtures'

test('manual candidate search keeps a stable ReviewBundle and reports provider state', async ({ page, muzilla }) => {
  await muzilla.scanOneFile()
  const reviewId = await muzilla.createManualReview()

  await page.goto(`${muzilla.baseUrl}/reviews/${reviewId}`)
  await expect(page.getByRole('heading', { name: 'Find a candidate' })).toBeVisible({ timeout: 10_000 })
  await expect(page.getByText('MusicBrainz (ready)')).toBeVisible()
  await expect(page.getByText('Discogs (not configured)')).toBeVisible()

  await page.getByLabel('Title').fill('Starálfur')
  await page.getByRole('button', { name: 'Search' }).click()

  await expect(page.getByText('MusicBrainz: Results found')).toBeVisible({ timeout: 10_000 })
  await expect(page.getByRole('button', { name: 'Use this result' })).toBeVisible()
  await page.getByRole('button', { name: 'Use this result' }).click()
  await expect(page.getByRole('button', { name: 'Selected' })).toBeVisible({ timeout: 10_000 })
  await expect(page).toHaveURL(new RegExp(`/reviews/${reviewId}$`))
})
