import { test, expect } from './fixtures'

test('the review screen has a persistent hint and a ? overlay listing every shortcut', async ({
  page,
  muzilla,
}) => {
  await muzilla.scanOneFile()

  const tracksRes = await page.request.get(`${muzilla.baseUrl}/api/tracks`)
  const trackId = (await tracksRes.json()).items[0].id
  const patchRes = await page.request.patch(`${muzilla.baseUrl}/api/tracks/${trackId}`, {
    data: { fields: { title: 'shortcut-overlay-test' } },
  })
  const detail = await patchRes.json()

  await page.goto(`${muzilla.baseUrl}/changes/${detail.id}`)
  await expect(page.getByRole('heading', { name: detail.title })).toBeVisible({ timeout: 10_000 })

  // docs/product-spec.md: the footer hint is always visible, not
  // just discoverable via the overlay itself.
  await expect(page.getByText('j/k navigate')).toBeVisible()
  await expect(page.getByText('? for all shortcuts')).toBeVisible()

  await page.keyboard.press('?')
  await expect(page.getByText('Keyboard shortcuts')).toBeVisible()
  await expect(page.getByText('Move focus to the next / previous change')).toBeVisible()
  await expect(page.getByText('Open the apply confirmation (draft only)')).toBeVisible()

  // ? toggles the overlay closed again.
  await page.keyboard.press('?')
  await expect(page.getByText('Keyboard shortcuts')).not.toBeVisible()

  // Clicking the footer hint's link opens it too.
  await page.getByRole('button', { name: '? for all shortcuts' }).click()
  await expect(page.getByText('Keyboard shortcuts')).toBeVisible()
})
