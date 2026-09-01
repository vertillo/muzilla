import { test, expect } from './fixtures'
import { spawnSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = path.resolve(__dirname, '..', '..')
const VENV_PYTHON = path.join(REPO_ROOT, '.venv', 'bin', 'python')

test.use({ urlProviders: true })

test('supported provider URLs import through every configured adapter without proxying user input', async ({
  page,
  muzilla,
}) => {
  const urlCases = [
    {
      url: 'https://musicbrainz.org/release/076ad60e-2b19-31b0-9c02-a5a2f0a4b1c8',
      provider: 'musicbrainz',
      candidateType: 'release',
      providerId: '076ad60e-2b19-31b0-9c02-a5a2f0a4b1c8',
    },
    {
      url: 'https://www.deezer.com/track/3146202',
      provider: 'deezer',
      candidateType: 'track',
      providerId: '3146202',
    },
    {
      url: 'https://www.discogs.com/release/439334-Sigur-Ros-Agaetis-Byrjun',
      provider: 'discogs',
      candidateType: 'release',
      providerId: '439334',
    },
    {
      url: 'https://www.deezer.com/album/302127',
      provider: 'deezer',
      candidateType: 'album',
      providerId: '302127',
    },
  ]

  let counter = 0
  async function createReviewForTrack(trackMeta: { title: string; artist: string; album: string; year: number }): Promise<number> {
    counter += 1
    const libDir = muzilla.libraryDir
    const dbPath = path.join(path.dirname(libDir), 'muzilla.db')
    const script = [
      'import sys',
      'from pathlib import Path',
      'from datetime import datetime, UTC',
      'from muzilla.db.engine import create_db_engine, create_session_factory',
      'from muzilla.db.models import Track',
      'from muzilla.domain.reviews import BundleState',
      'from muzilla.services.reviews import OperationDraft, put_revision, transition_bundle',
      'db_path = Path(sys.argv[1])',
      'title = sys.argv[2]',
      'artist = sys.argv[3]',
      'album = sys.argv[4]',
      'year = int(sys.argv[5])',
      'idx = sys.argv[6]',
      'factory = create_session_factory(create_db_engine(db_path))',
      'now = datetime.now(UTC)',
      'with factory() as session:',
      '    safe = title.replace(" ", "_").replace("/", "_")',
      '    t = Track(path=f"/music/{safe}-{artist}-{idx}.mp3".replace(" ", "_"), filename=f"{safe}-{idx}.mp3", ext="mp3", size_bytes=1000, mtime_ns=int(idx), title=title, artist=artist, album=album, album_artist=artist, year=year, first_seen_at=now, last_scanned_at=now)',
      '    session.add(t)',
      '    session.flush()',
      '    write = put_revision(session, logical_key=f"track:{t.id}", title=f"Review {t.filename}", scope_type="track", scope_id=t.id, source_snapshot={"items": [{"source_type": "track", "source_id": t.id, "filename": t.filename, "path": t.path}]}, operations=(OperationDraft(kind="set_tag", field="title", target_type="track", target_id=t.id, current_value=title, proposed_value=title),))',
      '    transition_bundle(session, write.bundle_id, BundleState.NEEDS_ATTENTION)',
      '    session.commit()',
      '    print(write.bundle_id)',
    ].join('\n')
    const res = spawnSync(VENV_PYTHON, ['-c', script, dbPath, trackMeta.title, trackMeta.artist, trackMeta.album, String(trackMeta.year), String(counter)], {
      encoding: 'utf8',
      timeout: 5000,
    })
    if (res.status !== 0) throw new Error(res.stderr || 'create track failed')
    return Number(res.stdout.trim())
  }

  // Test each URL with a matching track so candidate is not rejected; each uses its own review to isolate matching
  for (const candidate of urlCases) {
    const reviewId = await createReviewForTrack({
      title: 'Svefn-g-englar',
      artist: 'Sigur Rós',
      album: 'Ágætis byrjun',
      year: 1999,
    })

    const recognized = await page.request.post(
      `${muzilla.baseUrl}/api/reviews/${reviewId}/candidates/url/recognize`,
      { data: { url: candidate.url } },
    )
    expect(recognized.ok()).toBeTruthy()
    expect(await recognized.json()).toEqual({
      provider: candidate.provider,
      candidate_type: candidate.candidateType,
      provider_id: candidate.providerId,
    })

    const first = await page.request.post(
      `${muzilla.baseUrl}/api/reviews/${reviewId}/candidates/url/import`,
      { data: { url: candidate.url } },
    )
    if (!first.ok()) console.log('first failed', candidate.url, first.status(), await first.text())
    const second = await page.request.post(
      `${muzilla.baseUrl}/api/reviews/${reviewId}/candidates/url/import`,
      { data: { url: candidate.url } },
    )
    if (!second.ok()) console.log('second failed', candidate.url, second.status(), await second.text())
    expect(first.ok()).toBeTruthy()
    expect(second.ok()).toBeTruthy()
    const firstBody = await first.json()
    const secondBody = await second.json()
    expect(firstBody.already_selected).toBe(false)
    expect(secondBody.already_selected).toBe(true)
    expect(firstBody.review.id).toBe(reviewId)
    expect(secondBody.review.current_revision.id).toBe(firstBody.review.current_revision.id)
  }

  // Private/local URL must be rejected with 400 without proxying
  const anyReviewId = await createReviewForTrack({
    title: 'Svefn-g-englar',
    artist: 'Sigur Rós',
    album: 'Ágætis byrjun',
    year: 1999,
  })
  const rejected = await page.request.post(
    `${muzilla.baseUrl}/api/reviews/${anyReviewId}/candidates/url/import`,
    { data: { url: 'http://127.0.0.1:8765/private' } },
  )
  expect(rejected.status()).toBe(400)
})
