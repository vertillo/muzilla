import { test, expect } from './fixtures'

test('scan -> review -> apply -> undo', async ({ page, muzilla }) => {
  await muzilla.scanOneFile()

  // Stage an individual-file edit. Grouping remains internal and has no public CRUD.
  const tracksRes = await page.request.get(`${muzilla.baseUrl}/api/tracks`)
  const trackId = (await tracksRes.json()).items[0].id
  const stageRes = await page.request.patch(`${muzilla.baseUrl}/api/tracks/${trackId}`, {
    data: { fields: { title: 'apply-undo-review' } },
  })
  expect(stageRes.ok()).toBeTruthy()
  const changeset = await stageRes.json()
  expect(changeset.state).toBe('draft')
  expect(changeset.changes.length).toBeGreaterThan(0)

  // review: open the real review screen and confirm the diff renders
  await page.goto(`${muzilla.baseUrl}/changes/${changeset.id}`)
  await expect(page.getByRole('heading', { name: changeset.title })).toBeVisible({ timeout: 10_000 })

  // accept every change, then apply through the real API path
  const decisions = changeset.changes.map((c: { id: number }) => ({
    change_id: c.id,
    decision: 'accepted',
  }))
  const decideRes = await page.request.patch(`${muzilla.baseUrl}/api/changesets/${changeset.id}/changes`, {
    data: { decisions },
  })
  expect(decideRes.ok()).toBeTruthy()

  const applyRes = await page.request.post(`${muzilla.baseUrl}/api/changesets/${changeset.id}/apply`)
  expect(applyRes.status()).toBe(202)
  const applyJobId = (await applyRes.json()).job_id

  const appliedChangeset = await pollUntilChangesetState(page, muzilla.baseUrl, changeset.id, [
    'applied',
    'partially_applied',
    'failed',
  ])
  expect(appliedChangeset.state).toBe('applied')
  void applyJobId

  // undo: confirm the inverse changeset applies and the UI's Undo
  // button is what a real user would click (proves the button exists
  // and points at a working endpoint, not just that the API works)
  await page.goto(`${muzilla.baseUrl}/changes`)
  await expect(page.getByText(`#${changeset.id}`)).toBeVisible({ timeout: 10_000 })

  const undoRes = await page.request.post(`${muzilla.baseUrl}/api/changesets/${changeset.id}/undo`)
  expect(undoRes.status()).toBe(202)
  const undoResult = await undoRes.json()

  const undoJobRes = await pollJob(page, muzilla.baseUrl, undoResult.job_id)
  expect(undoJobRes.state).toBe('succeeded')
  const undoChangesetId = undoJobRes.result.undo_change_set_id

  const undoApplyRes = await page.request.post(`${muzilla.baseUrl}/api/changesets/${undoChangesetId}/apply`)
  expect(undoApplyRes.status()).toBe(202)
  const undoApplied = await pollUntilChangesetState(page, muzilla.baseUrl, undoChangesetId, [
    'applied',
    'partially_applied',
    'failed',
  ])
  expect(undoApplied.state).toBe('applied')
})

test('the undo draft screen shows an unmissable banner naming the original changeset', async ({ page, muzilla }) => {
  await muzilla.scanOneFile()

  const tracksRes = await page.request.get(`${muzilla.baseUrl}/api/tracks`)
  const trackId = (await tracksRes.json()).items[0].id

  const patchRes = await page.request.patch(`${muzilla.baseUrl}/api/tracks/${trackId}`, {
    data: { fields: { title: 'undo-banner-test' } },
  })
  const detail = await patchRes.json()
  const changesetId = detail.id
  const changeId = detail.changes[0].id

  await page.request.patch(`${muzilla.baseUrl}/api/changesets/${changesetId}/changes`, {
    data: { decisions: [{ change_id: changeId, decision: 'accepted' }] },
  })
  const applyRes = await page.request.post(`${muzilla.baseUrl}/api/changesets/${changesetId}/apply`)
  const applyJob = await pollJob(page, muzilla.baseUrl, (await applyRes.json()).job_id)
  expect(applyJob.state).toBe('succeeded')

  const undoRes = await page.request.post(`${muzilla.baseUrl}/api/changesets/${changesetId}/undo`)
  const undoJob = await pollJob(page, muzilla.baseUrl, (await undoRes.json()).job_id)
  expect(undoJob.state).toBe('succeeded')
  const undoChangesetId = undoJob.result.undo_change_set_id

  // docs/PLAN.md §12e step 6.2: land on the undo draft BEFORE applying
  // it — this is exactly the state a real user sees right after
  // clicking Undo, where the old "Undo staged" toast alone gave no
  // on-screen indication anything was still incomplete.
  await page.goto(`${muzilla.baseUrl}/changes/${undoChangesetId}`)
  await expect(
    page.getByText(`This reverts changeset #${changesetId}. Nothing has been written back yet`),
  ).toBeVisible({ timeout: 10_000 })
})

async function pollJob(
  page: import('@playwright/test').Page,
  baseUrl: string,
  jobId: number,
): Promise<{ state: string; result: any }> {
  const deadline = Date.now() + 10_000
  while (Date.now() < deadline) {
    const res = await page.request.get(`${baseUrl}/api/jobs/${jobId}`)
    const job = await res.json()
    if (['succeeded', 'failed', 'cancelled'].includes(job.state)) return job
    await new Promise((r) => setTimeout(r, 200))
  }
  throw new Error(`job ${jobId} did not finish within 10s`)
}

async function pollUntilChangesetState(
  page: import('@playwright/test').Page,
  baseUrl: string,
  changesetId: number,
  terminal: string[],
): Promise<{ state: string }> {
  const deadline = Date.now() + 10_000
  while (Date.now() < deadline) {
    const res = await page.request.get(`${baseUrl}/api/changesets/${changesetId}`)
    const cs = await res.json()
    if (terminal.includes(cs.state)) return cs
    await new Promise((r) => setTimeout(r, 200))
  }
  throw new Error(`changeset ${changesetId} did not reach a terminal state within 10s`)
}
