import { test, expect } from './fixtures'

test('the reviews inbox opens a file-first detail and Close restores its filter', async ({ page, muzilla }) => {
  await muzilla.scanOneFile('01-source.flac')
  const reviewId = await muzilla.createManualReview()

  await page.goto(`${muzilla.baseUrl}/reviews?q=01-source`)
  await expect(page.getByRole('heading', { name: 'Revisioni' })).toBeVisible({ timeout: 10_000 })
  await expect(page.getByRole('button', { name: 'Apri revisione: 01-source.flac' })).toBeVisible()
  await expect(page.getByText(/Percorso sorgente|\/library\/01-source\.flac/)).toBeVisible()

  await page.getByRole('button', { name: `Apri revisione: 01-source.flac` }).click()
  await expect(page).toHaveURL(new RegExp(`/reviews/${reviewId}\\?returnTo=`))
  await expect(page.getByRole('heading', { name: '01-source.flac' })).toBeVisible()
  await expect(page.getByText('Tag metadata')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Applica 0 modifiche' })).toBeDisabled()

  await page.getByRole('button', { name: 'Chiudi' }).click()
  await expect(page).toHaveURL(/\/reviews\?q=01-source#review-/)
  await expect(page.getByRole('button', { name: 'Apri revisione: 01-source.flac' })).toBeVisible()
})

test('quick reject archives a review without enabling apply and can be reopened from archived', async ({ page, muzilla }) => {
  await muzilla.scanOneFile('reject-source.flac')
  const reviewId = await muzilla.createManualReview()

  await page.goto(`${muzilla.baseUrl}/reviews`)
  await page.getByRole('button', { name: 'Rifiuta proposte' }).click()
  await expect(page.getByRole('button', { name: /Apri revisione: reject-source.flac/ })).not.toBeVisible()

  await page.getByLabel('Stato').selectOption('applied,discarded')
  await page.getByRole('button', { name: /Apri revisione: reject-source.flac/ }).click()
  await expect(page).toHaveURL(new RegExp(`/reviews/${reviewId}`))
  await expect(page.getByText('Stato: discarded')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Applica 0 modifiche' })).toBeDisabled()
  await page.getByRole('button', { name: 'In attesa' }).click()
  await expect(page.getByText('Stato: ready')).toBeVisible()
})

test('mobile shell exposes the drawer navigation without hiding the content owner', async ({ page, muzilla }) => {
  await muzilla.scanOneFile()
  await page.setViewportSize({ width: 390, height: 640 })
  await page.goto(`${muzilla.baseUrl}/reviews`)

  await page.getByRole('button', { name: 'Menu' }).click()
  await expect(page.getByRole('navigation', { name: 'Navigazione principale' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Revisioni' })).toBeVisible()
  await page.getByRole('button', { name: 'Chiudi navigazione' }).click()
  await expect(page.getByRole('heading', { name: 'Revisioni' })).toBeVisible()
})

test('review stays usable at desktop, tablet, and a compact 200% zoom-equivalent viewport', async ({ page, muzilla }) => {
  await muzilla.scanOneFile('responsive-source.flac')
  const reviewId = await muzilla.createManualReview()

  for (const viewport of [
    { width: 1280, height: 800 },
    { width: 900, height: 720 },
    { width: 640, height: 720 },
  ]) {
    await page.setViewportSize(viewport)
    await page.goto(`${muzilla.baseUrl}/reviews/${reviewId}?returnTo=%2Freviews`)
    await expect(page.getByRole('heading', { name: 'responsive-source.flac' })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  }

  const actionBar = page.getByRole('button', { name: 'Applica 0 modifiche' }).locator('xpath=../../..')
  await expect(actionBar).toHaveClass(/safe-area-inset-bottom/)
})

test('an uncertain collection opens a constrained review from the file detail', async ({ page, muzilla }) => {
  await muzilla.scanOneFile('uncertain-grouping.mp3')
  const { reviewId, trackId } = await muzilla.createUncertainGroupingReview()

  await page.goto(`${muzilla.baseUrl}/catalog/${trackId}`)
  await expect(page.getByRole('button', { name: 'Risolvi raccolta' })).toBeVisible()
  await page.getByRole('button', { name: 'Risolvi raccolta' }).click()

  await expect(page).toHaveURL(new RegExp(`/reviews/${reviewId}`))
  await expect(page.getByRole('heading', { name: 'Risolvi raccolta' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Scegli' })).toHaveCount(3)
  await expect(page.getByRole('button', { name: 'Applica 0 modifiche' })).toBeDisabled()
})
