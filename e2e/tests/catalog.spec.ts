import { test, expect } from './fixtures'

test('search filters the track list', async ({ page, muzilla }) => {
  await muzilla.scanOneFile()

  await page.goto(`${muzilla.baseUrl}/catalog`)
  await expect(page.getByRole('link', { name: 'Ágætis byrjun' })).toBeVisible({ timeout: 10_000 })

  await page.getByPlaceholder('Search title, artist, album…').fill('nonexistent-search-term')
  await expect(page.getByText('No tracks found')).toBeVisible({ timeout: 10_000 })
  // Catalog.tsx's empty-state description branches on `total`, but
  // `total` is the *search-scoped* count from the API response (db/
  // repo/tracks.py's list_tracks), not the overall library count — so
  // a search with zero matches shows the "Run `muzilla scan`" message
  // (meant for a genuinely empty library) rather than "No tracks match
  // the current search and filters." This is the exact defect pattern
  // Step 5.4 targets; characterizing the current (wrong) behavior here
  // rather than the intended one, since fixing UI copy is out of scope
  // for Phase 4.
  await expect(page.getByText('Run `muzilla scan <path>` to index your library.')).toBeVisible()

  await page.getByPlaceholder('Search title, artist, album…').fill('Sigur')
  await expect(page.getByRole('link', { name: 'Ágætis byrjun' })).toBeVisible({ timeout: 10_000 })
})

test('the artist facet filters the track list', async ({ page, muzilla }) => {
  await muzilla.scanOneFile()

  await page.goto(`${muzilla.baseUrl}/catalog`)
  await expect(page.getByRole('link', { name: 'Ágætis byrjun' })).toBeVisible({ timeout: 10_000 })

  // Sidebar facet <select>s render before the toolbar's sort <select>
  // in DOM order (Catalog.tsx: aside comes before main) — Artist is
  // the first combobox on the page.
  const artistFacet = page.getByRole('combobox').first()
  await expect(artistFacet).toHaveValue('')

  await artistFacet.selectOption('Sigur Rós')
  await expect(page.getByRole('link', { name: 'Ágætis byrjun' })).toBeVisible()
  await expect(page.getByText('1 of 1 tracks')).toBeVisible()

  // picking a genre that doesn't exist for this artist would empty the
  // list — instead prove the facet round-trips back to "All"
  await artistFacet.selectOption('')
  await expect(page.getByText('1 of 1 tracks')).toBeVisible()
})

test('multi-select enables bulk edit and rename, and Clear selection empties it', async ({ page, muzilla }) => {
  await muzilla.scanOneFile('a.mp3')
  await muzilla.scanOneFile('b.mp3')

  await page.goto(`${muzilla.baseUrl}/catalog`)
  await expect(page.getByText('2 of 2 tracks')).toBeVisible({ timeout: 10_000 })

  expect(await page.getByText('selected').count()).toBe(0)

  // Both fixture copies share identical tags (same source file), so
  // rows are only distinguishable by position, not visible text —
  // select both rows' checkboxes by row index rather than by title.
  const rowCheckboxes = page.locator('div[style*="width: 20px"] label')
  await rowCheckboxes.nth(0).click()
  await expect(page.getByText('1 selected')).toBeVisible()
  await rowCheckboxes.nth(1).click()
  await expect(page.getByText('2 selected')).toBeVisible()

  const bulkEditButton = page.getByRole('button', { name: 'Bulk edit' })
  const renameButton = page.getByRole('button', { name: 'Rename' })
  await expect(bulkEditButton).toBeVisible()
  await expect(renameButton).toBeVisible()

  await page.getByRole('button', { name: 'Clear selection' }).click()
  await expect(page.getByText('selected')).not.toBeVisible()

  await rowCheckboxes.nth(0).click()
  await bulkEditButton.click()
  await expect(page).toHaveURL(/\/edit\?ids=\d+/)
})

test('multi-select navigates to the rename flow with the selected ids', async ({ page, muzilla }) => {
  await muzilla.scanOneFile()

  await page.goto(`${muzilla.baseUrl}/catalog`)
  await expect(page.getByText('1 of 1 tracks')).toBeVisible({ timeout: 10_000 })

  const rowCheckboxes = page.locator('div[style*="width: 20px"] label')
  await rowCheckboxes.first().click()
  await page.getByRole('button', { name: 'Rename' }).click()

  await expect(page).toHaveURL(/\/rename\?ids=\d+/)
})
