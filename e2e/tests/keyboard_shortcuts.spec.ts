import { test, expect } from './fixtures'

test('the review screen has a persistent hint and a ? overlay listing every shortcut', async ({
  page,
  muzilla,
}) => {
  await muzilla.scanOneFile('shortcut-hint.mp3')
  const reviewId = await muzilla.createManualReview()

  await page.goto(`${muzilla.baseUrl}/reviews/${reviewId}?returnTo=%2Freviews`)
  await expect(page.getByRole('heading', { name: 'shortcut-hint.mp3' })).toBeVisible({ timeout: 10_000 })

  // The header has a persistent Scorciatoie button, not a footer hint
  await expect(page.getByRole('button', { name: 'Scorciatoie' })).toBeVisible()

  await page.keyboard.press('?')
  await expect(page.getByRole('dialog', { name: 'Scorciatoie' })).toBeVisible()
  await expect(page.getByText('sposta focus e viewport fra le modifiche')).toBeVisible()
  await expect(page.getByText('apre la review precedente o successiva')).toBeVisible()

  // Escape closes the overlay
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog', { name: 'Scorciatoie' })).not.toBeVisible()

  // Clicking the header button opens it too.
  await page.getByRole('button', { name: 'Scorciatoie' }).click()
  await expect(page.getByRole('dialog', { name: 'Scorciatoie' })).toBeVisible()
})
