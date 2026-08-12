import { authTest as test, expect } from './fixtures'

test('wrong password, then correct, then logout', async ({ page, muzillaAuth }) => {
  await page.goto(`${muzillaAuth.baseUrl}/catalog`)

  // AuthGuard redirects an unauthenticated visit to /login, preserving
  // the originally-requested path so a post-login redirect can return
  // there (Login.tsx reads location.state.from).
  await expect(page).toHaveURL(/\/login$/, { timeout: 10_000 })

  const passwordInput = page.getByPlaceholder('Password')
  const signInButton = page.getByRole('button', { name: 'Sign in' })

  await passwordInput.fill('definitely-the-wrong-password')
  await signInButton.click()
  await expect(page.getByText('Incorrect password.')).toBeVisible({ timeout: 10_000 })
  // still on the login screen — a failed attempt must not navigate away
  await expect(page).toHaveURL(/\/login$/)

  await passwordInput.fill(muzillaAuth.password)
  await signInButton.click()

  // successful login redirects back to the originally-requested /catalog
  await expect(page).toHaveURL(`${muzillaAuth.baseUrl}/catalog`, { timeout: 10_000 })
  await expect(page.getByRole('button', { name: 'Esci' })).toBeVisible()

  await page.getByRole('button', { name: 'Esci' }).click()
  await expect(page).toHaveURL(/\/login$/, { timeout: 10_000 })

  // logout actually revoked the session server-side (docs/product-spec.md
  // step 2.8), not just cleared client state — a reload must still
  // bounce to /login rather than briefly flashing the catalog.
  await page.reload()
  await expect(page).toHaveURL(/\/login$/, { timeout: 10_000 })
})

test('an empty password never enables the sign-in button', async ({ page, muzillaAuth }) => {
  await page.goto(`${muzillaAuth.baseUrl}/login`)
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeDisabled()
  await page.getByPlaceholder('Password').fill('x')
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeEnabled()
})
