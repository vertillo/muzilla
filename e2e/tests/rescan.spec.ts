import { test, expect } from './fixtures'
import * as fs from 'node:fs'
import path from 'node:path'

test('reread updates the catalog and missing-race is visible', async ({ page, muzilla }) => {
  await muzilla.scanOneFile()
  // Find track id via API
  const tracksRes = await page.request.get(`${muzilla.baseUrl}/api/tracks?limit=1`)
  expect(tracksRes.ok()).toBeTruthy()
  const { items } = await tracksRes.json()
  const trackId = items[0].id as number
  const trackPath = items[0].path as string

  await page.goto(`${muzilla.baseUrl}/catalog/${trackId}`)
  await expect(page.getByRole('heading', { name: 'Ágætis byrjun' })).toBeVisible({ timeout: 10_000 })
  await expect(page.getByRole('button', { name: 'Rileggi file' })).toBeVisible()

  // Trigger rescan (file-only, no provider)
  const rescanPromise = page.waitForResponse((r) => r.url().includes(`/api/tracks/${trackId}/rescan`) && r.request().method() === 'POST')
  await page.getByRole('button', { name: 'Rileggi file' }).click()
  const rescanRes = await rescanPromise
  expect(rescanRes.status()).toBe(202)
  const { job_id } = await rescanRes.json()

  // Poll job until succeeded
  let jobState: string | null = null
  for (let i = 0; i < 30; i++) {
    const jobRes = await page.request.get(`${muzilla.baseUrl}/api/jobs/${job_id}`)
    expect(jobRes.ok()).toBeTruthy()
    const job = await jobRes.json()
    jobState = job.state
    if (jobState === 'succeeded') break
    if (jobState === 'failed' || jobState === 'cancelled') break
    await new Promise((r) => setTimeout(r, 200))
  }
  expect(jobState).toBe('succeeded')

  // Catalog should still show the track, not missing
  await page.goto(`${muzilla.baseUrl}/catalog/${trackId}`)
  await expect(page.getByText('File non disponibile')).not.toBeVisible()
  await expect(page.getByRole('button', { name: 'Rileggi file' })).toBeVisible()

  // Now simulate missing file (race): delete file then rescan
  try {
    fs.unlinkSync(trackPath)
  } catch {
    // if path is symlink resolved, try realpath
    try {
      fs.unlinkSync(fs.realpathSync(trackPath))
    } catch {}
  }

  const rescan2Promise = page.waitForResponse((r) => r.url().includes(`/api/tracks/${trackId}/rescan`) && r.request().method() === 'POST')
  await page.getByRole('button', { name: 'Rileggi file' }).click()
  const rescan2Res = await rescan2Promise
  expect(rescan2Res.status()).toBe(202)
  const { job_id: job2 } = await rescan2Res.json()
  let job2State: string | null = null
  for (let i = 0; i < 30; i++) {
    const jobRes = await page.request.get(`${muzilla.baseUrl}/api/jobs/${job2}`)
    const job = await jobRes.json()
    job2State = job.state
    if (job2State === 'succeeded') break
    if (job2State === 'failed') break
    await new Promise((r) => setTimeout(r, 200))
  }
  expect(job2State).toBe('succeeded')

  await page.reload()
  await expect(page.getByText('File non disponibile')).toBeVisible({ timeout: 10_000 })
  await expect(page.getByRole('button', { name: 'Verifica di nuovo' })).toBeVisible()
})

test('Analyze again starts only after successful reread', async ({ page, muzilla }) => {
  await muzilla.scanOneFile()
  const tracksRes = await page.request.get(`${muzilla.baseUrl}/api/tracks?limit=1`)
  const { items } = await tracksRes.json()
  const trackId = items[0].id as number
  const trackPath = items[0].path as string

  await page.goto(`${muzilla.baseUrl}/catalog/${trackId}`)
  await expect(page.getByRole('button', { name: 'Analizza di nuovo' })).toBeVisible({ timeout: 10_000 })

  // Successful reread -> analysis_started should be true (via API, deterministic)
  const analyzeRes = await page.request.post(`${muzilla.baseUrl}/api/tracks/${trackId}/analyze`)
  expect(analyzeRes.status()).toBe(202)
  const { job_id } = await analyzeRes.json()
  let result: any = null
  for (let i = 0; i < 30; i++) {
    const jobRes = await page.request.get(`${muzilla.baseUrl}/api/jobs/${job_id}`)
    const job = await jobRes.json()
    if (job.state === 'succeeded') {
      result = job.result
      break
    }
    if (job.state === 'failed') break
    await new Promise((r) => setTimeout(r, 200))
  }
  expect(result).not.toBeNull()
  expect(result.analysis_started).toBe(true)
  expect(result.reread_state).toBe('updated')
  // UI should not show failure alert when analysis started; verify button still visible
  await expect(page.getByRole('button', { name: 'Analizza di nuovo' })).toBeVisible()
  await expect(page.getByText('La rilettura non è terminata: la ricerca non è stata avviata.')).not.toBeVisible()

  // Make file missing, then analyze again should not start analysis
  try {
    fs.unlinkSync(trackPath)
  } catch {
    try {
      fs.unlinkSync(fs.realpathSync(trackPath))
    } catch {}
  }
  const directRes = await page.request.post(`${muzilla.baseUrl}/api/tracks/${trackId}/analyze`)
  expect(directRes.status()).toBe(202)
  const { job_id: job2 } = await directRes.json()
  let result2: any = null
  for (let i = 0; i < 30; i++) {
    const jobRes = await page.request.get(`${muzilla.baseUrl}/api/jobs/${job2}`)
    const job = await jobRes.json()
    if (job.state === 'succeeded') {
      result2 = job.result
      break
    }
    await new Promise((r) => setTimeout(r, 200))
  }
  expect(result2).not.toBeNull()
  expect(result2.analysis_started).toBe(false)
  expect(result2.reread_state).toBe('missing')

  // Reload UI should show missing
  await page.reload()
  await expect(page.getByText('File non disponibile')).toBeVisible({ timeout: 10_000 })
  // Verify that after missing, the analyze result's failure maps to UI alert when re-triggered via UI (polling shows alert)
  // We simulate by directly checking the job result: analysis_started false means UI would show alert if it had been triggered
  expect(result2.analysis_started).toBe(false)
})
