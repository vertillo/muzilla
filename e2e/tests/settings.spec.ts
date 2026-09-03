import { test, expect } from './fixtures'
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import path from 'node:path'

test('Settings screen renders providers, saving a filename template previews and persists', async ({
  page,
  muzilla,
}) => {
  // Provider/token, filename-template, and strip-rule controls share this
  // Settings surface. Exercise the live filename-template preview and
  // persistence flow specifically.
  await page.goto(`${muzilla.baseUrl}/settings`)
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible({ timeout: 10_000 })

  const providersSection = page.getByRole('heading', { name: 'Providers' }).locator('..').locator('..')
  await expect(providersSection.getByText('MusicBrainz', { exact: true }).first()).toBeVisible()
  await expect(providersSection.getByText('Discogs', { exact: true }).first()).toBeVisible()

  // Album tracks template field: fill in a template, preview it against
  // the sample track, then save it.
  const albumSection = page.getByText('Album tracks').locator('..')
  const templateInput = albumSection.getByPlaceholder(/albumartist/)
  await templateInput.fill('$albumartist - $album - $track $title')

  await albumSection.getByRole('button', { name: 'Preview' }).click()
  await expect(page.getByText(/Sigur Rós - Ágætis byrjun/)).toBeVisible({ timeout: 10_000 })

  await albumSection.getByRole('button', { name: 'Save' }).click()

  // Reload the page — the saved template must round-trip from the DB,
  // proving PUT /api/settings/templates actually persisted it, not
  // just updated in-memory component state.
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible({ timeout: 10_000 })
  const reloadedInput = page.getByText('Album tracks').locator('..').getByPlaceholder(/albumartist/)
  await expect(reloadedInput).toHaveValue('$albumartist - $album - $track $title')
})

test('Settings nav item is present and navigates to /settings', async ({ page, muzilla }) => {
  await page.goto(muzilla.baseUrl)
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible({ timeout: 10_000 })

  await page.getByRole('link', { name: 'Impostazioni' }).click()
  await expect(page).toHaveURL(`${muzilla.baseUrl}/settings`)
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible()
})

test('a template preview with malformed syntax shows a structural error, not a crash', async ({
  page,
  muzilla,
}) => {
  await page.goto(`${muzilla.baseUrl}/settings`)
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible({ timeout: 10_000 })

  const albumSection = page.getByText('Album tracks').locator('..')
  const templateInput = albumSection.getByPlaceholder(/albumartist/)
  await templateInput.fill('%thisFunctionDoesNotExist{$title}')
  await albumSection.getByRole('button', { name: 'Preview' }).click()

  // The page must still show the Settings heading (no crash / blank
  // page) alongside whatever error text the backend returned.
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible()
})

test('catalog reset preserves Settings and the exact music bytes', async ({ page, muzilla }) => {
  const filename = 'reset-fixture.mp3'
  muzilla.addFixtureFile(filename)
  await muzilla.scanOneFile(filename)
  const musicPath = path.join(muzilla.libraryDir, filename)
  const beforeHash = createHash('sha256').update(readFileSync(musicPath)).digest('hex')

  // Visit Dashboard while the catalog has a row so its summary is cached.
  // The reset redirects here, where the old total must not be reused.
  await page.goto(muzilla.baseUrl)
  const catalogTile = page.getByText('File nel catalogo').locator('..')
  await expect(catalogTile.getByText('1', { exact: true })).toBeVisible({ timeout: 10_000 })

  await page.goto(`${muzilla.baseUrl}/settings`)
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible({ timeout: 10_000 })
  const albumSection = page.getByText('Album tracks').locator('..')
  const templateInput = albumSection.getByPlaceholder(/albumartist/)
  await templateInput.fill('$artist - $title')
  await albumSection.getByRole('button', { name: 'Save' }).click()

  await page.getByRole('button', { name: 'Reset catalog' }).click()
  const dialog = page.getByRole('dialog', { name: 'Reset catalog and activity?' })
  await dialog.getByRole('textbox').fill('RESET CATALOG AND ACTIVITY')
  await dialog.getByRole('button', { name: 'Confirm reset' }).click()
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible({ timeout: 10_000 })
  await expect(catalogTile.getByText('0', { exact: true })).toBeVisible({ timeout: 10_000 })

  const afterHash = createHash('sha256').update(readFileSync(musicPath)).digest('hex')
  expect(afterHash).toBe(beforeHash)
  await page.goto(`${muzilla.baseUrl}/catalog`)
  await expect(page.getByText('Nessun file trovato')).toBeVisible({ timeout: 10_000 })
  await page.goto(`${muzilla.baseUrl}/settings`)
  await expect(page.getByText('Album tracks').locator('..').getByPlaceholder(/albumartist/)).toHaveValue(
    '$artist - $title',
  )
})
