import { expect, test } from "./fixtures";
import type { Page } from "@playwright/test";
import type { MuzillaEnv } from "./fixtures";

async function seedReview(page: Page, muzilla: MuzillaEnv) {
  await muzilla.scanOneFile();
  const id = await muzilla.createManualReview();
  const response = await page.request.get(`${muzilla.baseUrl}/api/reviews/${id}`);
  expect(response.ok()).toBeTruthy();
  return { id, review: await response.json() };
}

async function serveReviewSequence(
  page: Page,
  id: number,
  responses: unknown[],
): Promise<() => number> {
  let reads = 0;
  await page.route(
    (url) => url.pathname === `/api/reviews/${id}`,
    async (route) => {
      if (route.request().method() !== "GET") {
        await route.continue();
        return;
      }
      const response = responses[Math.min(reads, responses.length - 1)];
      reads++;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(response),
      });
    },
  );
  return () => reads;
}

async function expectPollingStops(page: Page, reads: () => number) {
  const completedReads = reads();
  expect(completedReads).toBeGreaterThanOrEqual(2);
  await page.waitForTimeout(700);
  expect(reads()).toBe(completedReads);
}

test("review detail refreshes while preparing, then stops when ready", async ({
  page,
  muzilla,
}) => {
  const { id, review } = await seedReview(page, muzilla);
  const reads = await serveReviewSequence(page, id, [
    { ...review, state: "preparing" },
    { ...review, state: "ready" },
  ]);

  await page.goto(`${muzilla.baseUrl}/reviews/${id}?returnTo=%2Freviews`);
  await expect(page.getByText(/Stato: preparing/)).toBeVisible();
  await expect(page.getByText(/Stato: ready/)).toBeVisible({ timeout: 5_000 });
  await expectPollingStops(page, reads);
});

test("review detail follows a running enrichment retry and stops on completion", async ({
  page,
  muzilla,
}) => {
  const { id, review } = await seedReview(page, muzilla);
  const attempt = (attemptId: number, state: string, attemptNo: number) => ({
    id: attemptId,
    kind: "lyrics",
    item_key: `track:${review.scope_id}`,
    state,
    attempt_no: attemptNo,
    job_id: null,
    result: null,
    error: null,
  });
  const previousFailure = attempt(901, "transient_failure", 1);
  const retry = attempt(902, "running", 2);
  const reads = await serveReviewSequence(page, id, [
    { ...review, task_attempts: [previousFailure, retry] },
    {
      ...review,
      task_attempts: [previousFailure, { ...retry, state: "succeeded" }],
    },
  ]);

  await page.goto(`${muzilla.baseUrl}/reviews/${id}?returnTo=%2Freviews`);
  await expect(page.getByText("In corso", { exact: true })).toBeVisible();
  await expect(page.getByText("Pronto")).toBeVisible({ timeout: 5_000 });
  await expectPollingStops(page, reads);
});

test("review detail follows an active Undo and stops when it finishes", async ({
  page,
  muzilla,
}) => {
  const { id, review } = await seedReview(page, muzilla);
  const runId = 903;
  const trackId = review.scope_id as number;
  const applyRun = {
    id: runId,
    revision_id: review.current_revision.id,
    state: "applied",
    result: {
      state: "applied",
      atomicity: "review_bundle",
      files: [
        {
          track_id: trackId,
          state: "applied",
          applied_operation_ids: [],
          error: null,
        },
      ],
      recovery_required: false,
    },
    error: null,
    operation_attempts: [],
    undo_expired: false,
    undo_expiry_reason: null,
  };
  const undoRun = {
    id: 904,
    source_apply_run_id: runId,
    state: "undoing",
    result: null,
    error: null,
    job_ids: [],
  };
  const completedUndo = {
    ...undoRun,
    state: "undone",
    result: {
      state: "undone",
      atomicity: "review_bundle",
      files: [
        {
          track_id: trackId,
          state: "undone",
          source_change_set_ids: [],
          error: null,
          retryable: false,
        },
      ],
      recovery_required: false,
    },
  };
  const reads = await serveReviewSequence(page, id, [
    { ...review, state: "applied", apply_runs: [applyRun], undo_runs: [undoRun] },
    {
      ...review,
      state: "applied",
      apply_runs: [applyRun],
      undo_runs: [completedUndo],
    },
  ]);

  await page.goto(`${muzilla.baseUrl}/reviews/${id}?returnTo=%2Freviews`);
  await expect(page.getByText("Ripristino in corso…")).toBeVisible();
  await expect(page.getByText("undone", { exact: true })).toBeVisible({ timeout: 5_000 });
  await expect(page.getByText("Ripristino in corso…")).not.toBeVisible();
  await expectPollingStops(page, reads);
});
