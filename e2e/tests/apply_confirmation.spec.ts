import { test, expect } from './fixtures'

test('Apply requires confirmation; Enter opens the modal instead of applying directly', async ({
  page,
  muzilla,
}) => {
  await muzilla.scanOneFile()

  const tracksRes = await page.request.get(`${muzilla.baseUrl}/api/tracks`)
  const trackId = (await tracksRes.json()).items[0].id

  const patchRes = await page.request.patch(`${muzilla.baseUrl}/api/tracks/${trackId}`, {
    data: { fields: { title: 'confirmation-modal-test' } },
  })
  const detail = await patchRes.json()
  const changesetId = detail.id
  const changeId = detail.changes[0].id

  await page.request.patch(`${muzilla.baseUrl}/api/changesets/${changesetId}/changes`, {
    data: { decisions: [{ change_id: changeId, decision: 'accepted' }] },
  })

  await page.goto(`${muzilla.baseUrl}/changes/${changesetId}`)
  await expect(page.getByRole('heading', { name: detail.title })).toBeVisible({ timeout: 10_000 })

  // docs/PLAN.md §12e step 6.1: bare Enter must open the modal, never
  // apply directly — pressing it must NOT change the changeset's state.
  await page.keyboard.press('Enter')
  await expect(page.getByText('Apply this changeset?')).toBeVisible()
  const stillDraft = await (await page.request.get(`${muzilla.baseUrl}/api/changesets/${changesetId}`)).json()
  expect(stillDraft.state).toBe('draft')

  // Cancel closes the modal without applying.
  await page.getByRole('button', { name: 'Cancel' }).click()
  await expect(page.getByText('Apply this changeset?')).not.toBeVisible()

  // Clicking Apply also opens the modal rather than applying immediately.
  await page.getByRole('button', { name: 'Apply', exact: true }).click()
  await expect(page.getByText('Apply this changeset?')).toBeVisible()
  await expect(page.getByText('1 accepted change(s) will be written.')).toBeVisible()
  await expect(page.getByText(/1 file\(s\) will be written/)).toBeVisible()

  const stillDraftAfterOpen = await (
    await page.request.get(`${muzilla.baseUrl}/api/changesets/${changesetId}`)
  ).json()
  expect(stillDraftAfterOpen.state).toBe('draft')

  // Confirming inside the modal actually applies. Modal.tsx has no
  // role="dialog" (docs/PLAN.md §12e step 4.2 already characterized
  // that gap, left for Step 6.5 item 6) so scope by the modal's own
  // title heading's container rather than an accessible dialog role.
  const modalFooter = page.getByText('Apply this changeset?').locator('../..')
  await modalFooter.getByRole('button', { name: 'Apply', exact: true }).click()
  await expect(page.getByText(`Changeset #${changesetId} applied`)).toBeVisible({ timeout: 10_000 })

  const applied = await (await page.request.get(`${muzilla.baseUrl}/api/changesets/${changesetId}`)).json()
  expect(applied.state).toBe('applied')
})

test('Undo requires confirmation before staging the revert', async ({ page, muzilla }) => {
  await muzilla.scanOneFile()

  const tracksRes = await page.request.get(`${muzilla.baseUrl}/api/tracks`)
  const trackId = (await tracksRes.json()).items[0].id

  const patchRes = await page.request.patch(`${muzilla.baseUrl}/api/tracks/${trackId}`, {
    data: { fields: { title: 'undo-confirmation-test' } },
  })
  const detail = await patchRes.json()
  const changesetId = detail.id
  const changeId = detail.changes[0].id

  await page.request.patch(`${muzilla.baseUrl}/api/changesets/${changesetId}/changes`, {
    data: { decisions: [{ change_id: changeId, decision: 'accepted' }] },
  })
  const applyRes = await page.request.post(`${muzilla.baseUrl}/api/changesets/${changesetId}/apply`)
  const applyJobId = (await applyRes.json()).job_id

  const deadline = Date.now() + 10_000
  let applied = { state: 'applying' }
  while (Date.now() < deadline) {
    const job = await (await page.request.get(`${muzilla.baseUrl}/api/jobs/${applyJobId}`)).json()
    if (job.state === 'succeeded') {
      applied = await (await page.request.get(`${muzilla.baseUrl}/api/changesets/${changesetId}`)).json()
      break
    }
    await new Promise((r) => setTimeout(r, 200))
  }
  expect(applied.state).toBe('applied')

  await page.goto(`${muzilla.baseUrl}/changes/${changesetId}`)
  await expect(page.getByRole('button', { name: 'Undo' })).toBeVisible({ timeout: 10_000 })

  await page.getByRole('button', { name: 'Undo' }).click()
  await expect(page.getByText('Undo this changeset?')).toBeVisible()
  await expect(page.getByText(`reverts changeset #${changesetId}`)).toBeVisible()

  // Cancelling must not stage anything.
  await page.getByRole('button', { name: 'Cancel' }).click()
  await expect(page.getByText('Undo this changeset?')).not.toBeVisible()
})
