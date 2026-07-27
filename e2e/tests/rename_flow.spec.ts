import { test, expect } from './fixtures'
import fs from 'node:fs'
import path from 'node:path'

test('rename: Catalog -> select -> Rename -> Preview -> Stage -> Review & apply -> Undo', async ({
  page,
  muzilla,
}) => {
  await muzilla.scanOneFile()

  const tracksRes = await page.request.get(`${muzilla.baseUrl}/api/tracks`)
  const tracks = (await tracksRes.json()).items
  expect(tracks.length).toBe(1)
  const trackId = tracks[0].id
  const originalPath = tracks[0].path
  expect(fs.existsSync(originalPath)).toBe(true)

  await page.goto(`${muzilla.baseUrl}/rename?ids=${trackId}`)
  await expect(page.getByText(/Rename 1 track/)).toBeVisible({ timeout: 10_000 })

  await page.getByPlaceholder(/leave blank to use the configured/).fill('$artist - $title')
  await page.getByRole('button', { name: 'Preview' }).click()

  await expect(page.getByRole('button', { name: 'Stage as changeset' })).toBeEnabled({ timeout: 10_000 })
  await page.getByRole('button', { name: 'Stage as changeset' }).click()

  await expect(page.getByText(/Staged as changeset #/)).toBeVisible({ timeout: 10_000 })
  const reviewButton = page.getByRole('button', { name: 'Review & apply' })
  await expect(reviewButton).toBeVisible()
  await reviewButton.click()

  await expect(page).toHaveURL(/\/changes\/\d+/, { timeout: 10_000 })
  const match = page.url().match(/\/changes\/(\d+)/)
  if (!match) throw new Error(`could not parse changeset id from ${page.url()}`)
  const changesetId = Number(match[1])

  const csRes = await page.request.get(`${muzilla.baseUrl}/api/changesets/${changesetId}`)
  const cs = await csRes.json()
  expect(cs.source).toBe('rename')
  expect(cs.changes[0].field).toBe('path')
  expect(cs.changes[0].op).toBe('move')

  const decisions = cs.changes.map((c: { id: number }) => ({ change_id: c.id, decision: 'accepted' }))
  await page.request.patch(`${muzilla.baseUrl}/api/changesets/${changesetId}/changes`, {
    data: { decisions },
  })

  const applyRes = await page.request.post(`${muzilla.baseUrl}/api/changesets/${changesetId}/apply`)
  expect(applyRes.status()).toBe(202)
  const applyResult = await pollUntilChangesetState(page, muzilla.baseUrl, changesetId, [
    'applied',
    'failed',
    'partially_applied',
  ])
  expect(applyResult.state).toBe('applied')

  const trackAfterApply = await (await page.request.get(`${muzilla.baseUrl}/api/tracks/${trackId}`)).json()
  const newPath = trackAfterApply.path
  expect(newPath).not.toBe(originalPath)
  expect(fs.existsSync(newPath)).toBe(true)
  expect(fs.existsSync(originalPath)).toBe(false)
  expect(path.basename(newPath)).toMatch(/\.mp3$/)

  // undo: confirm the file moves back, proving op="move" composes
  // with the existing inverse-changeset machinery with no special
  // casing there (docs/PLAN.md's own stated verification goal for
  // Phase 5's rename work).
  const undoRes = await page.request.post(`${muzilla.baseUrl}/api/changesets/${changesetId}/undo`)
  expect(undoRes.status()).toBe(202)
  const undoJob = await pollJob(page, muzilla.baseUrl, (await undoRes.json()).job_id)
  expect(undoJob.state).toBe('succeeded')
  const undoChangesetId = undoJob.result.undo_change_set_id

  const undoApplyRes = await page.request.post(`${muzilla.baseUrl}/api/changesets/${undoChangesetId}/apply`)
  expect(undoApplyRes.status()).toBe(202)
  const undoApplied = await pollUntilChangesetState(page, muzilla.baseUrl, undoChangesetId, [
    'applied',
    'failed',
    'partially_applied',
  ])
  expect(undoApplied.state).toBe('applied')

  expect(fs.existsSync(originalPath)).toBe(true)
  expect(fs.existsSync(newPath)).toBe(false)
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
