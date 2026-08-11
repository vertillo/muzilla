import { test, expect } from './fixtures'

test('catalog uses accessible sort headers and opens a file detail', async ({ page, muzilla }) => {
  await muzilla.scanOneFile()
  await page.goto(`${muzilla.baseUrl}/catalog`)

  await expect(page.getByRole('button', { name: /Titolo/ })).toBeVisible({ timeout: 10_000 })
  await expect(page.getByRole('link', { name: 'Ágætis byrjun' })).toBeVisible()
  await page.getByRole('link', { name: 'Ágætis byrjun' }).click()
  await expect(page.getByRole('heading', { name: 'Ágætis byrjun' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Rileggi file' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Cerca corrispondenze' })).toBeVisible()
})

test('catalog treats punctuation, operators, Unicode, and blanks as literal search text', async ({ page, muzilla }) => {
  await muzilla.scanOneFile()
  await page.goto(`${muzilla.baseUrl}/catalog`)

  const search = page.getByPlaceholder('Cerca titolo, artista, album o percorso…')
  const cases = [
    { query: 'Ágætis-byrjun', findsTrack: true },
    { query: '"Ágætis"', findsTrack: true },
    { query: 'Ágætis:byrjun', findsTrack: true },
    { query: 'OR', findsTrack: false },
    { query: 'Ágætis', findsTrack: true },
    { query: '   ', findsTrack: true },
  ]

  for (const { query, findsTrack } of cases) {
    const [tracksResponse, facetsResponse] = await Promise.all([
      page.waitForResponse((response) => {
        const url = new URL(response.url())
        return url.pathname === '/api/tracks' && url.searchParams.get('q') === query
      }),
      page.waitForResponse((response) => {
        const url = new URL(response.url())
        return url.pathname === '/api/tracks/facets' && url.searchParams.get('q') === query
      }),
      search.fill(query),
    ])

    expect(tracksResponse.status()).toBe(200)
    expect(facetsResponse.status()).toBe(200)
    await expect(page.getByText('Impossibile caricare il catalogo')).not.toBeVisible()
    if (findsTrack) await expect(page.getByRole('link', { name: 'Ágætis byrjun' })).toBeVisible()
  }
})

test('the catalog exposes duplicate evidence without a separate workspace', async ({ page, muzilla }) => {
  await muzilla.scanOneFile()
  await page.goto(`${muzilla.baseUrl}/catalog`)
  await page.getByRole('link', { name: 'Possibili duplicati' }).click()
  await expect(page).toHaveURL(/\/catalog\?tool=duplicates/)
  await expect(page.getByRole('heading', { name: 'Possibili duplicati' })).toBeVisible()
  await expect(page.getByText(/Muzilla non elimina nulla automaticamente/)).toBeVisible()
})

test('retired technical workspaces are no longer SPA routes', async ({ page, muzilla }) => {
  for (const retiredPath of ['/groups', '/jobs', '/duplicates']) {
    await page.goto(`${muzilla.baseUrl}${retiredPath}`)
    await expect(page).toHaveURL(`${muzilla.baseUrl}/`)
  }
})
