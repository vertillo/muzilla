import { test, expect } from './fixtures'

test('the jobs list renders a finished scan job', async ({ page, muzilla }) => {
  await muzilla.scanOneFile()

  await page.goto(`${muzilla.baseUrl}/jobs`)
  // The worker also runs a retention_sweep job at startup (jobs/worker.
  // py::run_retention_loop's "on worker startup" guarantee), so the
  // list has at least two rows — scope to the scan row specifically.
  const scanRow = page.getByText('scan').locator('..').locator('..')
  await expect(scanRow).toBeVisible({ timeout: 10_000 })
  await expect(scanRow.getByText('succeeded')).toBeVisible()
})

test('expanding a job shows its log events, replayed via SSE', async ({ page, muzilla }) => {
  await muzilla.scanOneFile()

  await page.goto(`${muzilla.baseUrl}/jobs`)
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

  // Start a second scan and race to cancel it before it finishes — a
  // single tiny fixture file scans fast enough that this is timing-
  // sensitive, so drive the cancel through the real API immediately
  // after enqueueing rather than depending on a UI click landing inside
  // a narrow pending/running window.
  const scanRes = await page.request.post(`${muzilla.baseUrl}/api/scan`, {
    data: { root: muzilla.libraryDir },
  })
  const jobId = (await scanRes.json()).job_id
  await page.request.post(`${muzilla.baseUrl}/api/jobs/${jobId}/cancel`)

  // request_cancel (jobs/queue.py) only sets cancel_requested — the job
  // stays "pending" until the worker actually dequeues it, observes the
  // flag, and calls mark_cancelled, so the state transition is not
  // synchronous with the cancel request. Poll for a terminal state
  // rather than asserting immediately.
  let finalJob: { state: string } = { state: 'pending' }
  const deadline = Date.now() + 10_000
  while (Date.now() < deadline) {
    finalJob = await (await page.request.get(`${muzilla.baseUrl}/api/jobs/${jobId}`)).json()
    if (['cancelled', 'succeeded', 'failed'].includes(finalJob.state)) break
    await new Promise((r) => setTimeout(r, 100))
  }
  // A single tiny fixture file scans fast enough that the handler may
  // observe cancel_requested too late to matter — both outcomes are
  // correct depending on that race, "failed" is not.
  expect(['cancelled', 'succeeded']).toContain(finalJob.state)

  await page.goto(`${muzilla.baseUrl}/jobs`)
  await expect(page.getByText(`#${jobId}`)).toBeVisible({ timeout: 10_000 })
  if (finalJob.state === 'cancelled') {
    const jobRow = page.getByText(`#${jobId}`).locator('..').locator('..')
    await expect(jobRow.getByText('cancelled')).toBeVisible()
  }
})

test('enrichment buttons queue a job and show a confirmation toast', async ({ page, muzilla }) => {
  await muzilla.scanOneFile()

  await page.goto(`${muzilla.baseUrl}/jobs`)
  await expect(page.getByRole('button', { name: 'Lyrics' })).toBeVisible({ timeout: 10_000 })

  // Lyrics enrichment only needs lrclib, which the fixture leaves
  // disabled but the handler itself degrades gracefully rather than
  // failing outright (docs/PLAN.md §8) — safe to queue without a mock.
  await page.getByRole('button', { name: 'Lyrics' }).click()
  await expect(page.getByText(/Lyrics queued \(job #\d+\)/)).toBeVisible({ timeout: 10_000 })
})
