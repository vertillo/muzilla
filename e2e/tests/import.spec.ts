import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { test, expect, noLibraryTest } from './fixtures'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const FIXTURE_AUDIO = path.resolve(__dirname, '..', '..', 'tests', 'fixtures', 'audio', 'silence.mp3')

test('wizard shows the configured library root read-only and starts an import', async ({
  page,
  muzilla,
}) => {
  // Unlike muzilla.scanOneFile() (which drives POST /api/scan directly),
  // the import wizard walks scan -> fingerprint -> group -> match as one
  // orchestrated job (jobs/handlers/import_session.py), so the fixture
  // file needs to already be on disk before the wizard starts it.
  fs.copyFileSync(FIXTURE_AUDIO, path.join(muzilla.libraryDir, 'silence.mp3'))

  await page.goto(`${muzilla.baseUrl}/import`)
  await expect(page.getByRole('heading', { name: 'Import a library' })).toBeVisible({ timeout: 10_000 })

  // docs/product-spec.md: the free-text path input was
  // replaced with a read-only display of the configured
  // storage.library_root (the fixture sets that to muzilla.libraryDir)
  // -- there is no longer a text box to type a path into at all.
  await expect(page.getByText(muzilla.libraryDir, { exact: true })).toBeVisible({ timeout: 10_000 })
  await expect(page.getByPlaceholder('/music')).toHaveCount(0)

  await page.getByRole('button', { name: 'Start import' }).click()

  await expect(page).toHaveURL(/\/import\/\d+$/, { timeout: 10_000 })

  await expect(page.getByText('Scan', { exact: true })).toBeVisible({ timeout: 10_000 })
  await expect(page.getByText('Match', { exact: true })).toBeVisible()

  // Wait for the import to leave its running states. The mock includes only
  // unrelated candidates for this local file, so Matching v2 must reject them
  // instead of staging an unrelated proposal.
  await expect(page.getByText(/^(reviewing|completed)$/)).toBeVisible({ timeout: 20_000 })

  // A rejected automatic candidate is still a stable review: it exposes the
  // failure state and makes manual search available instead of disappearing.
  await expect(page.getByText(/Revisioni \(1\)/)).toBeVisible({ timeout: 10_000 })
  await expect(page.getByText('Revisione pronta o in preparazione')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Apri' })).toBeVisible()
})

noLibraryTest('Start import is disabled and a clear message shows when the library root does not exist', async ({
  page,
  muzillaNoLibrary,
}) => {
  // docs/product-spec.md: "a clear message when it is
  // unset" -- library_root always has a configured value (defaults to
  // /music), so "unset" in practice means the directory doesn't exist
  // on disk yet. This fixture points storage.library_root at a path
  // that was never created.
  await page.goto(`${muzillaNoLibrary.baseUrl}/import`)
  await expect(page.getByRole('heading', { name: 'Import a library' })).toBeVisible({ timeout: 10_000 })

  await expect(page.getByText('This path does not exist on disk.')).toBeVisible({ timeout: 10_000 })
  await expect(page.getByRole('button', { name: 'Start import' })).toBeDisabled()
})
