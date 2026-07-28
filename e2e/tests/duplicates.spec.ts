import { test, expect } from './fixtures'

test('empty state, and Scan for duplicates queues a job', async ({ page, muzilla }) => {
  await muzilla.scanOneFile()

  await page.goto(`${muzilla.baseUrl}/duplicates`)
  await expect(page.getByText('No duplicates found')).toBeVisible({ timeout: 10_000 })

  // Duplicate detection matches on AcoustID fingerprint (Duplicates.tsx's
  // own description text), which the fixture leaves disabled — so this
  // can only exercise "the button queues a real background job", not a
  // populated result. Producing an actual duplicate group would require
  // either enabling fingerprinting (heavy, real audio analysis) or
  // writing directly to the DB (breaks the "drive setup via the API"
  // pattern every other spec in this suite follows) — dismiss is
  // therefore untested end to end here.
  await page.getByRole('button', { name: 'Scan for duplicates' }).first().click()
  await expect(page.getByText(/Duplicate scan queued \(job #\d+\)/)).toBeVisible({ timeout: 10_000 })

  const jobsRes = await page.request.get(`${muzilla.baseUrl}/api/jobs`)
  const jobs = (await jobsRes.json()).items
  expect(jobs.some((j: { type: string }) => j.type === 'detect_duplicates')).toBe(true)
})

test('dismiss removes a duplicate group from the list', async ({ page, muzilla }) => {
  await muzilla.scanOneFile('a.mp3')
  await muzilla.scanOneFile('b.mp3')

  // No API creates a duplicate group directly (by design — detection is
  // the only producer, per api/routers/duplicates.py), so this drives
  // dismiss against whatever a real detect run finds. Both fixture
  // copies are identical bytes, so IF fingerprinting were enabled
  // they'd fingerprint-match — but acoustid is off in this fixture
  // (see the test above), so detect finds nothing to dismiss here
  // either. Skipped rather than asserted-around: there is no reachable
  // path to a real duplicate group without enabling fingerprinting.
  test.skip(true, 'no way to produce a real duplicate group without enabling AcoustID fingerprinting')
})
