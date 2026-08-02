import { test, expect } from './fixtures'

test.use({ urlProviders: true })

test('supported provider URLs import through every configured adapter without proxying user input', async ({
  page,
  muzilla,
}) => {
  await muzilla.scanOneFile()
  const reviewId = await muzilla.createManualReview()
  const urls = [
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

  for (const candidate of urls) {
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
    const second = await page.request.post(
      `${muzilla.baseUrl}/api/reviews/${reviewId}/candidates/url/import`,
      { data: { url: candidate.url } },
    )
    expect(first.ok()).toBeTruthy()
    expect(second.ok()).toBeTruthy()
    const firstBody = await first.json()
    const secondBody = await second.json()
    expect(firstBody.already_selected).toBe(false)
    expect(secondBody.already_selected).toBe(true)
    expect(firstBody.review.id).toBe(reviewId)
    expect(secondBody.review.current_revision.id).toBe(firstBody.review.current_revision.id)
  }

  const rejected = await page.request.post(
    `${muzilla.baseUrl}/api/reviews/${reviewId}/candidates/url/import`,
    { data: { url: 'http://127.0.0.1:8765/private' } },
  )
  expect(rejected.status()).toBe(400)
})
