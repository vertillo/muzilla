import { test, expect } from './fixtures'
import { spawnSync } from 'node:child_process'
import { realpathSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = path.resolve(__dirname, '..', '..')
const VENV_PYTHON = path.join(REPO_ROOT, '.venv', 'bin', 'python')

async function createReviewsForAllTracks(baseUrl: string, libraryDir: string, count: number): Promise<number[]> {
  const tracksRes = await fetch(`${baseUrl}/api/tracks?limit=100`)
  const tracks = (await tracksRes.json()) as { items: Array<{ id: number }> }
  const ids = tracks.items.slice(0, count).map((t) => t.id)
  const outIds: number[] = []
  for (const trackId of ids) {
    const script = [
      'import sys',
      'from pathlib import Path',
      'from muzilla.db.engine import create_db_engine, create_session_factory',
      'from muzilla.db.models import Track',
      'from muzilla.domain.reviews import BundleState',
      'from muzilla.services.reviews import OperationDraft, put_revision, transition_bundle',
      'factory = create_session_factory(create_db_engine(Path(sys.argv[1])))',
      'with factory() as session:',
      '    track = session.get(Track, int(sys.argv[2]))',
      '    if track is None:',
      '        from sqlalchemy import select',
      '        ids = list(session.scalars(select(Track.id)).all())',
      '        raise SystemExit(f"track {sys.argv[2]} not found in {sys.argv[1]}; ids={ids[:10]} count={len(ids)}")',
      '    write = put_revision(session, logical_key=f"track:{track.id}", title=f"Review {track.filename}", scope_type="track", scope_id=track.id, source_snapshot={"items": [{"source_type": "track", "source_id": track.id, "filename": track.filename, "path": track.path}]}, operations=(OperationDraft(kind="set_tag", field="title", target_type="track", target_id=track.id, current_value=track.title, proposed_value=track.title),))',
      '    transition_bundle(session, write.bundle_id, BundleState.READY)',
      '    session.commit()',
      '    print(write.bundle_id)',
    ].join('\n')
    const dbPath = (() => { try { return path.join(realpathSync(path.dirname(libraryDir)), 'muzilla.db'); } catch { return path.join(path.dirname(libraryDir), 'muzilla.db'); } })()
    const created = spawnSync(VENV_PYTHON, ['-c', script, dbPath, String(trackId)], { encoding: 'utf8' })
    if (created.status !== 0) throw new Error(created.stderr || 'bulk review seed failed')
    outIds.push(Number(created.stdout.trim()))
  }
  return outIds
}

test('bulk reject previews count, affects only selected, undo restores list and selection', async ({ page, muzilla }) => {
  muzilla.addFixtureFile('bulk-a.flac')
  muzilla.addFixtureFile('bulk-b.flac')
  await muzilla.scanOneFile('bulk-a.flac')
  const reviewIds = await createReviewsForAllTracks(muzilla.baseUrl, muzilla.libraryDir, 2)
  expect(reviewIds.length).toBe(2)

  await page.goto(`${muzilla.baseUrl}/reviews`)
  await expect(page.getByRole('heading', { name: 'Revisioni' })).toBeVisible()

  await expect(page.getByText(/selezionate/)).not.toBeVisible()

  await page.getByLabel('Seleziona visibili').check()
  await expect(page.getByText('2 selezionate')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Rifiuta selezionate (2)' })).toBeVisible()
  await expect(page.getByText(/Solo le revisioni visibili/)).toBeVisible()

  await page.getByRole('button', { name: 'Rifiuta selezionate (2)' }).click()
  await expect(page.getByRole('button', { name: 'Annulla ultimo rifiuto' })).toBeVisible({ timeout: 10_000 })
  // selection cleared for succeeded, failed retains (none here)
  await expect(page.getByText('2 selezionate')).not.toBeVisible()

  // inline undo must restore both list AND prior selection
  await page.getByRole('button', { name: 'Annulla ultimo rifiuto' }).click()
  await expect(page.getByRole('button', { name: 'Annulla ultimo rifiuto' })).not.toBeVisible({ timeout: 10_000 })
  await expect(page.getByText(/2 revisioni nell’ordine/)).toBeVisible({ timeout: 10_000 })
  // selection restored to 2 (F1)
  await expect(page.getByText('2 selezionate')).toBeVisible({ timeout: 10_000 })
  // checkboxes should be checked again
  const checks = page.getByRole('checkbox', { name: /Seleziona revisione/ })
  await expect(checks.first()).toBeChecked()
})

test('bulk partial failure keeps failed selection', async ({ page, muzilla }) => {
  muzilla.addFixtureFile('bulk-part-a.flac')
  muzilla.addFixtureFile('bulk-part-b.flac')
  await muzilla.scanOneFile('bulk-part-a.flac')
  await createReviewsForAllTracks(muzilla.baseUrl, muzilla.libraryDir, 2)

  await page.goto(`${muzilla.baseUrl}/reviews`)
  await expect(page.getByRole('heading', { name: 'Revisioni' })).toBeVisible()
  await page.getByLabel('Seleziona visibili').check()
  await expect(page.getByText('2 selezionate')).toBeVisible()

  let patchCount = 0
  await page.route('**/api/reviews/*/operations', async (route) => {
    if (route.request().method() === 'PATCH') {
      patchCount++
      if (patchCount === 2) {
        await route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ detail: 'review revision changed; reload and retry' }) })
        return
      }
    }
    await route.continue()
  })

  await page.getByRole('button', { name: 'Rifiuta selezionate (2)' }).click()
  await expect(page.getByText(/1 falliti/)).toBeVisible({ timeout: 10_000 })
  await expect(page.getByText('1 selezionate')).toBeVisible()
  await page.unroute('**/api/reviews/*/operations')
})

test('autosave anchor keeps detail open when it leaves active filter and shows banner immediately', async ({ page, muzilla }) => {
  await muzilla.scanOneFile('anchor.flac')
  const reviewId = await muzilla.createManualReview()
  await page.goto(`${muzilla.baseUrl}/reviews/${reviewId}?returnTo=${encodeURIComponent('/reviews?state=ready')}`)
  await expect(page.getByRole('heading', { name: 'anchor.flac' })).toBeVisible()
  await page.getByRole('button', { name: 'Rifiuta tutte' }).click()
  // banner must appear immediately after neighbors invalidation (F2) while detail stays open
  await expect(page.getByText(/non è più nel filtro attivo — rimane ancorata/)).toBeVisible({ timeout: 10_000 })
  await expect(page.getByRole('heading', { name: 'anchor.flac' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Precedente' })).toBeDisabled()
  await page.getByRole('button', { name: 'Chiudi' }).click()
  await expect(page).toHaveURL(/\/reviews\?state=ready/)
  await expect(page.getByText('anchor.flac')).not.toBeVisible()
})

test('unsaved typed edit blocks closing editor and requires Restare or Scartare with no implicit save', async ({ page, muzilla }) => {
  await muzilla.scanOneFile('discard.flac')
  const reviewId = await muzilla.createManualReview()
  await page.goto(`${muzilla.baseUrl}/reviews/${reviewId}?returnTo=%2Freviews`)
  await expect(page.getByRole('heading', { name: 'discard.flac' })).toBeVisible()
  await page.getByRole('button', { name: 'Modifica' }).first().click()
  const input = page.getByLabel('Valore tag')
  await expect(input).toBeVisible()
  await input.fill('Dirty value')
  // Annulla on dirty editor must show discard modal, not close directly
  await page.getByRole('button', { name: 'Annulla' }).click()
  await expect(page.getByRole('dialog', { name: 'Modifiche non salvate' })).toBeVisible()
  await expect(page.getByText(/Hai modifiche non salvate/)).toBeVisible()
  await page.getByRole('button', { name: 'Restare' }).click()
  await expect(page.getByRole('dialog', { name: 'Modifiche non salvate' })).not.toBeVisible()
  await expect(page.getByLabel('Valore tag')).toBeVisible()
  await expect(input).toHaveValue('Dirty value')
  // Scartare discards without saving
  await page.getByRole('button', { name: 'Annulla' }).click()
  await expect(page.getByRole('dialog', { name: 'Modifiche non salvate' })).toBeVisible()
  await page.getByRole('button', { name: 'Scartare' }).click()
  await expect(page.getByRole('dialog', { name: 'Modifiche non salvate' })).not.toBeVisible()
  await expect(page.getByRole('dialog', { name: /Modifica/ })).not.toBeVisible()
  // verify no edit was persisted: reopen and check original still there
  await page.getByRole('button', { name: 'Modifica' }).first().click()
  await expect(page.getByLabel('Valore tag')).not.toHaveValue('Dirty value')
  await page.getByRole('button', { name: 'Annulla' }).click()
  // ensure we can still close detail without discard now (clean)
  await expect(page.getByRole('heading', { name: 'discard.flac' })).toBeVisible()
})

test('unsaved typed edit Back presents Restare/Scartare and Scartare navigates to previous destination', async ({ page, muzilla }) => {
  await muzilla.scanOneFile('discard-back.flac')
  await muzilla.createManualReview()
  await page.goto(`${muzilla.baseUrl}/reviews`)
  await expect(page.getByRole('heading', { name: 'Revisioni' })).toBeVisible()
  await expect(page.getByRole('button', { name: new RegExp(`Apri revisione.*discard-back`) })).toBeVisible()
  await page.getByRole('button', { name: new RegExp(`Apri revisione.*discard-back`) }).click()
  await expect(page.getByRole('heading', { name: 'discard-back.flac' })).toBeVisible()
  await page.getByRole('button', { name: 'Modifica' }).first().click()
  const backInput = page.getByLabel('Valore tag')
  await expect(backInput).toBeVisible()
  await backInput.fill('Dirty back')
  await expect(backInput).toHaveValue('Dirty back')
  await expect.poll(async () => page.evaluate(() => (window as unknown as Record<string, unknown>).__muzillaDirty)).toBe(true)
  await page.evaluate(() => window.history.back())
  await expect(page.getByRole('dialog', { name: 'Modifiche non salvate' })).toBeVisible()
  await page.getByRole('button', { name: 'Restare' }).click()
  await expect(page.getByRole('dialog', { name: 'Modifiche non salvate' })).not.toBeVisible()
  await expect(page.getByRole('heading', { name: 'discard-back.flac' })).toBeVisible()
  await page.evaluate(() => window.history.back())
  await expect(page.getByRole('dialog', { name: 'Modifiche non salvate' })).toBeVisible()
  await page.getByRole('button', { name: 'Scartare' }).click()
  await expect(page).toHaveURL(/\/reviews(\?|#|$)/)
  await expect(page.getByRole('heading', { name: 'Revisioni' })).toBeVisible()
})

test('exposes confidence/issue/source/session filters without losing URL state', async ({ page, muzilla }) => {
  await muzilla.scanOneFile('filter.flac')
  await muzilla.createManualReview()
  await page.goto(`${muzilla.baseUrl}/reviews`)
  await expect(page.getByRole('heading', { name: 'Revisioni' })).toBeVisible()
  await expect(page.getByRole('combobox', { name: 'Confidenza' })).toBeVisible()
  await expect(page.getByRole('combobox', { name: 'Problema' })).toBeVisible()
  await expect(page.getByRole('combobox', { name: 'Provider' })).toBeVisible()
  await expect(page.getByRole('combobox', { name: 'Sessione' })).toBeVisible()

  await page.getByRole('combobox', { name: 'Confidenza' }).selectOption('high_confidence')
  await expect(page).toHaveURL(/confidence=high_confidence/)
  await expect(page.getByText('confidence: high_confidence')).toBeVisible()

  await page.getByRole('combobox', { name: 'Problema' }).selectOption('review')
  await expect(page).toHaveURL(/issue=review/)

  await page.getByLabel('Rimuovi filtro confidence').click()
  await expect(page).not.toHaveURL(/confidence=/)
  await expect(page).toHaveURL(/issue=review/)
  // clear remaining filter so list has an item to open (manual review has no issue filter)
  await page.getByRole('button', { name: 'Cancella filtri' }).first().click()
  await expect(page).not.toHaveURL(/issue=/)
  await expect(page.getByRole('button', { name: /Apri revisione/ }).first()).toBeVisible()
  await page.getByRole('button', { name: /Apri revisione/ }).first().click()
  await expect(page).toHaveURL(/returnTo=/)
  await page.getByRole('button', { name: 'Chiudi' }).click()
  await expect(page).toHaveURL(/\/reviews/)
  await expect(page).not.toHaveURL(/confidence=/)
})
