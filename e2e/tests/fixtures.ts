import { test as base } from "@playwright/test";
import { type ChildProcess, spawn, spawnSync } from "node:child_process";
import net from "node:net";
import {
  mkdtempSync,
  writeFileSync,
  mkdirSync,
  copyFileSync,
  existsSync,
  rmSync,
  realpathSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..", "..");
const VENV_PYTHON = path.join(REPO_ROOT, ".venv", "bin", "python");
const FIXTURE_AUDIO = path.join(
  REPO_ROOT,
  "tests",
  "fixtures",
  "audio",
  "silence.mp3",
);

// OS-allocated listen ports: the previous monotonic 20000-25000 / 35000-40000
// ranges overlapped Linux ephemeral ports (32768-60999), so an outbound
// connection could transiently occupy the next app port as an ephemeral source
// and the sequential bind would fail EADDRINUSE (run 33978445940: app bind
// 38064 while mock used 21069). Claiming free ports from the OS just before
// spawn keeps listen ports out of live ephemeral use; a bounded EADDRINUSE-only
// retry covers the residual claim-to-bind race without masking real failures.
export async function claimFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port =
        typeof address === "object" && address ? address.port : 0;
      server.close((err) => {
        if (err) reject(err);
        else if (!port) reject(new Error("OS did not assign a port"));
        else resolve(port);
      });
    });
  });
}

export async function claimDistinctPair(): Promise<[number, number]> {
  const first = await claimFreePort();
  for (let attempt = 0; attempt < 5; attempt++) {
    const second = await claimFreePort();
    if (second !== first) return [first, second];
  }
  throw new Error("could not claim two distinct free ports");
}

export function isAddrInUseTail(tail: string): boolean {
  return (
    tail.includes("EADDRINUSE") || tail.includes("address already in use")
  );
}

/** Bounded capture of a spawned server's output for fail-fast diagnostics.
 * stdio is "pipe" so nothing is lost when the process exits early. */
function captureOutput(proc: ChildProcess): { tail(): string } {
  const chunks: Buffer[] = [];
  let totalBytes = 0;
  const onData = (d: Buffer | string) => {
    const buf = Buffer.isBuffer(d) ? d : Buffer.from(d);
    chunks.push(buf);
    totalBytes += buf.length;
    while (totalBytes > 20000 && chunks.length > 1) {
      const removed = chunks.shift();
      if (removed) totalBytes -= removed.length;
    }
  };
  proc.stdout?.on("data", onData);
  proc.stderr?.on("data", onData);
  return {
    tail(): string {
      try {
        return Buffer.concat(chunks).toString("utf8").slice(-4000);
      } catch {
        return "<unavailable>";
      }
    },
  };
}

function earlyExitError(
  proc: ChildProcess,
  name: string,
  tail: string,
): Error | null {
  if (proc.exitCode !== null || proc.signalCode !== null) {
    return new Error(
      `${name} exited before readiness (exit=${proc.exitCode} signal=${proc.signalCode}). Logs:\n${tail || "<no output>"}`,
    );
  }
  return null;
}

async function waitForHttp(
  url: string,
  timeoutMs: number,
  proc?: ChildProcess,
  procName?: string,
  getTail?: () => string,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let lastStatus = "no attempt";
  while (Date.now() < deadline) {
    if (proc && procName && getTail) {
      const early = earlyExitError(proc, procName, getTail());
      if (early) throw early;
    }
    const controller = new AbortController();
    const t = setTimeout(() => controller.abort(), 2000);
    try {
      const res = await fetch(url, { signal: controller.signal });
      if (res.ok) return;
      lastStatus = `HTTP ${res.status}`;
      try {
        await res.text();
      } catch {
        // ignore body read failure, status is enough
      }
    } catch (err) {
      lastStatus = err instanceof Error ? err.message : String(err);
    } finally {
      clearTimeout(t);
    }
    if (proc && procName && getTail) {
      const early = earlyExitError(proc, procName, getTail());
      if (early) throw early;
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  const suffix =
    proc && procName && getTail
      ? ` (${procName} exit=${proc.exitCode} signal=${proc.signalCode} last=${lastStatus}. Logs:\n${getTail() || "<no output>"})`
      : ` (last=${lastStatus})`;
  throw new Error(`${url} did not become ready within ${timeoutMs}ms${suffix}`);
}

async function stopProcess(process: ChildProcess, name: string): Promise<void> {
  if (process.exitCode !== null || process.signalCode !== null) return;

  const waitForExit = (timeoutMs: number) =>
    new Promise<boolean>((resolve) => {
      if (process.exitCode !== null || process.signalCode !== null) {
        resolve(true);
        return;
      }
      const onExit = () => {
        clearTimeout(timeout);
        resolve(true);
      };
      const timeout = setTimeout(() => {
        process.off("exit", onExit);
        resolve(false);
      }, timeoutMs);
      process.once("exit", onExit);
    });

  process.kill("SIGTERM");
  if (await waitForExit(5_000)) return;

  process.kill("SIGKILL");
  if (await waitForExit(2_000)) return;

  throw new Error(`${name} did not exit after SIGTERM and SIGKILL`);
}

/** One scratch environment per test: its own library dir, DB, and a
 * fresh mock-provider-server + muzilla-serve pair on distinct ports
 * (workers:1 in playwright.config.ts, but tests within one worker
 * still run sequentially, and reusing ports across tests risks a
 * lingering process from a failed previous test). */
export interface MuzillaEnv {
  baseUrl: string;
  libraryDir: string;
  addFixtureFile(filename: string): void;
  addMatchingFixtureFile(filename?: string): void;
  scanOneFile(filename?: string): Promise<void>;
  restartApp(): Promise<void>;
  createManualReview(): Promise<number>;
  createUncertainGroupingReview(): Promise<{
    reviewId: number;
    trackId: number;
  }>;
}

/** auth.spec.ts is the one spec that needs
 * auth.enabled: true — every other spec uses the default `muzilla`
 * fixture below, which leaves auth off so tests can drive the API
 * directly without a login step. Password is fixed and known to the
 * test, not randomly generated: these are throwaway scratch servers
 * bound to 127.0.0.1 and torn down at the end of the test, so there is
 * nothing to protect by randomizing it. */
export const AUTH_PASSWORD = "e2e-test-password-not-a-secret";

function createScratch(opts: {
  authEnabled: boolean;
  createLibraryDir?: boolean;
  urlProviders?: boolean;
}) {
  const scratchRoot = mkdtempSync(path.join(tmpdir(), "muzilla-e2e-"));
  const libraryDir = path.join(scratchRoot, "library");
  const confDir = path.join(scratchRoot, "confdir");
  if (opts.createLibraryDir ?? true) mkdirSync(libraryDir, { recursive: true });
  mkdirSync(confDir, { recursive: true });
  const env = { ...process.env, MUZILLA_CONFIG_DIR: confDir };
  return { scratchRoot, libraryDir, confDir, env };
}

function writeTestConfig(
  confDir: string,
  scratchRoot: string,
  libraryDir: string,
  mockPort: number,
  opts: {
    authEnabled: boolean;
    urlProviders?: boolean;
  },
) {

  const authLines = opts.authEnabled
    ? [
        "auth:",
        "  enabled: true",
        `  password: "${AUTH_PASSWORD}"`,
        '  session_secret: "e2e-test-session-secret"',
      ]
    : ["auth:", "  enabled: false"];

  writeFileSync(
    path.join(confDir, "config.yaml"),
    [
      "storage:",
      `  db_path: ${path.join(scratchRoot, "muzilla.db")}`,
      `  cache_dir: ${path.join(scratchRoot, "cache")}`,
      `  library_root: ${libraryDir}`,
      `  blob_dir: ${path.join(scratchRoot, "blobs")}`,
      `  backup_dir: ${path.join(scratchRoot, "backups")}`,
      ...authLines,
      "paths:",
      "  create_directories: false",
      "providers:",
      "  musicbrainz:",
      "    enabled: true",
      `    base_url_override: "http://127.0.0.1:${mockPort}"`,
      "  discogs:",
      `    enabled: ${opts.urlProviders ? "true" : "false"}`,
      ...(opts.urlProviders
        ? [
            `    base_url_override: "http://127.0.0.1:${mockPort}"`,
            '    token: "e2e-discogs-token"',
          ]
        : []),
      "  deezer:",
      `    enabled: ${opts.urlProviders ? "true" : "false"}`,
      ...(opts.urlProviders
        ? [`    base_url_override: "http://127.0.0.1:${mockPort}"`]
        : []),
      "  acoustid:",
      "    enabled: false",
      "  coverartarchive:",
      "    enabled: false",
      "  lrclib:",
      "    enabled: false",
      "",
    ].join("\n"),
  );

}

/** Spawn a mock/app pair on OS-claimed ports and wait for readiness.
 * Retries only on EADDRINUSE (the residual claim-to-bind race or a
 * transient ephemeral steal); any other early exit fails fast with logs. */
async function bringUpPair(env: NodeJS.ProcessEnv, mockPort: number, appPort: number) {
  const mockServer = spawn(
    VENV_PYTHON,
    [
      path.join(REPO_ROOT, "e2e", "mock_provider_server.py"),
      "--port",
      String(mockPort),
    ],
    { env, cwd: REPO_ROOT, stdio: "pipe" },
  );
  const mockLogs = captureOutput(mockServer);
  const appServer = spawn(
    VENV_PYTHON,
    [
      "-m",
      "uvicorn",
      "muzilla.api.app:app",
      "--host",
      "127.0.0.1",
      "--port",
      String(appPort),
    ],
    { env, cwd: REPO_ROOT, stdio: "pipe" },
  );
  const appLogs = captureOutput(appServer);
  const baseUrl = `http://127.0.0.1:${appPort}`;
  try {
    await waitForHttp(
      `http://127.0.0.1:${mockPort}/release?query=test&limit=1&fmt=json`,
      15_000,
      mockServer,
      "mock provider server",
      () => mockLogs.tail(),
    );
    await waitForHttp(
      `${baseUrl}/api/health`,
      30_000,
      appServer,
      "app server",
      () => appLogs.tail(),
    );
  } catch (err) {
    const tails = `${mockLogs.tail()}\n${appLogs.tail()}`;
    await Promise.allSettled([
      stopProcess(appServer, "app server"),
      stopProcess(mockServer, "mock provider server"),
    ]);
    if (isAddrInUseTail(tails)) {
      const retry = new Error(
        `port collision on mock=${mockPort} app=${appPort}, retry with fresh OS ports: ${tails.slice(-1000)}`,
      );
      (retry as NodeJS.ErrnoException).code = "EADDRINUSE";
      throw retry;
    }
    throw err;
  }
  return { mockServer, appServer, mockLogs, appLogs, baseUrl };
}

export const test = base.extend<{ muzilla: MuzillaEnv; urlProviders: boolean }>(
  {
    urlProviders: [false, { option: true }],
    muzilla: async ({ urlProviders }, use) => {
      const { scratchRoot, libraryDir, confDir, env } = createScratch({
        authEnabled: false,
        urlProviders,
      });
      let mockServer: ChildProcess | null = null;
      let appServer: ChildProcess | null = null;
      let mockLogs = { tail: () => "" } as { tail(): string };
      let appLogs = { tail: () => "" } as { tail(): string };
      let baseUrl = "";
      let thisAppPort = 0;
      let broughtUp = false;
      let lastBringUpError: unknown = null;
      for (let attempt = 1; attempt <= 3 && !broughtUp; attempt++) {
        const [mockPort, appPort] = await claimDistinctPair();
        writeTestConfig(confDir, scratchRoot, libraryDir, mockPort, {
          authEnabled: false,
          urlProviders,
        });
        try {
          const pair = await bringUpPair(env, mockPort, appPort);
          mockServer = pair.mockServer;
          appServer = pair.appServer;
          mockLogs = pair.mockLogs;
          appLogs = pair.appLogs;
          baseUrl = pair.baseUrl;
          thisAppPort = appPort;
          broughtUp = true;
        } catch (err) {
          lastBringUpError = err;
          if ((err as NodeJS.ErrnoException)?.code !== "EADDRINUSE" || attempt === 3) throw err;
        }
      }
      if (!broughtUp || !mockServer || !appServer) throw lastBringUpError;
      const startApp = () =>
        spawn(
          VENV_PYTHON,
          [
            "-m",
            "uvicorn",
            "muzilla.api.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            String(thisAppPort),
          ],
          { env, cwd: REPO_ROOT, stdio: "pipe" },
        );

      try {

        await use({
          baseUrl,
          libraryDir,
          addFixtureFile(filename: string) {
            const dest = path.join(libraryDir, filename);
            if (!existsSync(dest)) copyFileSync(FIXTURE_AUDIO, dest);
          },
          addMatchingFixtureFile(filename = "e2e-source.mp3") {
            const dest = path.join(libraryDir, filename);
            copyFileSync(FIXTURE_AUDIO, dest);
            const script = [
              "import sys",
              "from pathlib import Path",
              "from muzilla.tags.writer import write_fields",
              'write_fields(Path(sys.argv[1]), {"title": "E2E Track", "artist": "E2E Artist", "album": "E2E Album", "album_artist": "E2E Artist", "track_no": 1, "track_total": 1, "year": 1999})',
            ].join("\n");
            const tagged = spawnSync(VENV_PYTHON, ["-c", script, dest], {
              env,
              cwd: REPO_ROOT,
              encoding: "utf8",
            });
            if (tagged.status !== 0)
              throw new Error(
                tagged.stderr || "matching audio fixture setup failed",
              );
          },
          async scanOneFile(filename = "silence.mp3") {
            const dest = path.join(libraryDir, filename);
            if (!existsSync(dest)) copyFileSync(FIXTURE_AUDIO, dest);
            // ponytail: resolve symlinks (/tmp -> /private/tmp on macOS) so Python's Path.resolve() check passes deterministically
            let scanRoot: string;
            try {
              scanRoot = realpathSync(libraryDir);
            } catch {
              scanRoot = libraryDir;
            }
            let jobId: number | undefined;
            let lastScanError = "";
            // ponytail: bounded retry for transient scan-start race (503/502/429 or empty job_id), no infinite poll
            for (let attempt = 0; attempt < 3; attempt++) {
              const res = await fetch(`${baseUrl}/api/scan`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ root: scanRoot }),
              });
              const text = await res.text();
              let body: any = {};
              try {
                body = text ? JSON.parse(text) : {};
              } catch {
                body = { detail: text };
              }
              if (!res.ok) {
                lastScanError = `scan start failed ${res.status}: ${text.slice(0, 500)}`;
                // ponytail: deterministic fallback for /tmp vs /private/tmp symlink drift — use server's own reported root
                if (
                  res.status === 400 &&
                  text.includes("is not the configured library root")
                ) {
                  try {
                    const cfgRes = await fetch(`${baseUrl}/api/imports/config`);
                    if (cfgRes.ok) {
                      const cfg = (await cfgRes.json()) as {
                        library_root: string;
                      };
                      if (cfg.library_root && cfg.library_root !== scanRoot) {
                        scanRoot = cfg.library_root;
                        const altDest = path.join(scanRoot, filename);
                        if (altDest !== dest && !existsSync(altDest)) {
                          try {
                            copyFileSync(FIXTURE_AUDIO, altDest);
                          } catch {
                            /* ignore copy failure, scan will surface */
                          }
                        }
                        await new Promise((r) => setTimeout(r, 100));
                        continue;
                      }
                    }
                  } catch {
                    /* ignore config fetch failure, fall through to throw */
                  }
                }
                if (res.status === 429 || res.status >= 502) {
                  await new Promise((r) => setTimeout(r, 200 * (attempt + 1)));
                  continue;
                }
                throw new Error(
                  lastScanError +
                    ` (scanRoot=${scanRoot} libraryDir=${libraryDir})`,
                );
              }
              if (typeof body.job_id !== "number") {
                lastScanError = `scan start returned no job_id: ${text.slice(0, 500)}`;
                await new Promise((r) => setTimeout(r, 200 * (attempt + 1)));
                continue;
              }
              jobId = body.job_id;
              break;
            }
            if (typeof jobId !== "number") {
              throw new Error(
                lastScanError || "scan start failed: no job_id after retries",
              );
            }
            const jobDeadline = Date.now() + 15_000;
            while (Date.now() < jobDeadline) {
              const jobRes = await fetch(`${baseUrl}/api/jobs/${jobId}`);
              if (!jobRes.ok) {
                const txt = await jobRes.text();
                throw new Error(
                  `scan job ${jobId} fetch failed ${jobRes.status}: ${txt.slice(0, 500)}`,
                );
              }
              const job = await jobRes.json();
              if (job.state === "succeeded") return;
              if (job.state === "failed" || job.state === "cancelled") {
                throw new Error(
                  `scan job ${jobId} ended in state ${job.state}${job.error ? `: ${job.error}` : ""}`,
                );
              }
              await new Promise((r) => setTimeout(r, 200));
            }
            throw new Error(`scan job ${jobId} did not finish within 15s`);
          },
          async createManualReview() {
            // ponytail: brief settle for scan WAL checkpoint before direct DB read
            await new Promise((r) => setTimeout(r, 400));
            const tracksResponse = await fetch(`${baseUrl}/api/tracks?limit=1`);
            const tracks = (await tracksResponse.json()) as {
              items: Array<{ id: number }>;
            };
            const trackId = tracks.items[0]?.id;
            if (trackId === undefined)
              throw new Error(
                "cannot create a manual review without a scanned track",
              );
            const script = [
              "import sys",
              "from muzilla.config.loader import load_config",
              "from muzilla.db.engine import create_db_engine, create_session_factory",
              "from muzilla.db.models import Track",
              "from muzilla.domain.reviews import BundleState",
              "from muzilla.services.reviews import OperationDraft, put_revision, transition_bundle",
              "cfg = load_config()",
              "factory = create_session_factory(create_db_engine(cfg.storage.db_path))",
              "with factory() as session:",
              "    track = session.get(Track, int(sys.argv[1]))",
              "    if track is None:",
              "        from sqlalchemy import select",
              "        ids = list(session.scalars(select(Track.id)).all())",
              '        raise SystemExit(f"track {sys.argv[1]} not found; db={cfg.storage.db_path} ids={ids[:10]} count={len(ids)}")',
              '    write = put_revision(session, logical_key=f"track:{track.id}", title=f"Review {track.filename}", scope_type="track", scope_id=track.id, source_snapshot={"items": [{"source_type": "track", "source_id": track.id, "filename": track.filename, "path": track.path}]}, operations=(OperationDraft(kind="set_tag", field="title", target_type="track", target_id=track.id, current_value=track.title, proposed_value=track.title),))',
              "    transition_bundle(session, write.bundle_id, BundleState.NEEDS_ATTENTION)",
              "    session.commit()",
              "    print(write.bundle_id)",
            ].join("\n");
            const created = spawnSync(
              VENV_PYTHON,
              ["-c", script, String(trackId)],
              {
                env,
                cwd: REPO_ROOT,
                encoding: "utf8",
              },
            );
            if (created.status !== 0)
              throw new Error(created.stderr || "manual review seed failed");
            return Number(created.stdout.trim());
          },
          async restartApp() {
            if (!appServer) throw new Error("app server not started");
            let lastErr: unknown = null;
            for (let attempt = 1; attempt <= 3; attempt++) {
              await stopProcess(appServer, "app server");
              await new Promise((r) => setTimeout(r, 200));
              appServer = startApp();
              appLogs = captureOutput(appServer);
              try {
                await waitForHttp(
                  `${baseUrl}/api/health`,
                  30_000,
                  appServer,
                  "app server",
                  () => appLogs.tail(),
                );
                return;
              } catch (err) {
                lastErr = err;
                const tail = appLogs.tail();
                if (attempt === 3 || !isAddrInUseTail(tail)) throw err;
                await stopProcess(appServer, "app server").catch(() => {});
              }
            }
            throw lastErr;
          },
          async createUncertainGroupingReview() {
            await new Promise((r) => setTimeout(r, 400));
            const tracksResponse = await fetch(`${baseUrl}/api/tracks?limit=1`);
            const tracks = (await tracksResponse.json()) as {
              items: Array<{ id: number }>;
            };
            const trackId = tracks.items[0]?.id;
            if (trackId === undefined)
              throw new Error(
                "cannot create a grouping review without a scanned track",
              );
            const script = [
              "import sys",
              "from muzilla.config.loader import load_config",
              "from muzilla.db.engine import create_db_engine, create_session_factory",
              "from muzilla.db.models import Track, WorkUnit",
              "cfg = load_config()",
              "factory = create_session_factory(create_db_engine(cfg.storage.db_path))",
              "with factory() as session:",
              "    track = session.get(Track, int(sys.argv[1]))",
              "    if track is None:",
              "        from sqlalchemy import select",
              "        ids = list(session.scalars(select(Track.id)).all())",
              '        raise SystemExit(f"track {sys.argv[1]} not found; db={cfg.storage.db_path} ids={ids[:10]} count={len(ids)}")',
              '    track.album = "Shared collection"',
              '    track.album_artist = track.artist or "Test artist"',
              '    source = WorkUnit(key=f"e2e-source:{track.id}", kind="album", grouping_basis="tags", grouping_confidence=0.4, album=track.album, album_artist=track.album_artist, track_count=1)',
              '    target = WorkUnit(key=f"e2e-target:{track.id}", kind="album", grouping_basis="tags", grouping_confidence=1.0, album=track.album, album_artist=track.album_artist, track_count=1)',
              "    session.add_all([source, target])",
              "    session.flush()",
              "    track.work_unit_id = source.id",
              "    session.commit()",
            ].join("\n");
            const seeded = spawnSync(
              VENV_PYTHON,
              ["-c", script, String(trackId)],
              {
                env,
                cwd: REPO_ROOT,
                encoding: "utf8",
              },
            );
            if (seeded.status !== 0)
              throw new Error(seeded.stderr || "grouping review seed failed");
            const response = await fetch(
              `${baseUrl}/api/tracks/${trackId}/review/grouping`,
              { method: "POST" },
            );
            if (!response.ok)
              throw new Error(
                `grouping review failed: ${await response.text()}`,
              );
            const review = (await response.json()) as { id: number };
            return { reviewId: review.id, trackId };
          },
        });
      } finally {
        await Promise.all([
          appServer ? stopProcess(appServer, "app server") : Promise.resolve(),
          mockServer ? stopProcess(mockServer, "mock provider server") : Promise.resolve(),
        ]);
        rmSync(scratchRoot, { recursive: true, force: true });
      }
    },
  },
);

/** Auth-enabled variant of the `muzilla` fixture, for auth.spec.ts only.
 * Every other
 * spec should keep using the default export above — this one requires
 * logging in before any API/UI call against `baseUrl` will succeed. */
export interface MuzillaAuthEnv {
  baseUrl: string;
  password: string;
}

export const authTest = base.extend<{ muzillaAuth: MuzillaAuthEnv }>({
  muzillaAuth: async ({}, use) => {
    const { scratchRoot, libraryDir, confDir, env } = createScratch({ authEnabled: true });
    let mockServer: ChildProcess | null = null;
    let appServer: ChildProcess | null = null;
    let baseUrl = "";
    let lastErr: unknown = null;
    let broughtUp = false;
    for (let attempt = 1; attempt <= 3 && !broughtUp; attempt++) {
      const [mockPort, appPort] = await claimDistinctPair();
      writeTestConfig(confDir, scratchRoot, libraryDir, mockPort, { authEnabled: true });
      try {
        const pair = await bringUpPair(env, mockPort, appPort);
        mockServer = pair.mockServer;
        appServer = pair.appServer;
        baseUrl = pair.baseUrl;
        broughtUp = true;
      } catch (err) {
        lastErr = err;
        if ((err as NodeJS.ErrnoException)?.code !== "EADDRINUSE" || attempt === 3) throw err;
      }
    }
    if (!broughtUp || !mockServer || !appServer) throw lastErr;

    try {
      await use({ baseUrl, password: AUTH_PASSWORD });
    } finally {
      await Promise.all([
        appServer ? stopProcess(appServer, "app server") : Promise.resolve(),
        mockServer ? stopProcess(mockServer, "mock provider server") : Promise.resolve(),
      ]);
      rmSync(scratchRoot, { recursive: true, force: true });
    }
  },
});

/** A `muzilla`-shaped server whose configured storage.library_root
 * directory was never created on disk — for import.spec.ts's "library
 * root does not exist" case only.
 * Every other spec uses the default `muzilla` fixture above, whose
 * library dir always exists. */
export interface MuzillaNoLibraryEnv {
  baseUrl: string;
}

export const noLibraryTest = base.extend<{
  muzillaNoLibrary: MuzillaNoLibraryEnv;
}>({
  muzillaNoLibrary: async ({}, use) => {
    const { scratchRoot, libraryDir, confDir, env } = createScratch({
      authEnabled: false,
      createLibraryDir: false,
    });
    let mockServer: ChildProcess | null = null;
    let appServer: ChildProcess | null = null;
    let baseUrl = "";
    let lastErr: unknown = null;
    let broughtUp = false;
    for (let attempt = 1; attempt <= 3 && !broughtUp; attempt++) {
      const [mockPort, appPort] = await claimDistinctPair();
      writeTestConfig(confDir, scratchRoot, libraryDir, mockPort, { authEnabled: false });
      try {
        const pair = await bringUpPair(env, mockPort, appPort);
        mockServer = pair.mockServer;
        appServer = pair.appServer;
        baseUrl = pair.baseUrl;
        broughtUp = true;
      } catch (err) {
        lastErr = err;
        if ((err as NodeJS.ErrnoException)?.code !== "EADDRINUSE" || attempt === 3) throw err;
      }
    }
    if (!broughtUp || !mockServer || !appServer) throw lastErr;

    try {
      await use({ baseUrl });
    } finally {
      await Promise.all([
        appServer ? stopProcess(appServer, "app server") : Promise.resolve(),
        mockServer ? stopProcess(mockServer, "mock provider server") : Promise.resolve(),
      ]);
      rmSync(scratchRoot, { recursive: true, force: true });
    }
  },
});

export { expect } from "@playwright/test";
