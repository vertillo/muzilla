import fs from "node:fs";
import { test, expect } from "./fixtures";

test("import -> ReviewBundle -> apply -> restart -> persistent undo restores file and catalog", async ({
  page,
  muzilla,
}) => {
  test.setTimeout(60_000);
  muzilla.addMatchingFixtureFile();
  const started = await page.request.post(`${muzilla.baseUrl}/api/imports`, {
    data: { library_root: muzilla.libraryDir },
  });
  expect(started.status()).toBe(202);
  const importSession = await pollImport(
    page,
    muzilla.baseUrl,
    (await started.json()).id,
  );
  expect(importSession.state).toMatch(/reviewing|completed/);
  expect(importSession.review_bundle_ids).toHaveLength(1);
  const reviewId = importSession.review_bundle_ids[0];
  const review = await pollReviewReady(page, muzilla.baseUrl, reviewId);
  const originalPath = review.source_items[0].path as string;
  expect(review.current_revision.candidate_snapshot.title).toBe("E2E Track");
  expect(review.current_revision.confidence).toBeGreaterThanOrEqual(0.85);
  expect(review.current_revision.operations.length).toBeGreaterThan(0);

  const accepted = await page.request.patch(
    `${muzilla.baseUrl}/api/reviews/${reviewId}/operations`,
    {
      data: {
        revision_id: review.current_revision.id,
        decisions: review.current_revision.operations.map(
          (operation: { id: number }) => ({
            operation_id: operation.id,
            decision: "accepted",
          }),
        ),
      },
    },
  );
  expect(accepted.ok(), await accepted.text()).toBeTruthy();

  await page.goto(`${muzilla.baseUrl}/reviews/${reviewId}?returnTo=%2Freviews`);
  const applyButton = page.getByRole("button", {
    name: /Applica \d+ modifiche/,
  });
  await expect(applyButton).toBeEnabled({ timeout: 10_000 });
  await applyButton.click();
  await expect(
    page.getByRole("dialog", { name: "Applicare le modifiche?" }),
  ).toBeVisible();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: /Applica \d+ modifiche/ })
    .click();
  await expect(
    page.getByRole("dialog", { name: "Applicare le modifiche?" }),
  ).not.toBeVisible({ timeout: 5000 });
  await expect(
    page
      .getByText(
        /Applicazione in corso|Applicazione avviata|Stato: applying|Stato: applied/,
      )
      .first(),
  ).toBeVisible({ timeout: 15000 });

  const trackId = (await pollReviewReady(page, muzilla.baseUrl, reviewId))
    .source_items[0].source_id as number;
  const afterApply = await pollUntilApplied(
    page,
    muzilla.baseUrl,
    reviewId,
    trackId,
    originalPath,
  );
  expect(afterApply.year).toBe(2026);
  expect(afterApply.path).not.toBe(originalPath);
  expect(fs.existsSync(afterApply.path)).toBeTruthy();
  // Strict ReviewBundle contract: GET must be 200 and result must contain applied_operation_ids
  const appliedReview = await (
    await page.request.get(`${muzilla.baseUrl}/api/reviews/${reviewId}`)
  ).json();
  expect(appliedReview.state).toBe("applied");
  const appliedFiles = appliedReview.apply_runs.at(-1)?.result?.files ?? [];
  expect(appliedFiles.length).toBeGreaterThan(0);
  for (const f of appliedFiles) {
    expect(f.state).toBe("applied");
    expect(Array.isArray(f.applied_operation_ids)).toBeTruthy();
    expect(f.applied_operation_ids.length).toBeGreaterThan(0);
  }

  await muzilla.restartApp();
  await page.goto(`${muzilla.baseUrl}/reviews/${reviewId}?returnTo=%2Freviews`);
  await expect(
    page.getByRole("button", { name: "Ripristina applicazione" }),
  ).toBeVisible({ timeout: 10_000 });
  await page.getByRole("button", { name: "Ripristina applicazione" }).click();
  await expect(
    page.getByRole("dialog", { name: "Ripristinare l’applicazione?" }),
  ).toBeVisible();
  await expect(
    page.getByText("Verranno eseguite in ordine inverso"),
  ).toBeVisible();
  const undoResponsePromise = page.waitForResponse((response) =>
    response.url().endsWith(`/api/reviews/${reviewId}/undo`),
  );
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Ripristina file" })
    .click();
  const undoResponse = await undoResponsePromise;
  expect(undoResponse.status(), await undoResponse.text()).toBe(202);
  await expect(
    page.getByRole("dialog", { name: "Ripristinare l’applicazione?" }),
  ).not.toBeVisible({ timeout: 5000 });

  const undone = await pollUntilUndone(
    page,
    muzilla.baseUrl,
    reviewId,
    trackId,
    originalPath,
  );
  expect(undone).toBeTruthy();
  const undoneReview = await (
    await page.request.get(`${muzilla.baseUrl}/api/reviews/${reviewId}`)
  ).json();
  const lastUndo = undoneReview.undo_runs.at(-1);
  // P1: strict undone only - partially_undone is fail-closed recovery, not success
  expect(lastUndo.state).toBe("undone");
  expect(lastUndo.result?.recovery_required).toBeFalsy();
  for (const f of lastUndo.result?.files ?? []) {
    expect(Array.isArray(f.source_change_set_ids)).toBeTruthy();
    expect(typeof f.retryable).toBe("boolean");
  }

  const restored = await (
    await page.request.get(`${muzilla.baseUrl}/api/tracks/${trackId}`)
  ).json();
  expect(restored.year).toBe(1999);
  expect(restored.path).toBe(originalPath);
  expect(fs.existsSync(originalPath)).toBeTruthy();
  expect(fs.existsSync(afterApply.path)).toBeFalsy();
});

async function pollImport(
  page: import("@playwright/test").Page,
  baseUrl: string,
  id: number,
): Promise<any> {
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    const res = await page.request.get(`${baseUrl}/api/imports/${id}`);
    expect(
      res.ok(),
      `GET /api/imports/${id} should not 500: ${await res.text()}`,
    ).toBeTruthy();
    const session = await res.json();
    if (
      ["reviewing", "completed", "failed", "cancelled"].includes(session.state)
    )
      return session;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`import ${id} did not finish`);
}

async function pollReviewReady(
  page: import("@playwright/test").Page,
  baseUrl: string,
  id: number,
): Promise<any> {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    const res = await page.request.get(`${baseUrl}/api/reviews/${id}`);
    expect(
      res.ok(),
      `GET /api/reviews/${id} should not 500: ${await res.text()}`,
    ).toBeTruthy();
    const review = await res.json();
    if (
      review.current_revision &&
      review.current_revision.operations.length > 0
    )
      return review;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`review ${id} did not become ready`);
}

async function pollUntilApplied(
  page: import("@playwright/test").Page,
  baseUrl: string,
  reviewId: number,
  trackId: number,
  originalPath: string,
  timeoutMs = 20_000,
): Promise<any> {
  const deadline = Date.now() + timeoutMs;
  let lastTrack: any = null;
  let lastReview: any = null;
  while (Date.now() < deadline) {
    const trRes = await page.request.get(`${baseUrl}/api/tracks/${trackId}`);
    expect(
      trRes.ok(),
      `GET /api/tracks/${trackId} should not 500: ${await trRes.text()}`,
    ).toBeTruthy();
    const tr = await trRes.json();
    lastTrack = tr;
    const revRes = await page.request.get(`${baseUrl}/api/reviews/${reviewId}`);
    expect(
      revRes.ok(),
      `GET /api/reviews/${reviewId} should not 500: ${await revRes.text()}`,
    ).toBeTruthy();
    const rev = await revRes.json();
    lastReview = rev;
    const appliedFiles = rev.apply_runs.at(-1)?.result?.files ?? [];
    const isAppliedState =
      rev.state === "applied" &&
      appliedFiles.some((f: any) => f.state === "applied");
    const isFileMoved =
      tr.year === 2026 && tr.path !== originalPath && fs.existsSync(tr.path);
    if (isAppliedState && isFileMoved) {
      for (const f of appliedFiles)
        expect(Array.isArray(f.applied_operation_ids)).toBeTruthy();
      return tr;
    }
    if (rev.state === "failed" || rev.apply_runs.at(-1)?.state === "failed") {
      throw new Error(
        `review ${reviewId} apply failed: ${JSON.stringify(rev.apply_runs.at(-1)?.result ?? rev.error)}`,
      );
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(
    `review ${reviewId} did not reach applied+file-moved within ${timeoutMs}ms, lastTrack=${JSON.stringify(lastTrack).slice(0, 800)} lastReview=${JSON.stringify(lastReview).slice(0, 1200)}`,
  );
}

async function pollUntilUndone(
  page: import("@playwright/test").Page,
  baseUrl: string,
  reviewId: number,
  trackId: number,
  originalPath: string,
  timeoutMs = 20_000,
): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  let lastReview: any = null;
  let lastTrack: any = null;
  while (Date.now() < deadline) {
    const revRes = await page.request.get(`${baseUrl}/api/reviews/${reviewId}`);
    expect(
      revRes.ok(),
      `GET /api/reviews/${reviewId} should not 500: ${await revRes.text()}`,
    ).toBeTruthy();
    const rev = await revRes.json();
    lastReview = rev;
    const trRes = await page.request.get(`${baseUrl}/api/tracks/${trackId}`);
    expect(
      trRes.ok(),
      `GET /api/tracks/${trackId} should not 500: ${await trRes.text()}`,
    ).toBeTruthy();
    const tr = await trRes.json();
    lastTrack = tr;
    const u = rev.undo_runs.at(-1);
    // P1: strict undone only; partially_undone is explicit fail-closed recovery state, not success
    const isUndone = u && u.state === "undone";
    const isPartiallyUndone = u && u.state === "partially_undone";
    const isFileRestored =
      tr.year === 1999 &&
      tr.path === originalPath &&
      fs.existsSync(originalPath);
    if (isUndone && isFileRestored) {
      expect(u.result?.recovery_required).toBeFalsy();
      for (const f of u.result?.files ?? []) {
        expect(Array.isArray(f.source_change_set_ids)).toBeTruthy();
        expect(typeof f.retryable).toBe("boolean");
        expect(f.state).toBe("undone");
      }
      return true;
    }
    if (isPartiallyUndone) {
      throw new Error(
        `undo recovery_required: partially_undone is not success: ${JSON.stringify(u.result ?? u.error)}`,
      );
    }
    if (u && u.state === "failed")
      throw new Error(`undo failed: ${JSON.stringify(u.result ?? u.error)}`);
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(
    `review ${reviewId} undone+restored not reached within ${timeoutMs}ms, lastReview=${JSON.stringify(lastReview).slice(0, 1200)} lastTrack=${JSON.stringify(lastTrack).slice(0, 800)}`,
  );
}
