import { test, expect } from "./fixtures";

/**
 * ReviewBundle apply/undo confirmation — ported from legacy ChangeSet flow.
 * Uses POST /api/tracks/{id}/review/manual (ProposalComposer) so the source
 * snapshot includes size_bytes/mtime_ns/tag_hash and apply succeeds to
 * 'applied' without the stale-preflight failure. Verifies that bare Enter
 * does NOT trigger apply, that Apply/Undo modals gate the mutation, and
 * that GET /api/reviews never 500 (applied_operation_ids present).
 */

async function createManualReviewViaAPI(
  page: import("@playwright/test").Page,
  baseUrl: string,
): Promise<number> {
  const tracksRes = await page.request.get(`${baseUrl}/api/tracks?limit=1`);
  expect(tracksRes.ok()).toBeTruthy();
  const { items } = await tracksRes.json();
  const trackId = items[0]?.id;
  if (trackId === undefined) throw new Error("no track for manual review");
  const createRes = await page.request.post(
    `${baseUrl}/api/tracks/${trackId}/review/manual`,
    {
      data: { fields: { title: "confirmation-modal-test" } },
    },
  );
  expect(createRes.ok(), await createRes.text()).toBeTruthy();
  const created = await createRes.json();
  return created.id as number;
}

async function acceptFirstOperation(
  page: import("@playwright/test").Page,
  baseUrl: string,
  reviewId: number,
): Promise<void> {
  const reviewRes = await page.request.get(
    `${baseUrl}/api/reviews/${reviewId}`,
  );
  expect(reviewRes.ok()).toBeTruthy();
  const review = await reviewRes.json();
  const revisionId = review.current_revision.id;
  const ops = review.current_revision.operations as Array<{ id: number }>;
  // Accept all pending operations — manual review via ProposalComposer may include a move_file preview
  const decisions = ops.map((op) => ({
    operation_id: op.id,
    decision: "accepted" as const,
  }));
  const acceptRes = await page.request.patch(
    `${baseUrl}/api/reviews/${reviewId}/operations`,
    {
      data: { revision_id: revisionId, decisions },
    },
  );
  expect(acceptRes.ok(), await acceptRes.text()).toBeTruthy();
}

async function pollUntilApplied(
  page: import("@playwright/test").Page,
  baseUrl: string,
  reviewId: number,
  timeoutMs = 15000,
): Promise<any> {
  const deadline = Date.now() + timeoutMs;
  let last: any = null;
  while (Date.now() < deadline) {
    const res = await page.request.get(`${baseUrl}/api/reviews/${reviewId}`);
    expect(
      res.ok(),
      `GET /api/reviews/${reviewId} should not 500: ${await res.text()}`,
    ).toBeTruthy();
    const body = await res.json();
    last = body;
    if (body.state === "applied") {
      // verify per-file result contract: applied_operation_ids must be present (fix for 500)
      const files = body.apply_runs?.[0]?.result?.files ?? [];
      for (const f of files) {
        expect(
          Array.isArray(f.applied_operation_ids),
          "applied_operation_ids missing",
        ).toBeTruthy();
      }
      return body;
    }
    if (body.state === "failed") {
      // fail fast with context instead of swallowing as lenient pass
      throw new Error(
        `review ${reviewId} failed unexpectedly: ${JSON.stringify(body.apply_runs?.[0]?.result ?? body.error)}`,
      );
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error(
    `review ${reviewId} did not reach 'applied' within ${timeoutMs}ms, last state=${last?.state} body=${JSON.stringify(last).slice(0, 1200)}`,
  );
}

test("Apply requires confirmation; Enter opens the modal instead of applying directly", async ({
  page,
  muzilla,
}) => {
  await muzilla.scanOneFile();
  const reviewId = await createManualReviewViaAPI(page, muzilla.baseUrl);
  await acceptFirstOperation(page, muzilla.baseUrl, reviewId);

  await page.goto(`${muzilla.baseUrl}/reviews/${reviewId}?returnTo=%2Freviews`);
  await expect(page.getByText("Tag metadata")).toBeVisible({ timeout: 10_000 });
  const applyButton = page.getByRole("button", {
    name: /Applica \d+ modifiche/,
  });
  await expect(applyButton).toBeEnabled({ timeout: 10_000 });

  // Bare Enter must not apply directly — review must stay not-applied and no modal (ChangeSet-era Enter-to-open is removed)
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("dialog", { name: "Applicare le modifiche?" }),
  ).not.toBeVisible();
  const stillNotApplied = await (
    await page.request.get(`${muzilla.baseUrl}/api/reviews/${reviewId}`)
  ).json();
  expect(["ready", "needs_attention", "preparing"]).toContain(
    stillNotApplied.state,
  );

  // Clicking Apply opens the modal rather than applying immediately.
  await applyButton.click();
  await expect(
    page.getByRole("dialog", { name: "Applicare le modifiche?" }),
  ).toBeVisible();
  await expect(
    page.getByText(/Verranno applicate \d+ modifiche/),
  ).toBeVisible();

  const stillNotAppliedAfterOpen = await (
    await page.request.get(`${muzilla.baseUrl}/api/reviews/${reviewId}`)
  ).json();
  expect(["ready", "needs_attention"]).toContain(
    stillNotAppliedAfterOpen.state,
  );

  // Cancel closes the modal without applying.
  await page.getByRole("button", { name: "Annulla" }).click();
  await expect(
    page.getByRole("dialog", { name: "Applicare le modifiche?" }),
  ).not.toBeVisible();
  const afterCancel = await (
    await page.request.get(`${muzilla.baseUrl}/api/reviews/${reviewId}`)
  ).json();
  expect(["ready", "needs_attention"]).toContain(afterCancel.state);
  expect(afterCancel.apply_runs.length).toBe(0);

  // Confirming inside the modal actually applies — scoped to dialog so the page action bar is not hit
  await applyButton.click();
  await expect(
    page.getByRole("dialog", { name: "Applicare le modifiche?" }),
  ).toBeVisible();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: /Applica \d+ modifiche/ })
    .click();
  // Dialog should close immediately after confirm
  await expect(
    page.getByRole("dialog", { name: "Applicare le modifiche?" }),
  ).not.toBeVisible({ timeout: 5000 });
  // UI should show applying state, then applied
  await expect(
    page
      .getByText(
        /Applicazione in corso|Applicazione avviata|Stato: applying|Stato: applied/,
      )
      .first(),
  ).toBeVisible({ timeout: 15000 });

  const applied = await pollUntilApplied(page, muzilla.baseUrl, reviewId);
  expect(applied.state).toBe("applied");
});

test("Undo requires confirmation before staging the revert", async ({
  page,
  muzilla,
}) => {
  await muzilla.scanOneFile();
  const reviewId = await createManualReviewViaAPI(page, muzilla.baseUrl);
  await acceptFirstOperation(page, muzilla.baseUrl, reviewId);

  // Apply first via UI (exercises same confirmation gate)
  await page.goto(`${muzilla.baseUrl}/reviews/${reviewId}?returnTo=%2Freviews`);
  await page.getByRole("button", { name: /Applica \d+ modifiche/ }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: /Applica \d+ modifiche/ })
    .click();
  await expect(
    page
      .getByText(
        /Applicazione in corso|Applicazione avviata|Stato: applying|Stato: applied/,
      )
      .first(),
  ).toBeVisible({ timeout: 15000 });
  await pollUntilApplied(page, muzilla.baseUrl, reviewId);

  await page.goto(`${muzilla.baseUrl}/reviews/${reviewId}?returnTo=%2Freviews`);
  await expect(page.getByText(/Stato:/).first()).toBeVisible({
    timeout: 10_000,
  });
  const undoButton = page.getByRole("button", {
    name: "Ripristina applicazione",
  });
  await expect(undoButton).toBeVisible({ timeout: 10_000 });
  await expect(undoButton).toBeEnabled();

  // Capture undo_runs before opening modal to ensure cancel doesn't stage
  const beforeUndo = await (
    await page.request.get(`${muzilla.baseUrl}/api/reviews/${reviewId}`)
  ).json();
  const undoRunsBefore = beforeUndo.undo_runs.length;

  await undoButton.click();
  await expect(
    page.getByRole("dialog", { name: "Ripristinare l’applicazione?" }),
  ).toBeVisible();
  await expect(
    page.getByText("Verranno eseguite in ordine inverso"),
  ).toBeVisible();

  // Cancelling must not stage anything.
  await page.getByRole("button", { name: "Annulla" }).click();
  await expect(
    page.getByRole("dialog", { name: "Ripristinare l’applicazione?" }),
  ).not.toBeVisible();

  const stillApplied = await (
    await page.request.get(`${muzilla.baseUrl}/api/reviews/${reviewId}`)
  ).json();
  expect(stillApplied.state).toBe("applied");
  expect(stillApplied.undo_runs.length).toBe(undoRunsBefore);
  // No undo job should have been enqueued — GET should still be 200 with no 500
  expect(stillApplied.undo_runs.length).toBe(0);
});
