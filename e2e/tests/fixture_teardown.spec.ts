import { existsSync } from 'node:fs'
import path from 'node:path'

import { test, expect } from './fixtures'

let scratchRoot: string | undefined

test('removes its scratch environment after server teardown', async ({ muzilla }) => {
  scratchRoot = path.dirname(muzilla.libraryDir)
  expect(existsSync(scratchRoot)).toBe(true)
})

test.afterAll(() => {
  if (scratchRoot === undefined) return
  expect(existsSync(scratchRoot)).toBe(false)
})
