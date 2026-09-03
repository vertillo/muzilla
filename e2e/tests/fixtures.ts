import { test as base } from "@playwright/test";
import { type ChildProcess, spawn, spawnSync } from "node:child_process";
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

let mockProviderPort = 20000 + Math.floor(Math.random() * 20000);
let appPort = 30000 + Math.floor(Math.random() * 20000);

async function waitForHttp(url: string, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const controller = new AbortController();
    const t = setTimeout(() => controller.abort(), 2000);
    try {
      const res = await fetch(url, { signal: controller.signal });
      if (res.ok) return;
    } catch {
      // not up yet or timed out
    } finally {
      clearTimeout(t);
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error(`${url} did not become ready within ${timeoutMs}ms`);
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

function buildEnvAndConfig(opts: {
  authEnabled: boolean;
  createLibraryDir?: boolean;
  urlProviders?: boolean;
}) {
  const scratchRoot = mkdtempSync(path.join(tmpdir(), "muzilla-e2e-"));
  const libraryDir = path.join(scratchRoot, "library");
  const confDir = path.join(scratchRoot, "confdir");
  if (opts.createLibraryDir ?? true) mkdirSync(libraryDir, { recursive: true });
  mkdirSync(confDir, { recursive: true });

  const thisMockPort = mockProviderPort++;
  const thisAppPort = appPort++;

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
      `    base_url_override: "http://127.0.0.1:${thisMockPort}"`,
      "  discogs:",
      `    enabled: ${opts.urlProviders ? "true" : "false"}`,
      ...(opts.urlProviders
        ? [
            `    base_url_override: "http://127.0.0.1:${thisMockPort}"`,
            '    token: "e2e-discogs-token"',
          ]
        : []),
      "  deezer:",
      `    enabled: ${opts.urlProviders ? "true" : "false"}`,
      ...(opts.urlProviders
        ? [`    base_url_override: "http://127.0.0.1:${thisMockPort}"`]
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

  const env = { ...process.env, MUZILLA_CONFIG_DIR: confDir };
  return { scratchRoot, libraryDir, thisMockPort, thisAppPort, env };
}

export const test = base.extend<{ muzilla: MuzillaEnv; urlProviders: boolean }>(
  {
    urlProviders: [false, { option: true }],
    muzilla: async ({ urlProviders }, use) => {
      const { scratchRoot, libraryDir, thisMockPort, thisAppPort, env } =
        buildEnvAndConfig({
          authEnabled: false,
          urlProviders,
        });

      const mockServer: ChildProcess = spawn(
        VENV_PYTHON,
        [
          path.join(REPO_ROOT, "e2e", "mock_provider_server.py"),
          "--port",
          String(thisMockPort),
        ],
        { env, cwd: REPO_ROOT, stdio: "pipe" },
      );

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
      let appServer: ChildProcess = startApp();

      const baseUrl = `http://127.0.0.1:${thisAppPort}`;

      try {
        await waitForHttp(
          `http://127.0.0.1:${thisMockPort}/release?query=test&limit=1&fmt=json`,
          15_000,
        );
        await waitForHttp(`${baseUrl}/api/health`, 30_000);

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
            await stopProcess(appServer, "app server");
            appServer = startApp();
            await waitForHttp(`${baseUrl}/api/health`, 30_000);
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
              "from muzilla.db.models import Track, TrackGroup",
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
              '    source = TrackGroup(key=f"e2e-source:{track.id}", kind="album", grouping_basis="tags", grouping_confidence=0.4, album=track.album, album_artist=track.album_artist, track_count=1)',
              '    target = TrackGroup(key=f"e2e-target:{track.id}", kind="album", grouping_basis="tags", grouping_confidence=1.0, album=track.album, album_artist=track.album_artist, track_count=1)',
              "    session.add_all([source, target])",
              "    session.flush()",
              "    track.group_id = source.id",
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
          stopProcess(appServer, "app server"),
          stopProcess(mockServer, "mock provider server"),
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
    const { scratchRoot, thisMockPort, thisAppPort, env } = buildEnvAndConfig({
      authEnabled: true,
    });

    const mockServer: ChildProcess = spawn(
      VENV_PYTHON,
      [
        path.join(REPO_ROOT, "e2e", "mock_provider_server.py"),
        "--port",
        String(thisMockPort),
      ],
      { env, cwd: REPO_ROOT, stdio: "pipe" },
    );

    const appServer: ChildProcess = spawn(
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

    const baseUrl = `http://127.0.0.1:${thisAppPort}`;

    try {
      await waitForHttp(
        `http://127.0.0.1:${thisMockPort}/release?query=test&limit=1&fmt=json`,
        15_000,
      );
      await waitForHttp(`${baseUrl}/api/health`, 30_000);

      await use({ baseUrl, password: AUTH_PASSWORD });
    } finally {
      await Promise.all([
        stopProcess(appServer, "app server"),
        stopProcess(mockServer, "mock provider server"),
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
    const { scratchRoot, thisMockPort, thisAppPort, env } = buildEnvAndConfig({
      authEnabled: false,
      createLibraryDir: false,
    });

    const mockServer: ChildProcess = spawn(
      VENV_PYTHON,
      [
        path.join(REPO_ROOT, "e2e", "mock_provider_server.py"),
        "--port",
        String(thisMockPort),
      ],
      { env, cwd: REPO_ROOT, stdio: "pipe" },
    );

    const appServer: ChildProcess = spawn(
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

    const baseUrl = `http://127.0.0.1:${thisAppPort}`;

    try {
      await waitForHttp(
        `http://127.0.0.1:${thisMockPort}/release?query=test&limit=1&fmt=json`,
        15_000,
      );
      await waitForHttp(`${baseUrl}/api/health`, 30_000);

      await use({ baseUrl });
    } finally {
      await Promise.all([
        stopProcess(appServer, "app server"),
        stopProcess(mockServer, "mock provider server"),
      ]);
      rmSync(scratchRoot, { recursive: true, force: true });
    }
  },
});

export { expect } from "@playwright/test";
