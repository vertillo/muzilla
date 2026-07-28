import { test, expect } from './fixtures'

test('the review screen left pane labels entities by track, not database id', async ({
  page,
  muzilla,
}) => {
  await muzilla.scanOneFile('a.mp3')
  await muzilla.scanOneFile('b.mp3')

  const tracksRes = await page.request.get(`${muzilla.baseUrl}/api/tracks`)
  const tracks = (await tracksRes.json()).items
  expect(tracks.length).toBe(2)
  const trackIds = tracks.map((t: { id: number }) => t.id)

  // Both fixture copies share identical tags (Sigur Rós / Ágætis
  // byrjun, see e2e/tests/fixtures.ts) and neither is grouped (no
  // cascade run), so both are singletons — the label format under
  // test is "artist - title", not "N. title" (that's album mode,
  // covered by the backend unit tests in
  // tests/services/test_changesets.py).
  const bulkEditRes = await page.request.post(`${muzilla.baseUrl}/api/tracks/bulk-edit`, {
    data: { track_ids: trackIds, fields: [{ field: 'year', new_value: 2001 }] },
  })
  expect(bulkEditRes.ok()).toBeTruthy()
  const changeset = await bulkEditRes.json()

  await page.goto(`${muzilla.baseUrl}/changes/${changeset.id}`)
  await expect(page.getByRole('heading', { name: changeset.title })).toBeVisible({ timeout: 10_000 })

  // The left pane only renders when there's more than one entity
  // (ChangeSetReview.tsx: "collapses implicitly ... singleton mode").
  const expectedLabel = 'Sigur Rós – Ágætis byrjun'
  const labels = page.getByText(expectedLabel, { exact: true })
  await expect(labels).toHaveCount(2)

  // The old behavior rendered a bare "#<id>" — confirm neither track id
  // appears as a raw label anywhere in the entity list.
  for (const id of trackIds) {
    await expect(page.getByText(`#${id}`, { exact: true })).not.toBeVisible()
  }
})
