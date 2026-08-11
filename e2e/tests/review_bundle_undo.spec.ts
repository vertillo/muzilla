import fs from 'node:fs'
import { test, expect } from './fixtures'

test('import -> ReviewBundle -> apply -> restart -> persistent undo restores file and catalog', async ({ page, muzilla }) => {
  muzilla.addMatchingFixtureFile()
  const started = await page.request.post(`${muzilla.baseUrl}/api/imports`, {
    data: { library_root: muzilla.libraryDir },
  })
  expect(started.status()).toBe(202)
  const importSession = await pollImport(page, muzilla.baseUrl, (await started.json()).id)
  expect(importSession.state).toMatch(/reviewing|completed/)
  expect(importSession.review_bundle_ids).toHaveLength(1)
  const reviewId = importSession.review_bundle_ids[0]
  const review = await pollReviewReady(page, muzilla.baseUrl, reviewId)
  const originalPath = review.source_items[0].path as string
  expect(review.current_revision.candidate_snapshot.title).toBe('E2E Track')
  expect(review.current_revision.confidence).toBeGreaterThanOrEqual(0.85)
  expect(review.current_revision.operations.length).toBeGreaterThan(0)

  const accepted = await page.request.patch(`${muzilla.baseUrl}/api/reviews/${reviewId}/operations`, {
    data: {
      revision_id: review.current_revision.id,
      decisions: review.current_revision.operations.map((operation: { id: number }) => ({
        operation_id: operation.id,
        decision: 'accepted',
      })),
    },
  })
  expect(accepted.ok()).toBeTruthy()

  await page.goto(`${muzilla.baseUrl}/reviews/${reviewId}?returnTo=%2Freviews`)
  const applyButton = page.getByRole('button', { name: /Applica \d+ modifiche/ })
  await expect(applyButton).toBeEnabled({ timeout: 10_000 })
  await applyButton.click()
  await page.getByRole('dialog').getByRole('button', { name: /Applica \d+ modifiche/ }).click()
  const applied = await pollReviewState(page, muzilla.baseUrl, reviewId, ['applied'])
  expect(applied.apply_runs.at(-1).result.files[0].state).toBe('applied')

  const trackId = applied.source_items[0].source_id
  const afterApply = await (await page.request.get(`${muzilla.baseUrl}/api/tracks/${trackId}`)).json()
  expect(afterApply.year).toBe(2026)
  expect(afterApply.path).not.toBe(originalPath)
  expect(fs.existsSync(afterApply.path)).toBeTruthy()

  await muzilla.restartApp()
  await page.goto(`${muzilla.baseUrl}/reviews/${reviewId}?returnTo=%2Freviews`)
  await page.getByRole('button', { name: 'Ripristina applicazione' }).click()
  const undoResponsePromise = page.waitForResponse(
    (response) => response.url().endsWith(`/api/reviews/${reviewId}/undo`),
  )
  await page.getByRole('dialog').getByRole('button', { name: 'Ripristina file' }).click()
  const undoResponse = await undoResponsePromise
  expect(undoResponse.status(), await undoResponse.text()).toBe(202)
  const undone = await pollUndoState(page, muzilla.baseUrl, reviewId)
  expect(undone.state).toBe('undone')
  expect(undone.result.files[0].state).toBe('undone')

  const restored = await (await page.request.get(`${muzilla.baseUrl}/api/tracks/${trackId}`)).json()
  expect(restored.year).toBe(1999)
  expect(restored.path).toBe(originalPath)
  expect(fs.existsSync(originalPath)).toBeTruthy()
  expect(fs.existsSync(afterApply.path)).toBeFalsy()
})

async function pollImport(page: import('@playwright/test').Page, baseUrl: string, id: number): Promise<any> {
  const deadline = Date.now() + 20_000
  while (Date.now() < deadline) {
    const session = await (await page.request.get(`${baseUrl}/api/imports/${id}`)).json()
    if (['reviewing', 'completed', 'failed', 'cancelled'].includes(session.state)) return session
    await new Promise((resolve) => setTimeout(resolve, 200))
  }
  throw new Error(`import ${id} did not finish`)
}

async function pollReviewReady(page: import('@playwright/test').Page, baseUrl: string, id: number): Promise<any> {
  const deadline = Date.now() + 20_000
  while (Date.now() < deadline) {
    const review = await (await page.request.get(`${baseUrl}/api/reviews/${id}`)).json()
    const activeTasks = review.task_attempts.filter((task: { state: string }) => ['pending', 'running'].includes(task.state))
    if (activeTasks.length === 0 && ['ready', 'needs_attention'].includes(review.state)) return review
    await new Promise((resolve) => setTimeout(resolve, 200))
  }
  throw new Error(`review ${id} did not become ready`)
}

async function pollReviewState(page: import('@playwright/test').Page, baseUrl: string, id: number, states: string[]): Promise<any> {
  const deadline = Date.now() + 20_000
  while (Date.now() < deadline) {
    const review = await (await page.request.get(`${baseUrl}/api/reviews/${id}`)).json()
    if (states.includes(review.state)) return review
    if (review.state === 'failed') throw new Error(`review apply failed: ${review.error}`)
    await new Promise((resolve) => setTimeout(resolve, 200))
  }
  throw new Error(`review ${id} did not reach ${states.join(', ')}`)
}

async function pollUndoState(page: import('@playwright/test').Page, baseUrl: string, id: number): Promise<any> {
  const deadline = Date.now() + 20_000
  while (Date.now() < deadline) {
    const review = await (await page.request.get(`${baseUrl}/api/reviews/${id}`)).json()
    const undo = review.undo_runs.at(-1)
    if (undo && ['undone', 'partially_undone', 'failed'].includes(undo.state)) return undo
    await new Promise((resolve) => setTimeout(resolve, 200))
  }
  throw new Error(`review ${id} undo did not finish`)
}
