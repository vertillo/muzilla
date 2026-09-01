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

test('catalog columns resize with pointer and keyboard, persist, reset, and keep responsive overflow', async ({ page, muzilla }) => {
  await muzilla.scanOneFile()
  await page.goto(`${muzilla.baseUrl}/catalog`)

  const table = page.getByRole('table', { name: 'Catalogo dei file' })
  await expect(table).toBeVisible()
  for (const label of ['Titolo', 'Artista', 'Album', 'Aggiunto', 'Formato', 'Durata', 'Stato']) {
    const resize = page.getByRole('separator', { name: `Ridimensiona colonna ${label}` })
    await expect(resize).toBeVisible()
    expect(await resize.evaluate((element) => element.getBoundingClientRect().width)).toBeGreaterThanOrEqual(24)
  }

  const overflow = await table.evaluate((element) => {
    const container = element.parentElement
    return { clientWidth: container?.clientWidth ?? 0, scrollWidth: container?.scrollWidth ?? 0 }
  })
  expect(overflow.scrollWidth).toBeGreaterThan(overflow.clientWidth)

  const titleResize = page.getByRole('separator', { name: 'Ridimensiona colonna Titolo' })
  const titleBefore = Number(await titleResize.getAttribute('aria-valuenow'))
  const titleBox = await titleResize.boundingBox()
  expect(titleBox).not.toBeNull()
  if (!titleBox) throw new Error('title resize handle has no layout box')
  await page.mouse.move(titleBox.x + titleBox.width / 2, titleBox.y + titleBox.height / 2)
  await page.mouse.down()
  await page.mouse.move(titleBox.x + titleBox.width / 2 + 40, titleBox.y + titleBox.height / 2)
  await page.mouse.up()
  const titleAfterDrag = Number(await titleResize.getAttribute('aria-valuenow'))
  expect(titleAfterDrag).toBeGreaterThan(titleBefore)

  const artistResize = page.getByRole('separator', { name: 'Ridimensiona colonna Artista' })
  const artistBefore = Number(await artistResize.getAttribute('aria-valuenow'))
  await artistResize.focus()
  await page.keyboard.press('ArrowRight')
  await expect(artistResize).toHaveAttribute('aria-valuenow', String(artistBefore + 16))

  const savedWidths = await page.evaluate((key) => window.localStorage.getItem(key), 'muzilla.catalog.column-widths')
  expect(savedWidths).not.toBeNull()
  expect(JSON.parse(savedWidths ?? '{}')).toMatchObject({ title: titleAfterDrag, artist: artistBefore + 16 })

  await page.reload()
  await expect(page.getByRole('separator', { name: 'Ridimensiona colonna Titolo' })).toHaveAttribute('aria-valuenow', String(titleAfterDrag))
  await expect(page.getByRole('separator', { name: 'Ridimensiona colonna Artista' })).toHaveAttribute('aria-valuenow', String(artistBefore + 16))

  await page.getByRole('button', { name: 'Ripristina larghezze colonne' }).click()
  await expect(page.getByRole('separator', { name: 'Ridimensiona colonna Titolo' })).toHaveAttribute('aria-valuenow', '240')
  expect(await page.evaluate((key) => window.localStorage.getItem(key), 'muzilla.catalog.column-widths')).toBeNull()

  const bounds = [
    { label: 'Titolo', min: '160', max: '420' },
    { label: 'Artista', min: '120', max: '320' },
    { label: 'Album', min: '140', max: '360' },
    { label: 'Aggiunto', min: '96', max: '200' },
    { label: 'Formato', min: '88', max: '180' },
    { label: 'Durata', min: '88', max: '180' },
    { label: 'Stato', min: '188', max: '360' },
  ]
  for (const { label, min, max } of bounds) {
    const resize = page.getByRole('separator', { name: `Ridimensiona colonna ${label}` })
    await resize.focus()
    await page.keyboard.press('Home')
    await expect(resize).toHaveAttribute('aria-valuenow', min)
    await page.keyboard.press('ArrowLeft')
    await expect(resize).toHaveAttribute('aria-valuenow', min)
    await page.keyboard.press('End')
    await expect(resize).toHaveAttribute('aria-valuenow', max)
    await page.keyboard.press('ArrowRight')
    await expect(resize).toHaveAttribute('aria-valuenow', max)
  }

  await page.setViewportSize({ width: 375, height: 800 })
  await expect(table).not.toBeVisible()
  await expect(page.getByRole('article')).toBeVisible()
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
    const tracksResponse = await Promise.all([
      page.waitForResponse((response) => {
        const url = new URL(response.url())
        return url.pathname === '/api/tracks' && url.searchParams.get('q') === query
      }),
      search.fill(query),
    ]).then(([res]) => res)
    // Facets are lazy (only fetched when a facet combobox is open) since UX-CATALOG-002;
    // the literal-search contract is proven by the tracks request alone. If facets happen to be
    // open, they will also be filtered literally, but we do not require a facets fetch here.
    expect(tracksResponse.status()).toBe(200)
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
