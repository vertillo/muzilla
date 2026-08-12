import { test, expect } from './fixtures'

test('the jobs list renders a finished scan job', async ({ page, muzilla }) => {
  await muzilla.scanOneFile()

  await page.goto(`${muzilla.baseUrl}/activity`)
  // The worker also runs a retention_sweep job at startup (jobs/worker.
  // py::run_retention_loop's "on worker startup" guarantee), so the
  // list has at least two rows — scope to the scan row specifically.
  const scanRow = page.getByText('scan').locator('..').locator('..')
  await expect(scanRow).toBeVisible({ timeout: 10_000 })
  await expect(scanRow.getByText('succeeded')).toBeVisible()
})

test('expanding a job shows its log events, replayed via SSE', async ({ page, muzilla }) => {
  await muzilla.scanOneFile()

  await page.goto(`${muzilla.baseUrl}/activity`)
  const jobRow = page.getByText('scan').locator('..').locator('..')
  await expect(jobRow).toBeVisible({ timeout: 10_000 })

  await jobRow.click()
  // scan's handler (jobs/handlers/scan.py) calls progress.log(f"scanning
  // {root}") — GET .../events?after=0 replays every persisted
  // job_events row even for an already-finished job (useJobEvents.ts's
  // comment: "subscribing works for completed jobs too"), so this text
  // should appear even though the job finished before the panel opened.
  await expect(page.getByText(/scanning/)).toBeVisible({ timeout: 10_000 })
})

test('cancelling a job marks it cancelled', async ({ page, muzilla }) => {
  await muzilla.scanOneFile()

  // Keep the real scan handler busy long enough to exercise its file
  // checkpoint, then request cancellation through the Jobs UI.  A tiny
  // single-file fixture could complete before a human-visible cancel can
  // land and would not prove the running-state contract.
  for (let i = 0; i < 5000; i += 1) {
    muzilla.addFixtureFile(`cancel-${i}.mp3`)
  }
  await page.goto(`${muzilla.baseUrl}/activity`)
  const scanRes = await page.request.post(`${muzilla.baseUrl}/api/scan`, {
    data: { root: muzilla.libraryDir },
  })
  const jobId = (await scanRes.json()).job_id

  await page.reload()
  const jobRow = page.getByText(`#${jobId}`).locator('..').locator('..')
  await expect(jobRow).toBeVisible({ timeout: 10_000 })
  await expect(jobRow.getByText('running')).toBeVisible({ timeout: 10_000 })
  await jobRow.click()
  await page.getByRole('button', { name: 'Cancel' }).click()
  await expect(page.getByText('Cancelling…')).toBeVisible({ timeout: 10_000 })

  let finalJob: { state: string } = { state: 'pending' }
  const deadline = Date.now() + 10_000
  while (Date.now() < deadline) {
    finalJob = await (await page.request.get(`${muzilla.baseUrl}/api/jobs/${jobId}`)).json()
    if (['cancelled', 'succeeded', 'failed'].includes(finalJob.state)) break
    await new Promise((r) => setTimeout(r, 100))
  }
  expect(finalJob.state).toBe('cancelled')

  await expect(page.getByText(`#${jobId}`)).toBeVisible({ timeout: 10_000 })
  await expect(jobRow.getByText('cancelled')).toBeVisible()
})

test('enrichment buttons queue a job and show a confirmation toast', async ({ page, muzilla }) => {
  await muzilla.scanOneFile()

  await page.goto(`${muzilla.baseUrl}/activity`)
  await page.getByText('Azioni tecniche opzionali').click()
  await expect(page.getByRole('button', { name: 'Lyrics' })).toBeVisible({ timeout: 10_000 })

  // Lyrics enrichment only needs lrclib, which the fixture leaves
  // disabled but the handler itself degrades gracefully rather than
  // failing outright (docs/product-spec.md) — safe to queue without a mock.
  await page.getByRole('button', { name: 'Lyrics' }).click()
  await expect(page.getByText(/Lyrics queued \(job #\d+\)/)).toBeVisible({ timeout: 10_000 })
})
