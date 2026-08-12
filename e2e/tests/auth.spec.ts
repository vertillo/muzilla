import { authTest as test, expect } from './fixtures'

test('wrong password, then correct, then logout', async ({ page, muzillaAuth }) => {
  await page.goto(`${muzillaAuth.baseUrl}/catalog`)

  // AuthGuard preserves the requested path so login can return to it.
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

  // Successful login returns to the requested /catalog route.
  await expect(page).toHaveURL(`${muzillaAuth.baseUrl}/catalog`, { timeout: 10_000 })
  await expect(page.getByRole('button', { name: 'Esci' })).toBeVisible()

  await page.getByRole('button', { name: 'Esci' }).click()
  await expect(page).toHaveURL(/\/login$/, { timeout: 10_000 })

  // Logout must actually revoke the session server-side, not just clear
  // client state — a reload must still
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
