import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { test, expect } from './fixtures'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const FIXTURE_AUDIO = path.resolve(__dirname, '..', '..', 'tests', 'fixtures', 'audio', 'silence.mp3')

test('wizard -> session -> review -> open a staged changeset', async ({ page, muzilla }) => {
  // Unlike muzilla.scanOneFile() (which drives POST /api/scan directly),
  // the import wizard walks scan -> fingerprint -> group -> match as one
  // orchestrated job (jobs/handlers/import_session.py), so the fixture
  // file needs to already be on disk before the wizard starts it.
  fs.copyFileSync(FIXTURE_AUDIO, path.join(muzilla.libraryDir, 'silence.mp3'))

  await page.goto(`${muzilla.baseUrl}/import`)
  await expect(page.getByRole('heading', { name: 'Import a library' })).toBeVisible({ timeout: 10_000 })

  // docs/PLAN.md §12c step 2.7 constrains the import root to the
  // configured storage.library_root or a descendant — the fixture sets
  // that to muzilla.libraryDir, so typing it back is what a real user
  // pointing the wizard at their configured library would do.
  await page.getByPlaceholder('/music').fill(muzilla.libraryDir)
  await page.getByRole('button', { name: 'Start import' }).click()

  await expect(page).toHaveURL(/\/import\/\d+$/, { timeout: 10_000 })

  await expect(page.getByText('Scan', { exact: true })).toBeVisible({ timeout: 10_000 })
  await expect(page.getByText('Match', { exact: true })).toBeVisible()

  // Wait for the import to leave its running states — a matched
  // singleton auto-stages a changeset (docs/PLAN.md §10), so "reviewing"
  // or "completed" is the terminal state to wait for, not a fixed sleep.
  await expect(page.getByText(/^(reviewing|completed)$/)).toBeVisible({ timeout: 20_000 })

  const reviewButtons = page.getByRole('button', { name: 'Review' })
  await expect(reviewButtons.first()).toBeVisible({ timeout: 10_000 })
  await reviewButtons.first().click()

  await expect(page).toHaveURL(/\/changes\/\d+$/, { timeout: 10_000 })
})

test('starting an import outside the configured library root is rejected', async ({ page, muzilla }) => {
  await page.goto(`${muzilla.baseUrl}/import`)
  await expect(page.getByRole('heading', { name: 'Import a library' })).toBeVisible({ timeout: 10_000 })

  // docs/PLAN.md §12c step 2.7: scan/import roots are constrained
  // server-side to storage.library_root or a descendant — /etc is
  // never inside the fixture's scratch library dir.
  await page.getByPlaceholder('/music').fill('/etc')
  await page.getByRole('button', { name: 'Start import' }).click()

  await expect(page.getByText(/is not the configured library root/)).toBeVisible({ timeout: 10_000 })
  await expect(page).toHaveURL(`${muzilla.baseUrl}/import`)
})
