import { test, expect } from './fixtures'

test('search filters the track list', async ({ page, muzilla }) => {
  await muzilla.scanOneFile()

  await page.goto(`${muzilla.baseUrl}/catalog`)
  await expect(page.getByRole('link', { name: 'Ágætis byrjun' })).toBeVisible({ timeout: 10_000 })

  // No hyphen here deliberately — docs/KNOWN_BUGS.md #2: FTS5's MATCH
  // treats a hyphen as a NOT-prefix on the following token, so a term
  // like "nonexistent-search-term" 500s db/repo/tracks.py's list_tracks
  // instead of returning zero rows. That's a separate, real backend bug
  // recorded there; this test wants the genuine empty-result path.
  await page.getByPlaceholder('Search title, artist, album…').fill('zzznonexistent')
  await expect(page.getByText('No tracks found')).toBeVisible({ timeout: 10_000 })
  // Step 5.4 fixed Catalog.tsx's empty-state message to derive from
  // actual search/filter state (hasActiveSearchOrFilter) instead of the
  // search-scoped `total` (docs/KNOWN_BUGS.md #1, fixed) — with a
  // search term active, this must read "No tracks match…", never the
  // "Run muzilla scan" message meant for a genuinely empty library.
  await expect(page.getByText('No tracks match the current search and filters.')).toBeVisible()

  await page.getByPlaceholder('Search title, artist, album…').fill('Sigur')
  await expect(page.getByRole('link', { name: 'Ágætis byrjun' })).toBeVisible({ timeout: 10_000 })
})

test('a search term that crashes FTS5 shows a real error, not a misleading empty state', async ({
  page,
  muzilla,
}) => {
  await muzilla.scanOneFile()

  await page.goto(`${muzilla.baseUrl}/catalog`)
  await expect(page.getByRole('link', { name: 'Ágætis byrjun' })).toBeVisible({ timeout: 10_000 })

  // docs/KNOWN_BUGS.md #2: a hyphenated search term makes FTS5's MATCH
  // throw (the hyphen is a NOT-prefix in FTS5 query syntax), which
  // GET /api/tracks surfaces as a real 500 — this is the honest-error
  // path Step 5.4 added, not the bug's fix itself (the query string is
  // still unsanitized backend-side).
  await page.getByPlaceholder('Search title, artist, album…').fill('nonexistent-search-term')
  await expect(page.getByText("Couldn't load tracks")).toBeVisible({ timeout: 10_000 })
  await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible()
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

test('the genre facet dropdown and filter are computed server-side, not from loaded rows', async ({
  page,
  muzilla,
}) => {
  // docs/PHASE8_BRIEF.md Phase 7 suggestion #1: facet options and the
  // "N of M tracks" total used to be derived entirely from whatever
  // pages the client had already fetched. This exercises the server
  // round trip specifically (GET /api/tracks/facets and GET /api/tracks
  // with a genre= filter) rather than just re-testing the artist facet,
  // since genre is stored as a JSON array column and needed its own SQL
  // path (json_each), unlike the scalar artist/album/format columns.
  await muzilla.scanOneFile()

  await page.goto(`${muzilla.baseUrl}/catalog`)
  await expect(page.getByRole('link', { name: 'Ágætis byrjun' })).toBeVisible({ timeout: 10_000 })

  const genreFacet = page.getByRole('combobox').nth(2)
  await expect(genreFacet.locator('option', { hasText: 'Post-Rock' })).toHaveCount(1)

  await genreFacet.selectOption('Post-Rock')
  await expect(page.getByText('1 of 1 tracks')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Ágætis byrjun' })).toBeVisible()
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

test('selection survives navigating to bulk edit and back', async ({ page, muzilla }) => {
  // docs/PLAN.md §12e step 6.5 item 1: selecting tracks, opening bulk
  // edit, and returning used to clear the selection — it lived in
  // Catalog.tsx's own component state, which unmounts on navigation.
  await muzilla.scanOneFile('a.mp3')
  await muzilla.scanOneFile('b.mp3')

  await page.goto(`${muzilla.baseUrl}/catalog`)
  await expect(page.getByText('2 of 2 tracks')).toBeVisible({ timeout: 10_000 })

  const rowCheckboxes = page.locator('div[style*="width: 20px"] label')
  await rowCheckboxes.nth(0).click()
  await rowCheckboxes.nth(1).click()
  await expect(page.getByText('2 selected')).toBeVisible()

  await page.getByRole('button', { name: 'Bulk edit' }).click()
  await expect(page).toHaveURL(/\/edit\?ids=\d+,\d+/)

  await page.goBack()
  await expect(page.getByText('2 of 2 tracks')).toBeVisible({ timeout: 10_000 })
  await expect(page.getByText('2 selected')).toBeVisible()
})
