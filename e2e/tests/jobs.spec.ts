import { test, expect } from "./fixtures";

test("the jobs list renders a finished scan job", async ({ page, muzilla }) => {
  await muzilla.scanOneFile();

  await page.goto(`${muzilla.baseUrl}/activity`);
  // Activity now groups by user action: scan is shown as "Scansione"
  // with human title, outcome and Italian state label. Retention sweep
  // is hidden by default, so only the scan/import rows matter.
  const scanRow = page
    .getByText("Scansione")
    .first()
    .locator("..")
    .locator("..");
  await expect(scanRow).toBeVisible({ timeout: 10_000 });
  await expect(scanRow.getByText("Completata")).toBeVisible();
});

test("expanding a job shows its log events, replayed via SSE", async ({
  page,
  muzilla,
}) => {
  await muzilla.scanOneFile();

  await page.goto(`${muzilla.baseUrl}/activity`);
  const jobRow = page
    .getByText("Scansione")
    .first()
    .locator("..")
    .locator("..");
  await expect(jobRow).toBeVisible({ timeout: 10_000 });

  await jobRow.click();
  // Technical events are now behind "Diagnostica tecnica" details.
  await page.getByText("Diagnostica tecnica").click();
  // scan's handler (jobs/handlers/scan.py) calls progress.log(f"scanning
  // {root}") — GET .../events?after=0 replays every persisted
  // job_events row even for an already-finished job (useJobEvents.ts's
  // comment: "subscribing works for completed jobs too"), so this text
  // should appear even though the job finished before the panel opened.
  await expect(page.getByText(/scanning/)).toBeVisible({ timeout: 10_000 });
});

test("cancelling a job marks it cancelled", async ({ page, muzilla }) => {
  await muzilla.scanOneFile();

  // Keep the real scan handler busy long enough to exercise its file
  // checkpoint, then request cancellation through the Jobs UI.  A tiny
  // single-file fixture could complete before a human-visible cancel can
  // land and would not prove the running-state contract.  The cancel is
  // gated on API-observed mid-scan progress (running with ample remaining
  // files) so the job is provably in flight when the UI cancel lands —
  // a fixed file count alone is timing-dependent on fast machines.
  for (let i = 0; i < 12000; i += 1) {
    muzilla.addFixtureFile(`cancel-${i}.mp3`);
  }
  await page.goto(`${muzilla.baseUrl}/activity`);
  const scanRes = await page.request.post(`${muzilla.baseUrl}/api/scan`, {
    data: { root: muzilla.libraryDir },
  });
  const jobId = (await scanRes.json()).job_id;

  // No reload: the activity list polls every 2s, and a reload would burn
  // scan time while the cancel needs to land mid-flight. Scan progress is
  // indeterminate (total=None), so mid-flight proof is the API-observed
  // `running` state immediately after enqueue.
  {
    const deadline = Date.now() + 30_000;
    let running = false;
    while (Date.now() < deadline) {
      const probe = await (
        await page.request.get(`${muzilla.baseUrl}/api/jobs/${jobId}`)
      ).json();
      if (probe.state === "running") {
        running = true;
        break;
      }
      if (["cancelled", "succeeded", "failed"].includes(probe.state)) break;
      await new Promise((r) => setTimeout(r, 100));
    }
    expect(running).toBe(true);
  }
  const jobRow = page.getByRole("button").filter({ hasText: `#${jobId}` });
  await expect(jobRow).toBeVisible({ timeout: 15_000 });
  await expect(jobRow.getByText("In corso")).toBeVisible({ timeout: 10_000 });
  await jobRow.click();
  await page.getByRole("button", { name: "Annulla" }).click();
  // The "Annullamento in corso…" intermediate renders only while a 2s UI
  // poll observes the server-side `cancelling` state, which a fast file
  // checkpoint can traverse between polls — requiring it here is inherently
  // racy. Its rendering given `cancelling` is covered deterministically by
  // the frontend Activity unit test. Either cancel-visible string proves the
  // UI reflected cancellation; the API + `Annullata` assertions below pin
  // the terminal outcome.
  await expect(page.getByText(/Annullamento in corso…|Annullata/)).toBeVisible({
    timeout: 15_000,
  });

  let finalJob: { state: string } = { state: "pending" };
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    finalJob = await (
      await page.request.get(`${muzilla.baseUrl}/api/jobs/${jobId}`)
    ).json();
    if (["cancelled", "succeeded", "failed"].includes(finalJob.state)) break;
    await new Promise((r) => setTimeout(r, 100));
  }
  expect(finalJob.state).toBe("cancelled");

  await expect(jobRow).toBeVisible({ timeout: 10_000 });
  await expect(jobRow.getByText("Annullata")).toBeVisible();
});

test("enrichment buttons queue a job and show a confirmation toast", async ({
  page,
  muzilla,
}) => {
  await muzilla.scanOneFile();

  await page.goto(`${muzilla.baseUrl}/activity`);
  await page.getByText("Azioni tecniche opzionali").click();
  await expect(page.getByRole("button", { name: "Lyrics" })).toBeVisible({
    timeout: 10_000,
  });

  // Lyrics enrichment only needs lrclib, which the fixture leaves
  // disabled but the handler itself degrades gracefully rather than
  // failing outright — safe to queue without a mock.
  await page.getByRole("button", { name: "Lyrics" }).click();
  await expect(page.getByText(/Lyrics queued \(job #\d+\)/)).toBeVisible({
    timeout: 10_000,
  });
});
