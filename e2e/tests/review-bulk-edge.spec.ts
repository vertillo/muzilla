import { test, expect } from './fixtures'
import type { ConsoleMessage, Page, Request } from '@playwright/test'
import { spawnSync } from 'node:child_process'
import { realpathSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = path.resolve(__dirname, '..', '..')
const VENV_PYTHON = path.join(REPO_ROOT, '.venv', 'bin', 'python')
const r23BrowserTests = new Set([
  'unsaved typed edit Back presents Restare/Scartare and Scartare navigates to previous destination',
  'saved edits preserve one-step Back and Forward across repeated cycles; failed saves keep native unload protection',
  'Back and Forward Stay repetitions preserve forward destinations and discard the exact transition once',
  'browser-router protocol: DOM-activated dirty links, Close and neighbors preserve filtered origin and replacement history',
])

type BrowserDiagnostics = {
  consoleErrors: string[]
  pageErrors: string[]
  failedRequests: string[]
  stop: () => void
}

const browserDiagnostics = new WeakMap<Page, BrowserDiagnostics>()
test.beforeEach(async ({ page }, testInfo) => {
  if (!r23BrowserTests.has(testInfo.title)) return

  const consoleErrors: string[] = []
  const pageErrors: string[] = []
  const failedRequests: string[] = []
  const onConsole = (message: ConsoleMessage) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  }
  const onPageError = (error: Error) => pageErrors.push(error.stack ?? error.message)
  const onRequestFailed = (request: Request) => {
    failedRequests.push(`${request.method()} ${request.url()}: ${request.failure()?.errorText ?? 'unknown failure'}`)
  }

  page.on('console', onConsole)
  page.on('pageerror', onPageError)
  page.on('requestfailed', onRequestFailed)
  browserDiagnostics.set(page, {
    consoleErrors,
    pageErrors,
    failedRequests,
    stop: () => {
      page.off('console', onConsole)
      page.off('pageerror', onPageError)
      page.off('requestfailed', onRequestFailed)
    },
  })
})

test.afterEach(async ({ page }, testInfo) => {
  const diagnostics = browserDiagnostics.get(page)
  if (!diagnostics) return

  diagnostics.stop()
  browserDiagnostics.delete(page)
  console.log(`[browser diagnostics] ${testInfo.title}: ${JSON.stringify({
    browserVersion: page.context().browser()?.version() ?? 'unknown',
    consoleErrors: diagnostics.consoleErrors,
    pageErrors: diagnostics.pageErrors,
    failedRequests: diagnostics.failedRequests,
  })}`)
})

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
  await expect(page.getByRole('heading', { name: 'anchor.flac' })).not.toBeVisible()
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
  // Escape on the editor is also local-only and never saves implicitly.
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog', { name: 'Modifiche non salvate' })).toBeVisible()
  await page.getByRole('button', { name: 'Restare' }).click()
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
  const detail = page.url()
  const discardDialog = page.getByRole('dialog', { name: 'Modifiche non salvate' })
  await page.evaluate(() => window.history.back())
  await expect(discardDialog).toBeVisible()
  const closeConfirmation = discardDialog.getByRole('button', { name: 'Close' })
  const stay = discardDialog.getByRole('button', { name: 'Restare' })
  const discard = discardDialog.getByRole('button', { name: 'Scartare' })
  await expect(closeConfirmation).toBeFocused()
  await page.keyboard.press('Shift+Tab')
  await expect(discard).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(closeConfirmation).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(stay).toBeFocused()
  await page.keyboard.press('Shift+Tab')
  await expect(closeConfirmation).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(discardDialog).not.toBeVisible()
  await expect(page).toHaveURL(detail)
  await expect(backInput).toHaveValue('Dirty back')
  await expect(backInput).toBeFocused()

  await page.evaluate(() => window.history.back())
  await expect(discardDialog).toBeVisible()
  await page.getByRole('button', { name: 'Restare' }).click()
  await expect(discardDialog).not.toBeVisible()
  await expect(page.getByRole('heading', { name: 'discard-back.flac' })).toBeVisible()
  await page.evaluate(() => window.history.back())
  await expect(page.getByRole('dialog', { name: 'Modifiche non salvate' })).toBeVisible()
  await page.getByRole('button', { name: 'Scartare' }).click()
  await expect(page).toHaveURL(/\/reviews(\?|#|$)/)
  await expect(page.getByRole('heading', { name: 'Revisioni' })).toBeVisible()
})

test('saved edits preserve one-step Back and Forward across repeated cycles; failed saves keep native unload protection', async ({ page, muzilla }) => {
  await muzilla.scanOneFile('history-save.flac')
  expect(await createReviewsForAllTracks(muzilla.baseUrl, muzilla.libraryDir, 1)).toHaveLength(1)
  const inbox = `${muzilla.baseUrl}/reviews?state=ready#position`
  await page.goto(inbox)
  await page.getByRole('button', { name: /Apri revisione.*history-save/ }).click()
  await expect(page.getByRole('heading', { name: 'history-save.flac' })).toBeVisible()
  const detail = page.url()
  for (let cycle = 0; cycle < 2; cycle++) {
    await page.getByRole('button', { name: 'Modifica', exact: true }).first().click()
    await page.getByLabel('Valore tag').fill(`Saved history ${cycle}`)
    await page.getByRole('button', { name: 'Salva modifica' }).click()
    await expect(page.getByLabel('Valore tag')).not.toBeVisible()
    await expect(page).toHaveURL(detail)
    await page.evaluate(() => window.history.back())
    await expect(page).toHaveURL(inbox)
    await expect(page.getByRole('heading', { name: 'Revisioni', exact: true })).toBeVisible()
    await page.evaluate(() => window.history.forward())
    await expect(page).toHaveURL(detail)
    await expect(page.getByRole('heading', { name: 'history-save.flac' })).toBeVisible()
  }
  await page.route('**/api/reviews/*/operations/*/edit', route => route.fulfill({ status: 422, contentType: 'application/json', body: JSON.stringify({ detail: 'invalid edit' }) }))
  await page.getByRole('button', { name: 'Modifica', exact: true }).first().click()
  await page.getByLabel('Valore tag').fill('Failed history save')
  await page.getByRole('button', { name: 'Salva modifica' }).click()
  await expect(page.getByText(/La modifica non è valida/)).toBeVisible()
  await Promise.all([
    page.waitForEvent('dialog').then(async dialog => {
      expect(dialog.type()).toBe('beforeunload')
      await dialog.dismiss()
    }),
    page.evaluate(() => window.location.reload()),
  ])
  await expect(page.getByLabel('Valore tag')).toHaveValue('Failed history save')
  await page.evaluate(() => window.history.back())
  await expect(page.getByRole('dialog', { name: 'Modifiche non salvate' })).toBeVisible()
  await page.getByRole('button', { name: 'Restare' }).click()
  await expect(page).toHaveURL(detail)
  await page.getByRole('button', { name: 'Annulla', exact: true }).click()
  await page.getByRole('button', { name: 'Scartare' }).click()
  await expect(page).toHaveURL(detail)
})

test('Back and Forward Stay repetitions preserve forward destinations and discard the exact transition once', async ({ page, muzilla }) => {
  await muzilla.scanOneFile('history-forward.flac')
  expect(await createReviewsForAllTracks(muzilla.baseUrl, muzilla.libraryDir, 1)).toHaveLength(1)
  const inbox = `${muzilla.baseUrl}/reviews?state=ready#position`
  await page.goto(inbox)
  await page.getByRole('button', { name: /Apri revisione.*history-forward/ }).click()
  await expect(page.getByRole('heading', { name: 'history-forward.flac' })).toBeVisible()
  const detail = page.url()
  await page.getByRole('navigation', { name: 'Navigazione principale' }).getByRole('link', { name: 'Catalogo' }).click()
  await expect(page).toHaveURL(`${muzilla.baseUrl}/catalog`)
  await page.evaluate(() => window.history.back())
  await expect(page).toHaveURL(detail)
  await page.getByRole('button', { name: 'Modifica', exact: true }).first().click()
  await page.getByLabel('Valore tag').fill('Forward dirty')
  for (const direction of ['back', 'forward', 'back', 'forward'] as const) {
    await page.evaluate(direction => window.history[direction](), direction)
    await expect(page.getByRole('dialog', { name: 'Modifiche non salvate' })).toBeVisible()
    await page.getByRole('button', { name: 'Restare' }).click()
    await expect(page.getByRole('dialog', { name: 'Modifiche non salvate' })).not.toBeVisible()
    await expect(page).toHaveURL(detail)
    await expect(page.getByLabel('Valore tag')).toHaveValue('Forward dirty')
  }
  await page.evaluate(() => window.history.forward())
  await page.getByRole('button', { name: 'Scartare' }).click()
  await expect(page).toHaveURL(`${muzilla.baseUrl}/catalog`)
  await expect(page.getByRole('heading', { name: 'Catalogo', exact: true })).toBeVisible()
  await page.evaluate(() => window.history.back())
  await expect(page).toHaveURL(detail)
  await expect(page.getByRole('heading', { name: 'history-forward.flac' })).toBeVisible()
  await page.evaluate(() => window.history.back())
  await expect(page).toHaveURL(inbox)
  await expect(page.getByRole('heading', { name: 'Revisioni', exact: true })).toBeVisible()
})

test('browser-router protocol: DOM-activated dirty links, Close and neighbors preserve filtered origin and replacement history', async ({ page, muzilla }) => {
  muzilla.addFixtureFile('history-a.flac')
  muzilla.addFixtureFile('history-b.flac')
  await muzilla.scanOneFile('history-a.flac')
  await createReviewsForAllTracks(muzilla.baseUrl, muzilla.libraryDir, 2)
  const inbox = `${muzilla.baseUrl}/reviews?state=ready#position`
  await page.goto(inbox)
  await page.getByRole('button', { name: /Apri revisione/ }).first().click()
  const first = page.url()
  const returnTo = new URL(new URL(first).searchParams.get('returnTo')!, muzilla.baseUrl).href
  for (const action of ['Revisioni', 'Catalogo', 'Chiudi', 'Successiva']) {
    await page.getByRole('button', { name: 'Modifica', exact: true }).first().click()
    await page.getByLabel('Valore tag').fill(`Dirty ${action}`)
    // Protocol coverage only: the editor overlay intentionally prevents background pointer clicks.
    const target = action === 'Catalogo'
      ? page.getByRole('navigation', { name: 'Navigazione principale' }).getByRole('link', { name: action })
      : action === 'Revisioni'
        ? page.getByRole('link', { name: action, exact: true }).last()
        : page.getByRole('button', { name: action, exact: true })
    const trigger = () => target.evaluate(element => (element as HTMLElement).click())
    await trigger()
    await expect(page.getByRole('dialog', { name: 'Modifiche non salvate' })).toBeVisible()
    await page.getByRole('button', { name: 'Restare' }).click()
    await expect(page).toHaveURL(first)
    await expect(page.getByLabel('Valore tag')).toHaveValue(`Dirty ${action}`)
    await trigger()
    await page.getByRole('button', { name: 'Scartare' }).click()
    if (action === 'Successiva') {
      await expect(page).not.toHaveURL(first)
      await expect(page.getByRole('button', { name: 'Precedente', exact: true })).toBeEnabled()
      const second = page.url()
      await page.getByRole('button', { name: 'Modifica', exact: true }).first().click()
      await page.getByLabel('Valore tag').fill('Dirty previous')
      await page.getByRole('button', { name: 'Precedente', exact: true }).evaluate(element => (element as HTMLElement).click())
      await page.getByRole('button', { name: 'Restare' }).click()
      await expect(page).toHaveURL(second)
      await page.getByRole('button', { name: 'Precedente', exact: true }).evaluate(element => (element as HTMLElement).click())
      await page.getByRole('button', { name: 'Scartare' }).click()
      await expect(page).toHaveURL(first)
      // Both neighbor transitions replace the open item; Back must not revisit either detail.
      await page.evaluate(() => window.history.back())
      await expect(page).toHaveURL(inbox)
    } else {
      await expect(page).toHaveURL(action === 'Catalogo' ? `${muzilla.baseUrl}/catalog` : returnTo)
      await expect(page.getByRole('heading', { name: action === 'Catalogo' ? 'Catalogo' : 'Revisioni', exact: true })).toBeVisible()
      await page.evaluate(() => window.history.back())
      await expect(page).toHaveURL(first)
    }
  }
  await expect(page.getByRole('heading', { name: 'Revisioni', exact: true })).toBeVisible()
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
