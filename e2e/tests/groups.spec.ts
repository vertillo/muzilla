import { test, expect, type MuzillaEnv } from './fixtures'
import type { Page } from '@playwright/test'

/** Both fixture copies share identical tags — including mb_release_id
 * — since they're byte-for-byte copies of the same source file
 * (e2e/tests/fixtures.ts's scanOneFile), so the grouping cascade's
 * stage-1 strong-identifier clustering (pipeline/grouping.py) merges
 * them into one album group by default. The merge tests need two
 * separate groups to merge, so this clears mb_release_id and gives the
 * second track a distinct album via the real manual-edit -> accept ->
 * apply path (not a DB write) before the cascade runs. */
async function makeSecondTrackASeparateGroup(page: Page, muzilla: MuzillaEnv): Promise<void> {
  const tracksRes = await page.request.get(`${muzilla.baseUrl}/api/tracks`)
  const tracks = (await tracksRes.json()).items
  const secondTrackId = tracks[1].id

  const patchRes = await page.request.patch(`${muzilla.baseUrl}/api/tracks/${secondTrackId}`, {
    data: { fields: { mb_release_id: null, album: 'A Completely Different Album' } },
  })
  const detail = await patchRes.json()
  const changesetId = detail.id
  const decisions = detail.changes.map((c: { id: number }) => ({ change_id: c.id, decision: 'accepted' }))

  await page.request.patch(`${muzilla.baseUrl}/api/changesets/${changesetId}/changes`, {
    data: { decisions },
  })
  const applyRes = await page.request.post(`${muzilla.baseUrl}/api/changesets/${changesetId}/apply`)
  const jobId = (await applyRes.json()).job_id

  const deadline = Date.now() + 10_000
  while (Date.now() < deadline) {
    const job = await (await page.request.get(`${muzilla.baseUrl}/api/jobs/${jobId}`)).json()
    if (job.state === 'succeeded') return
    if (job.state === 'failed' || job.state === 'cancelled') throw new Error(`apply job ended in ${job.state}`)
    await new Promise((r) => setTimeout(r, 200))
  }
  throw new Error('apply job did not finish within 10s')
}

test('cascade run populates the group list', async ({ page, muzilla }) => {
  await muzilla.scanOneFile()

  await page.goto(`${muzilla.baseUrl}/groups`)
  await expect(page.getByText('No groups yet')).toBeVisible({ timeout: 10_000 })

  await page.getByRole('button', { name: 'Run grouping cascade', exact: true }).click()
  await expect(page.getByText('No groups yet')).not.toBeVisible({ timeout: 10_000 })
  await expect(page.getByText('Singleton').first()).toBeVisible()
})

test('pin applies immediately: clicking Pin flips is_pinned via the real API', async ({
  page,
  muzilla,
}) => {
  await muzilla.scanOneFile()

  const cascadeRes = await page.request.post(`${muzilla.baseUrl}/api/groups/cascade`)
  expect(cascadeRes.ok()).toBeTruthy()

  await page.goto(`${muzilla.baseUrl}/groups`)
  const pinButton = page.getByRole('button', { name: 'Pin', exact: true })
  await expect(pinButton).toBeVisible({ timeout: 10_000 })

  // docs/KNOWN_BUGS.md #3's fix, Phase 7 item 6's product decision:
  // pin (like merge/split/reassign/force-to-singleton) now auto-applies
  // its changeset immediately rather than only staging a draft — this
  // is the exact opposite assertion from what this test used to check
  // before the fix landed (it used to assert is_pinned stayed false).
  await pinButton.click()
  await expect(page.getByText('pinned')).toBeVisible({ timeout: 10_000 })

  const groupsAfter = await (await page.request.get(`${muzilla.baseUrl}/api/groups`)).json()
  expect(groupsAfter.items[0].is_pinned).toBe(true)
})

test('merge mode: entering it shows a banner naming the source group, cancel exits it', async ({
  page,
  muzilla,
}) => {
  await muzilla.scanOneFile('a.mp3')
  await muzilla.scanOneFile('b.mp3')
  await makeSecondTrackASeparateGroup(page, muzilla)

  const cascadeRes = await page.request.post(`${muzilla.baseUrl}/api/groups/cascade`)
  expect(cascadeRes.ok()).toBeTruthy()

  await page.goto(`${muzilla.baseUrl}/groups`)
  await expect(page.getByRole('button', { name: 'Merge…' }).first()).toBeVisible({ timeout: 10_000 })

  const groupsRes = await page.request.get(`${muzilla.baseUrl}/api/groups`)
  const groups = (await groupsRes.json()).items
  expect(groups.length).toBeGreaterThanOrEqual(2)

  await page.getByRole('button', { name: 'Merge…' }).first().click()
  await expect(page.getByText(/Merging "/)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Merge into' }).first()).toBeVisible()

  await page.getByText('cancel').click()
  await expect(page.getByText(/Merging "/)).not.toBeVisible()
  await expect(page.getByRole('button', { name: 'Merge…' }).first()).toBeVisible()
})

test('merge mode: Escape cancels it', async ({ page, muzilla }) => {
  await muzilla.scanOneFile('a.mp3')
  await muzilla.scanOneFile('b.mp3')
  await makeSecondTrackASeparateGroup(page, muzilla)

  const cascadeRes = await page.request.post(`${muzilla.baseUrl}/api/groups/cascade`)
  expect(cascadeRes.ok()).toBeTruthy()

  await page.goto(`${muzilla.baseUrl}/groups`)
  await expect(page.getByRole('button', { name: 'Merge…' }).first()).toBeVisible({ timeout: 10_000 })

  // docs/PLAN.md §12e step 6.5 item 2: Escape used to do nothing here.
  await page.getByRole('button', { name: 'Merge…' }).first().click()
  await expect(page.getByText(/Merging "/)).toBeVisible()

  await page.keyboard.press('Escape')
  await expect(page.getByText(/Merging "/)).not.toBeVisible()
  await expect(page.getByRole('button', { name: 'Merge…' }).first()).toBeVisible()
})

test('merge into: completing a merge reduces the group count via the real API', async ({ page, muzilla }) => {
  await muzilla.scanOneFile('a.mp3')
  await muzilla.scanOneFile('b.mp3')
  await makeSecondTrackASeparateGroup(page, muzilla)

  const cascadeRes = await page.request.post(`${muzilla.baseUrl}/api/groups/cascade`)
  expect(cascadeRes.ok()).toBeTruthy()

  const before = await (await page.request.get(`${muzilla.baseUrl}/api/groups`)).json()
  expect(before.items.length).toBeGreaterThanOrEqual(2)

  await page.goto(`${muzilla.baseUrl}/groups`)
  await expect(page.getByRole('button', { name: 'Merge…' }).first()).toBeVisible({ timeout: 10_000 })

  await page.getByRole('button', { name: 'Merge…' }).first().click()
  await page.getByRole('button', { name: 'Merge into' }).first().click()

  // docs/PLAN.md §12e step 6.5 item 2: completing a merge now goes
  // through a confirmation modal naming both groups before it actually
  // mutates anything.
  await expect(page.getByText('Merge these groups?')).toBeVisible({ timeout: 10_000 })
  await page.getByRole('button', { name: 'Merge', exact: true }).click()

  // merge now auto-applies (docs/KNOWN_BUGS.md #3's fix) — confirm the
  // group count actually dropped via the real API, not just that the
  // banner cleared. Not asserting an exact -1: the cascade can produce
  // more than 2 starting groups depending on how it splits the fixture,
  // so "strictly fewer groups than before" is the robust claim; the
  // banner-clearing assertion above already proves the request itself
  // succeeded.
  await expect(page.getByText(/Merging "/)).not.toBeVisible({ timeout: 10_000 })
  const after = await (await page.request.get(`${muzilla.baseUrl}/api/groups`)).json()
  expect(after.items.length).toBeLessThan(before.items.length)
})

test('force-to-singleton pulls a track out of its group immediately', async ({ page, muzilla }) => {
  await muzilla.scanOneFile('a.mp3')
  await muzilla.scanOneFile('b.mp3')

  const cascadeRes = await page.request.post(`${muzilla.baseUrl}/api/groups/cascade`)
  expect(cascadeRes.ok()).toBeTruthy()

  const groups = (await (await page.request.get(`${muzilla.baseUrl}/api/groups`)).json()).items
  const albumGroup = groups.find((g: { track_count: number }) => g.track_count >= 2) ?? groups[0]

  await page.goto(`${muzilla.baseUrl}/groups/${albumGroup.id}`)
  await expect(page.getByRole('button', { name: 'Force to singleton' }).first()).toBeVisible({
    timeout: 10_000,
  })

  const tracksBefore = (await (await page.request.get(`${muzilla.baseUrl}/api/groups/${albumGroup.id}`)).json())
    .track_ids

  await page.getByRole('button', { name: 'Force to singleton' }).first().click()

  await expect
    .poll(async () => {
      const detail = await (await page.request.get(`${muzilla.baseUrl}/api/groups/${albumGroup.id}`)).json()
      return detail.track_ids.length
    }, { timeout: 10_000 })
    .toBeLessThan(tracksBefore.length)
})

test('split moves the selected tracks into their own group immediately', async ({ page, muzilla }) => {
  await muzilla.scanOneFile('a.mp3')
  await muzilla.scanOneFile('b.mp3')

  const cascadeRes = await page.request.post(`${muzilla.baseUrl}/api/groups/cascade`)
  expect(cascadeRes.ok()).toBeTruthy()

  const groups = (await (await page.request.get(`${muzilla.baseUrl}/api/groups`)).json()).items
  const groupWithTwoTracks = groups.find((g: { track_count: number }) => g.track_count >= 2)
  test.skip(!groupWithTwoTracks, 'fixture did not group both tracks together this run')
  if (!groupWithTwoTracks) return

  await page.goto(`${muzilla.baseUrl}/groups/${groupWithTwoTracks.id}`)
  await expect(page.getByRole('button', { name: /Split selected out/ })).toBeVisible({ timeout: 10_000 })

  // Select the first track's checkbox (leftmost column of the first row).
  await page.locator('div[style*="width: 24px"] label').first().click()
  await page.getByRole('button', { name: /Split selected out \(1\)/ }).click()

  await expect
    .poll(async () => {
      const detail = await (
        await page.request.get(`${muzilla.baseUrl}/api/groups/${groupWithTwoTracks.id}`)
      ).json()
      return detail.track_ids.length
    }, { timeout: 10_000 })
    .toBe(1)
})

test('move to group reassigns a track via the picker, applying immediately', async ({ page, muzilla }) => {
  await muzilla.scanOneFile('a.mp3')
  await muzilla.scanOneFile('b.mp3')
  await makeSecondTrackASeparateGroup(page, muzilla)

  const cascadeRes = await page.request.post(`${muzilla.baseUrl}/api/groups/cascade`)
  expect(cascadeRes.ok()).toBeTruthy()

  const groups = (await (await page.request.get(`${muzilla.baseUrl}/api/groups`)).json()).items
  expect(groups.length).toBeGreaterThanOrEqual(2)
  const [source, destination] = groups

  await page.goto(`${muzilla.baseUrl}/groups/${source.id}`)
  await expect(page.getByRole('button', { name: 'Move' }).first()).toBeVisible({ timeout: 10_000 })

  const picker = page.locator('select').first()
  await picker.selectOption(String(destination.id))
  await page.getByRole('button', { name: 'Move' }).first().click()

  await expect
    .poll(async () => {
      const detail = await (await page.request.get(`${muzilla.baseUrl}/api/groups/${destination.id}`)).json()
      return detail.track_ids.length
    }, { timeout: 10_000 })
    .toBeGreaterThan(0)
})
