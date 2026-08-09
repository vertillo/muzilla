import { test, expect } from './fixtures'

test('catalog uses accessible sort headers and opens a file detail', async ({ page, muzilla }) => {
  await muzilla.scanOneFile()
  await page.goto(`${muzilla.baseUrl}/catalog`)

  await expect(page.getByRole('button', { name: /Titolo/ })).toBeVisible({ timeout: 10_000 })
  await expect(page.getByRole('link', { name: 'Ágætis byrjun' })).toBeVisible()
  await page.getByRole('link', { name: 'Ágætis byrjun' }).click()
  await expect(page.getByRole('heading', { name: 'Ágætis byrjun' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Rileggi file' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Cerca corrispondenze' })).toBeVisible()
})

test('legacy duplicate route redirects to the catalog tool with explicit evidence', async ({ page, muzilla }) => {
  await muzilla.scanOneFile()
  await page.goto(`${muzilla.baseUrl}/duplicates`)
  await expect(page).toHaveURL(/\/catalog\?tool=duplicates/)
  await expect(page.getByRole('heading', { name: 'Possibili duplicati' })).toBeVisible()
  await expect(page.getByText(/Muzilla non elimina nulla automaticamente/)).toBeVisible()
})

test('retired technical routes remain safe bookmark redirects', async ({ page, muzilla }) => {
  await page.goto(`${muzilla.baseUrl}/groups`)
  await expect(page).toHaveURL(/\/reviews\?issue=review/)

  await page.goto(`${muzilla.baseUrl}/jobs`)
  await expect(page).toHaveURL(/\/activity/)
})
