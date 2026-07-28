import type { Page } from '@playwright/test'
import { authTest, test, expect } from './fixtures'

/** Collects `console` messages that are CSP violation reports. Chromium
 * reports a blocked resource as a `console.error` whose text starts
 * with "Refused to" — that's the signal docs/PLAN.md §12c step 2.3
 * asks this spec to prove is absent, not just that the header exists
 * (a unit test of the header string, which api/test_middleware.py
 * already covers, doesn't prove the policy is survivable in a real
 * browser). */
function collectCspViolations(page: Page): string[] {
  const violations: string[] = []
  page.on('console', (msg) => {
    if (msg.type() === 'error' && /Refused to|Content Security Policy/.test(msg.text())) {
      violations.push(msg.text())
    }
  })
  return violations
}

const ROUTES = ['/catalog', '/groups', '/changes', '/jobs', '/duplicates', '/import']

for (const route of ROUTES) {
  test(`zero CSP violations on ${route}`, async ({ page, muzilla }) => {
    await muzilla.scanOneFile()
    const violations = collectCspViolations(page)

    await page.goto(`${muzilla.baseUrl}${route}`)
    await page.waitForLoadState('networkidle')

    expect(violations).toEqual([])
  })
}

authTest('zero CSP violations on /login', async ({ page, muzillaAuth }) => {
  const violations = collectCspViolations(page)

  await page.goto(`${muzillaAuth.baseUrl}/login`)
  await page.waitForLoadState('networkidle')

  expect(violations).toEqual([])
})
