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

test('pin stages a grouping_correction changeset, but the list is not pinned until it is applied', async ({
  page,
  muzilla,
}) => {
  await muzilla.scanOneFile()

  const cascadeRes = await page.request.post(`${muzilla.baseUrl}/api/groups/cascade`)
  expect(cascadeRes.ok()).toBeTruthy()

  await page.goto(`${muzilla.baseUrl}/groups`)
  const pinButton = page.getByRole('button', { name: 'Pin', exact: true })
  await expect(pinButton).toBeVisible({ timeout: 10_000 })

  // services/grouping.py::pin_group only ever stages a changeset (like
  // merge/split/reassign/force-singleton — none of the five
  // grouping_correction call sites auto-apply); nothing in Groups.tsx's
  // onClick applies it either. So clicking Pin does not flip
  // Group.is_pinned or update this list on its own — characterizing
  // that gap here, not the "pinned badge appears" behavior a first
  // read of the button's label would suggest. Phase 7 suggestion #6
  // (docs/PHASE8_BRIEF.md) already flags the grouping workspace as
  // missing actions; this is the same gap for the one action that does
  // exist.
  await pinButton.click()
  const groupsAfter = await (await page.request.get(`${muzilla.baseUrl}/api/groups`)).json()
  expect(groupsAfter.items[0].is_pinned).toBe(false)
  await expect(pinButton).toBeVisible()
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
  await expect(page.getByText(/Merging group #\d+/)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Merge into' }).first()).toBeVisible()

  await page.getByText('cancel').click()
  await expect(page.getByText(/Merging group #\d+/)).not.toBeVisible()
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

  // merge stages a ChangeSet (docs/PLAN.md §4: nothing touches disk or
  // the group table until applied) rather than mutating groups
  // synchronously — assert the banner clears, proving the mutation
  // round-tripped, rather than asserting on the group list itself.
  await expect(page.getByText(/Merging group #\d+/)).not.toBeVisible({ timeout: 10_000 })
})
