import { test as base } from '@playwright/test'
import { ChildProcess, spawn } from 'node:child_process'
import { mkdtempSync, writeFileSync, mkdirSync, copyFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = path.resolve(__dirname, '..', '..')
const VENV_PYTHON = path.join(REPO_ROOT, '.venv', 'bin', 'python')
const FIXTURE_AUDIO = path.join(REPO_ROOT, 'tests', 'fixtures', 'audio', 'silence.mp3')

let mockProviderPort = 8765
let appPort = 8180

async function waitForHttp(url: string, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url)
      if (res.ok) return
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, 200))
  }
  throw new Error(`${url} did not become ready within ${timeoutMs}ms`)
}

/** One scratch environment per test: its own library dir, DB, and a
 * fresh mock-provider-server + muzilla-serve pair on distinct ports
 * (workers:1 in playwright.config.ts, but tests within one worker
 * still run sequentially, and reusing ports across tests risks a
 * lingering process from a failed previous test). */
export interface MuzillaEnv {
  baseUrl: string
  libraryDir: string
  addFixtureFile(filename: string): void
  scanOneFile(filename?: string): Promise<void>
}

/** auth.spec.ts (docs/PLAN.md §12e step 4.3) is the one spec that needs
 * auth.enabled: true — every other spec uses the default `muzilla`
 * fixture below, which leaves auth off so tests can drive the API
 * directly without a login step. Password is fixed and known to the
 * test, not randomly generated: these are throwaway scratch servers
 * bound to 127.0.0.1 and torn down at the end of the test, so there is
 * nothing to protect by randomizing it. */
export const AUTH_PASSWORD = 'e2e-test-password-not-a-secret'

function buildEnvAndConfig(opts: { authEnabled: boolean; createLibraryDir?: boolean }) {
  const scratchRoot = mkdtempSync(path.join(tmpdir(), 'muzilla-e2e-'))
  const libraryDir = path.join(scratchRoot, 'library')
  const confDir = path.join(scratchRoot, 'confdir')
  if (opts.createLibraryDir ?? true) mkdirSync(libraryDir, { recursive: true })
  mkdirSync(confDir, { recursive: true })

  const thisMockPort = mockProviderPort++
  const thisAppPort = appPort++

  const authLines = opts.authEnabled
    ? ['auth:', '  enabled: true', `  password: "${AUTH_PASSWORD}"`, '  session_secret: "e2e-test-session-secret"']
    : ['auth:', '  enabled: false']

  writeFileSync(
    path.join(confDir, 'config.yaml'),
    [
      'storage:',
      `  db_path: ${path.join(scratchRoot, 'muzilla.db')}`,
      `  cache_dir: ${path.join(scratchRoot, 'cache')}`,
      `  library_root: ${libraryDir}`,
      `  blob_dir: ${path.join(scratchRoot, 'blobs')}`,
      `  backup_dir: ${path.join(scratchRoot, 'backups')}`,
      ...authLines,
      'paths:',
      '  create_directories: false',
      'providers:',
      '  musicbrainz:',
      '    enabled: true',
      `    base_url_override: "http://127.0.0.1:${thisMockPort}"`,
      '  discogs:',
      '    enabled: false',
      '  deezer:',
      '    enabled: false',
      '  acoustid:',
      '    enabled: false',
      '  coverartarchive:',
      '    enabled: false',
      '  lrclib:',
      '    enabled: false',
      '',
    ].join('\n'),
  )

  const env = { ...process.env, MUZILLA_CONFIG_DIR: confDir }
  return { scratchRoot, libraryDir, thisMockPort, thisAppPort, env }
}

export const test = base.extend<{ muzilla: MuzillaEnv }>({
  muzilla: async ({}, use) => {
    const { libraryDir, thisMockPort, thisAppPort, env } = buildEnvAndConfig({ authEnabled: false })

    const mockServer: ChildProcess = spawn(
      VENV_PYTHON,
      [path.join(REPO_ROOT, 'e2e', 'mock_provider_server.py'), '--port', String(thisMockPort)],
      { env, cwd: REPO_ROOT, stdio: 'pipe' },
    )

    const appServer: ChildProcess = spawn(
      VENV_PYTHON,
      ['-m', 'uvicorn', 'muzilla.api.app:app', '--host', '127.0.0.1', '--port', String(thisAppPort)],
      { env, cwd: REPO_ROOT, stdio: 'pipe' },
    )

    const baseUrl = `http://127.0.0.1:${thisAppPort}`

    try {
      await waitForHttp(`http://127.0.0.1:${thisMockPort}/release?query=test&limit=1&fmt=json`, 10_000)
      await waitForHttp(`${baseUrl}/api/health`, 20_000)

      await use({
        baseUrl,
        libraryDir,
        addFixtureFile(filename: string) {
          const dest = path.join(libraryDir, filename)
          if (!existsSync(dest)) copyFileSync(FIXTURE_AUDIO, dest)
        },
        async scanOneFile(filename = 'silence.mp3') {
          const dest = path.join(libraryDir, filename)
          if (!existsSync(dest)) copyFileSync(FIXTURE_AUDIO, dest)
          const res = await fetch(`${baseUrl}/api/scan`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ root: libraryDir }),
          })
          const body = await res.json()
          const jobId = body.job_id
          const jobDeadline = Date.now() + 10_000
          while (Date.now() < jobDeadline) {
            const jobRes = await fetch(`${baseUrl}/api/jobs/${jobId}`)
            const job = await jobRes.json()
            if (job.state === 'succeeded') return
            if (job.state === 'failed' || job.state === 'cancelled') {
              throw new Error(`scan job ${jobId} ended in state ${job.state}`)
            }
            await new Promise((r) => setTimeout(r, 200))
          }
          throw new Error(`scan job ${jobId} did not finish within 10s`)
        },
      })
    } finally {
      appServer.kill()
      mockServer.kill()
    }
  },
})

/** Auth-enabled variant of the `muzilla` fixture, for auth.spec.ts only
 * (docs/PLAN.md §12e step 4.3: "the current fixture sets auth.enabled:
 * false, so the entire auth path is untested end to end"). Every other
 * spec should keep using the default export above — this one requires
 * logging in before any API/UI call against `baseUrl` will succeed. */
export interface MuzillaAuthEnv {
  baseUrl: string
  password: string
}

export const authTest = base.extend<{ muzillaAuth: MuzillaAuthEnv }>({
  muzillaAuth: async ({}, use) => {
    const { thisMockPort, thisAppPort, env } = buildEnvAndConfig({ authEnabled: true })

    const mockServer: ChildProcess = spawn(
      VENV_PYTHON,
      [path.join(REPO_ROOT, 'e2e', 'mock_provider_server.py'), '--port', String(thisMockPort)],
      { env, cwd: REPO_ROOT, stdio: 'pipe' },
    )

    const appServer: ChildProcess = spawn(
      VENV_PYTHON,
      ['-m', 'uvicorn', 'muzilla.api.app:app', '--host', '127.0.0.1', '--port', String(thisAppPort)],
      { env, cwd: REPO_ROOT, stdio: 'pipe' },
    )

    const baseUrl = `http://127.0.0.1:${thisAppPort}`

    try {
      await waitForHttp(`http://127.0.0.1:${thisMockPort}/release?query=test&limit=1&fmt=json`, 10_000)
      await waitForHttp(`${baseUrl}/api/health`, 20_000)

      await use({ baseUrl, password: AUTH_PASSWORD })
    } finally {
      appServer.kill()
      mockServer.kill()
    }
  },
})

/** A `muzilla`-shaped server whose configured storage.library_root
 * directory was never created on disk — for import.spec.ts's "library
 * root does not exist" case only (docs/PLAN.md §12e step 6.5 item 4).
 * Every other spec uses the default `muzilla` fixture above, whose
 * library dir always exists. */
export interface MuzillaNoLibraryEnv {
  baseUrl: string
}

export const noLibraryTest = base.extend<{ muzillaNoLibrary: MuzillaNoLibraryEnv }>({
  muzillaNoLibrary: async ({}, use) => {
    const { thisMockPort, thisAppPort, env } = buildEnvAndConfig({
      authEnabled: false,
      createLibraryDir: false,
    })

    const mockServer: ChildProcess = spawn(
      VENV_PYTHON,
      [path.join(REPO_ROOT, 'e2e', 'mock_provider_server.py'), '--port', String(thisMockPort)],
      { env, cwd: REPO_ROOT, stdio: 'pipe' },
    )

    const appServer: ChildProcess = spawn(
      VENV_PYTHON,
      ['-m', 'uvicorn', 'muzilla.api.app:app', '--host', '127.0.0.1', '--port', String(thisAppPort)],
      { env, cwd: REPO_ROOT, stdio: 'pipe' },
    )

    const baseUrl = `http://127.0.0.1:${thisAppPort}`

    try {
      await waitForHttp(`http://127.0.0.1:${thisMockPort}/release?query=test&limit=1&fmt=json`, 10_000)
      await waitForHttp(`${baseUrl}/api/health`, 20_000)

      await use({ baseUrl })
    } finally {
      appServer.kill()
      mockServer.kill()
    }
  },
})

export { expect } from '@playwright/test'
