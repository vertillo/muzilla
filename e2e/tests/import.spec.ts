import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test, expect, noLibraryTest } from "./fixtures";

// Import tests drive a full scratch app + worker per test; the cancellation
// case copies 80 files and must reliably reach a running ImportTask checkpoint
// before cancelling, so it needs headroom above the global 30s timeout when
// the host is under full-suite load.
test.setTimeout(60_000);

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE_AUDIO = path.resolve(
  __dirname,
  "..",
  "..",
  "tests",
  "fixtures",
  "audio",
  "silence.mp3",
);

test("wizard shows the configured library root read-only and starts an import", async ({
  page,
  muzilla,
}) => {
  // Unlike muzilla.scanOneFile() (which drives POST /api/scan directly),
  // the import wizard walks scan -> fingerprint -> group -> match as one
  // orchestrated job (jobs/handlers/import_session.py), so the fixture
  // file needs to already be on disk before the wizard starts it.
  fs.copyFileSync(FIXTURE_AUDIO, path.join(muzilla.libraryDir, "silence.mp3"));

  await page.goto(`${muzilla.baseUrl}/import`, {
    waitUntil: "domcontentloaded",
  });
  await expect(
    page.getByRole("heading", { name: "Import a library" }),
  ).toBeVisible({ timeout: 15_000 });

  // The free-text path input was replaced with a read-only display of the
  // configured
  // storage.library_root (the fixture sets that to muzilla.libraryDir)
  // -- there is no longer a text box to type a path into at all.
  await expect(page.getByText(muzilla.libraryDir).first()).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByPlaceholder("/music")).toHaveCount(0);

  await page.getByRole("button", { name: "Start import" }).click();

  await expect(page).toHaveURL(/\/import\/\d+$/, { timeout: 10_000 });

  await expect(page.getByText("Scan", { exact: true })).toBeVisible({
    timeout: 10_000,
  });
  await expect(page.getByText("Match", { exact: true })).toBeVisible();

  // Wait for the import to leave its running states. The mock includes only
  // unrelated candidates for this local file, so Matching v2 must reject them
  // instead of staging an unrelated proposal.
  await expect(page.getByText(/^(reviewing|completed)$/)).toBeVisible({
    timeout: 20_000,
  });

  // A rejected automatic candidate is still a stable review: it exposes the
  // failure state and makes manual search available instead of disappearing.
  await expect(page.getByText(/Revisioni \(1\)/)).toBeVisible({
    timeout: 10_000,
  });
  await expect(
    page.getByText("Revisione pronta o in preparazione"),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Apri" })).toBeVisible();
});

noLibraryTest(
  "Start import is disabled and a clear message shows when the library root does not exist",
  async ({ page, muzillaNoLibrary }) => {
    // Show a clear message when the library root is unavailable. library_root
    // always has a configured value (defaults to
    // /music), so "unset" in practice means the directory doesn't exist
    // on disk yet. This fixture points storage.library_root at a path
    // that was never created.
    await page.goto(`${muzillaNoLibrary.baseUrl}/import`);
    await expect(
      page.getByRole("heading", { name: "Import a library" }),
    ).toBeVisible({ timeout: 10_000 });

    await expect(
      page.getByText("This path does not exist on disk."),
    ).toBeVisible({ timeout: 10_000 });
    await expect(
      page.getByRole("button", { name: "Start import" }),
    ).toBeDisabled();
  },
);

test("scoped import: browse server-side, preview counts, and import a subdirectory", async ({
  page,
  muzilla,
}) => {
  // Build a tiny scoped library: two subdirs, one with supported files, one with ignored/unsupported.
  const subA = path.join(muzilla.libraryDir, "subA");
  const subB = path.join(muzilla.libraryDir, "subB");
  fs.mkdirSync(subA, { recursive: true });
  fs.mkdirSync(subB, { recursive: true });
  fs.copyFileSync(FIXTURE_AUDIO, path.join(subA, "a1.mp3"));
  fs.copyFileSync(FIXTURE_AUDIO, path.join(subA, "a2.flac"));
  fs.copyFileSync(FIXTURE_AUDIO, path.join(subB, "b1.mp3"));
  fs.writeFileSync(
    path.join(muzilla.libraryDir, "cover.jpg"),
    Buffer.from([0xff, 0xd8, 0xff]),
  );
  fs.writeFileSync(path.join(muzilla.libraryDir, "notes.txt"), "hello");
  fs.mkdirSync(path.join(muzilla.libraryDir, ".git"));

  await page.goto(`${muzilla.baseUrl}/import`);
  await expect(
    page.getByRole("heading", { name: "Import a library" }),
  ).toBeVisible({ timeout: 10_000 });

  // Browse root: server-side entries, no client fiction.
  await expect(page.getByText("Sfoglia la libreria")).toBeVisible({
    timeout: 10_000,
  });
  await expect(page.getByText("subA")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("subB")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("cover.jpg")).toBeVisible();
  await expect(page.getByText("(ignorato)").first()).toBeVisible();
  await expect(page.getByText(".git").first()).toBeVisible();

  // Preview for whole library before selection should show supported files.
  await expect(page.getByText(/supportati:/).first()).toBeVisible({
    timeout: 10_000,
  });

  // Select subA via its Seleziona button (scoped file/directory selection).
  const subARow = page.locator("li", { hasText: "subA" });
  await subARow.getByRole("button", { name: "Seleziona" }).click();

  // Preview updates to subA scope: 2 supported, plus honest unsupported/sidecar feedback for whole library not in scope.
  await expect(page.getByText(/Cartella.*supportati: 2/)).toBeVisible({
    timeout: 10_000,
  });
  await expect(page.getByText(muzilla.libraryDir + "/subA")).toBeVisible();

  // Cost/policy preview must be visible (effective enrichment/rename policy + honest cost, no pre-Apply writes).
  await expect(
    page.getByText("Policy effettiva per nuovo import"),
  ).toBeVisible();
  await expect(
    page.getByText(/nessuna scrittura su disco prima di Apply/).first(),
  ).toBeVisible();

  // Start scoped import.
  await page.getByRole("button", { name: "Start import" }).click();
  await expect(page).toHaveURL(/\/import\/\d+$/, { timeout: 10_000 });
  await expect(page.getByText("Scan", { exact: true })).toBeVisible({
    timeout: 10_000,
  });
  await expect(page.getByText(/^(reviewing|completed)$/)).toBeVisible({
    timeout: 20_000,
  });

  // Verify outcome: only subA files were indexed via scoped scan (b1.mp3 outside scope not imported).
  const tracksRes = await page.request.get(
    `${muzilla.baseUrl}/api/tracks?limit=100`,
  );
  expect(tracksRes.ok()).toBeTruthy();
  const tracks = await tracksRes.json();
  const paths: string[] = tracks.items.map((t: { path: string }) => t.path);
  // At least the two subA files must be present.
  expect(paths.some((p) => p.includes("/subA/a1.mp3"))).toBeTruthy();
  expect(paths.some((p) => p.includes("/subA/a2.flac"))).toBeTruthy();
  // The out-of-scope file must NOT have been indexed by this scoped import.
  expect(paths.some((p) => p.includes("/subB/b1.mp3"))).toBeFalsy();
});

test("cancellation is cooperative and explains partial outcome", async ({
  page,
  muzilla,
}) => {
  const TOTAL = 120;
  for (let i = 0; i < TOTAL; i++) {
    fs.copyFileSync(
      FIXTURE_AUDIO,
      path.join(muzilla.libraryDir, `canc-${i}.mp3`),
    );
  }

  await page.goto(`${muzilla.baseUrl}/import`);
  await expect(
    page.getByRole("heading", { name: "Import a library" }),
  ).toBeVisible({ timeout: 10_000 });
  await page.getByRole("button", { name: "Start import" }).click();
  await expect(page).toHaveURL(/\/import\/\d+$/, { timeout: 10_000 });
  const urlMatch = page.url().match(/\/import\/(\d+)$/);
  expect(urlMatch).not.toBeNull();
  const importId = urlMatch![1];

  await expect(page.getByText("Scan", { exact: true })).toBeVisible({
    timeout: 10_000,
  });
  await expect(
    page.getByRole("button", { name: "Annulla import" }),
  ).toBeVisible({ timeout: 10_000 });

  const sessionRes0 = await page.request.get(
    `${muzilla.baseUrl}/api/imports/${importId}`,
  );
  expect(sessionRes0.ok()).toBeTruthy();
  const session0 = await sessionRes0.json();
  const jobId = session0.job_id;
  expect(jobId).toBeTruthy();

  // Deterministic checkpoint: poll until an ImportTask is actually
  // state=running (not merely job running) before cancelling. This
  // avoids the race where a fast small library finishes before the
  // cancel request and makes the partial/catalog assertions meaningful.
  // Under full-suite load the worker lease may take longer than one
  // tick, so give a generous window (15s) without masking a real stall.
  let checkpointStage: string | null = null;
  for (let i = 0; i < 150; i++) {
    const sessRes = await page.request.get(
      `${muzilla.baseUrl}/api/imports/${importId}`,
    );
    expect(sessRes.ok()).toBeTruthy();
    const sess = await sessRes.json();
    const runningTask = (
      sess.tasks as Array<{ stage: string; state: string }>
    ).find((t) => t.state === "running");
    if (runningTask) {
      checkpointStage = runningTask.stage;
      break;
    }
    // If the session already left running (failed/cancelled/completed)
    // without ever hitting running, fail fast with context instead of
    // hanging the full 15s and then issuing a meaningless cancel.
    if (
      sess.state !== "scanning" &&
      sess.state !== "fingerprinting" &&
      sess.state !== "grouping" &&
      sess.state !== "matching" &&
      sess.state !== "pending"
    ) {
      checkpointStage = null;
      break;
    }
    await new Promise((res) => setTimeout(res, 100));
  }
  expect(
    checkpointStage,
    "should reach ImportTask running before cancel",
  ).not.toBeNull();

  // Single-request cancel: must be acknowledged as cancelling/cancelled.
  // No test-level retry — server is responsible for transient DB contention
  // (SQLite busy_timeout=10s covers writer contention; any remaining
  // OperationalError is retried server-side). A failure here is a real bug.
  const cancelRes = await page.request.post(
    `${muzilla.baseUrl}/api/jobs/${jobId}/cancel`,
  );
  const cancelText = await cancelRes.text();
  let cancelBody: any = {};
  try {
    cancelBody = cancelText ? JSON.parse(cancelText) : {};
  } catch {
    cancelBody = {};
  }
  expect(
    cancelRes.ok(),
    `cancel POST failed: ${cancelRes.status()} ${cancelText.slice(0, 800)}`,
  ).toBeTruthy();
  expect(["cancelled", "cancelling"].includes(cancelBody.state)).toBeTruthy();

  // Poll until terminal `cancelled` for both session and job.
  // Under full-suite load with 80-file scan, cancellation propagation
  // through scan batch commits can take longer than 12s.
  let finalState: string | null = null;
  let finalJobBody: any = null;
  for (let i = 0; i < 60; i++) {
    const [sessR, jobR] = await Promise.all([
      page.request.get(`${muzilla.baseUrl}/api/imports/${importId}`),
      page.request.get(`${muzilla.baseUrl}/api/jobs/${jobId}`),
    ]);
    expect(sessR.ok()).toBeTruthy();
    expect(jobR.ok()).toBeTruthy();
    const s = await sessR.json();
    const j = await jobR.json();
    finalState = s.state;
    finalJobBody = j;
    if (s.state === "cancelled" && j.state === "cancelled") break;
    await new Promise((res) => setTimeout(res, 300));
  }
  expect(finalState).toBe("cancelled");
  expect(finalJobBody.state).toBe("cancelled");
  // Job.result must be partial and disclose the concrete cancelled stage.
  expect(finalJobBody.result).toBeTruthy();
  expect(finalJobBody.result.partial).toBe(true);
  expect(typeof finalJobBody.result.cancelled_stage).toBe("string");
  expect(finalJobBody.result.cancelled_stage.length).toBeGreaterThan(0);

  await page.reload();
  await expect(page.getByText(/Import annullato/)).toBeVisible({
    timeout: 10_000,
  });
  await expect(
    page.getByText(/i file già indicizzati restano validi/),
  ).toBeVisible();
  await expect(
    page.getByText(/nessuna proposta è stata pubblicata dopo il checkpoint/),
  ).toBeVisible();

  const afterTracks = await page.request.get(
    `${muzilla.baseUrl}/api/tracks?limit=100`,
  );
  expect(afterTracks.ok()).toBeTruthy();
  const afterBody = await afterTracks.json();
  // Non-tautological: partial outcome must retain at least one indexed file
  // but cancellation prevents a fully completed import from publishing proposals.
  expect(afterBody.items.length).toBeGreaterThan(0);
  expect(afterBody.items.length).toBeLessThanOrEqual(TOTAL);
  const sessionFinal = await page.request.get(
    `${muzilla.baseUrl}/api/imports/${importId}`,
  );
  const sessionFinalBody = await sessionFinal.json();
  const taskStates = (
    sessionFinalBody.tasks as Array<{ stage: string; state: string }>
  ).map((t) => t.state);
  const cancelledStage = finalJobBody.result.cancelled_stage as string;
  const cancelledTask = (
    sessionFinalBody.tasks as Array<{ stage: string; state: string }>
  ).find((t) => t.stage === cancelledStage);
  expect(cancelledTask).toBeTruthy();
  expect(cancelledTask!.state).toBe("cancelled");
  expect(taskStates.includes("cancelled")).toBeTruthy();
  // No proposal must be visible after a cancelled checkpoint.
  const reviewsRes = await page.request.get(
    `${muzilla.baseUrl}/api/reviews?limit=10`,
  );
  expect(reviewsRes.ok()).toBeTruthy();
  const reviews = await reviewsRes.json();
  const items = (reviews.items ?? reviews) as Array<unknown>;
  expect(items.length).toBe(0);
});
