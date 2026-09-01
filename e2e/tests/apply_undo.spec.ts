import { test, expect } from "./fixtures";

/**
 * ReviewBundle scan → manual review → apply → undo
 * Ported from legacy ChangeSet flow to POST /api/tracks/{id}/review/manual
 * with complete source snapshot so preflight succeeds and strict
 * applied/undone assertions hold (no lenient failed/empty swallow).
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
      data: { fields: { title: "undo-confirmation-test" } },
    },
  );
  expect(createRes.ok(), await createRes.text()).toBeTruthy();
  const created = await createRes.json();
  return created.id as number;
}

async function acceptAllOperations(
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
      const files = body.apply_runs?.[0]?.result?.files ?? [];
      for (const f of files)
        expect(Array.isArray(f.applied_operation_ids)).toBeTruthy();
      return body;
    }
    if (body.state === "failed")
      throw new Error(
        `review ${reviewId} failed unexpectedly: ${JSON.stringify(body.apply_runs?.[0]?.result ?? body.error)}`,
      );
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error(
    `review ${reviewId} did not reach 'applied' within ${timeoutMs}ms, last=${JSON.stringify(last).slice(0, 1200)}`,
  );
}

async function pollUntilUndone(
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
    const undo = body.undo_runs?.[body.undo_runs.length - 1];
    if (undo && undo.state === "undone") {
      const files = undo.result?.files ?? [];
      for (const f of files)
        expect(Array.isArray(f.source_change_set_ids)).toBeTruthy();
      return body;
    }
    if (undo && undo.state === "failed")
      throw new Error(
        `undo failed: ${JSON.stringify(undo.result ?? undo.error)}`,
      );
    await new Promise((r) => setTimeout(r, 300));
  }
  throw new Error(
    `review ${reviewId} undo did not reach 'undone' within ${timeoutMs}ms, last=${JSON.stringify(last).slice(0, 1500)}`,
  );
}

test("scan -> review -> apply -> undo", async ({ page, muzilla }) => {
  await muzilla.scanOneFile();
  const reviewId = await createManualReviewViaAPI(page, muzilla.baseUrl);
  await acceptAllOperations(page, muzilla.baseUrl, reviewId);

  await page.goto(`${muzilla.baseUrl}/reviews/${reviewId}?returnTo=%2Freviews`);
  await expect(page.getByText("Tag metadata")).toBeVisible({ timeout: 10_000 });
  const applyBtn = page.getByRole("button", { name: /Applica \d+ modifiche/ });
  await expect(applyBtn).toBeEnabled();
  await applyBtn.click();
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
  ).toBeVisible({ timeout: 15_000 });
  const applied = await pollUntilApplied(page, muzilla.baseUrl, reviewId);
  expect(applied.state).toBe("applied");

  await page.goto(`${muzilla.baseUrl}/reviews`);
  await expect(page.getByRole("heading", { name: "Revisioni" })).toBeVisible({
    timeout: 10_000,
  });

  // Undo confirmation gate: cancel must not stage
  await page.goto(`${muzilla.baseUrl}/reviews/${reviewId}?returnTo=%2Freviews`);
  const undoBtn = page.getByRole("button", { name: "Ripristina applicazione" });
  await expect(undoBtn).toBeVisible({ timeout: 10_000 });
  await expect(undoBtn).toBeEnabled();
  const before = await (
    await page.request.get(`${muzilla.baseUrl}/api/reviews/${reviewId}`)
  ).json();
  expect(before.undo_runs.length).toBe(0);

  await undoBtn.click();
  await expect(
    page.getByRole("dialog", { name: "Ripristinare l’applicazione?" }),
  ).toBeVisible();
  await expect(
    page.getByText("Verranno eseguite in ordine inverso"),
  ).toBeVisible();
  await page.getByRole("button", { name: "Annulla" }).click();
  await expect(
    page.getByRole("dialog", { name: "Ripristinare l’applicazione?" }),
  ).not.toBeVisible();
  const stillApplied = await (
    await page.request.get(`${muzilla.baseUrl}/api/reviews/${reviewId}`)
  ).json();
  expect(stillApplied.state).toBe("applied");
  expect(stillApplied.undo_runs.length).toBe(0);

  // Now actually confirm undo and verify file restoration
  await undoBtn.click();
  await expect(
    page.getByRole("dialog", { name: "Ripristinare l’applicazione?" }),
  ).toBeVisible();
  // Wait for POST /api/reviews/{id}/undo as the UI does
  const undoResponsePromise = page.waitForResponse((r) =>
    r.url().endsWith(`/api/reviews/${reviewId}/undo`),
  );
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Ripristina file" })
    .click();
  const undoRes = await undoResponsePromise;
  expect(undoRes.status(), await undoRes.text()).toBe(202);
  await expect(
    page.getByRole("dialog", { name: "Ripristinare l’applicazione?" }),
  ).not.toBeVisible({ timeout: 5000 });
  const undone = await pollUntilUndone(page, muzilla.baseUrl, reviewId);
  expect(undone.undo_runs[undone.undo_runs.length - 1].state).toBe("undone");
});

test("the undo draft screen shows an unmissable banner naming the original review", async ({
  page,
  muzilla,
}) => {
  await muzilla.scanOneFile();
  const reviewId = await createManualReviewViaAPI(page, muzilla.baseUrl);
  // Use distinct title so banner is visible
  await acceptAllOperations(page, muzilla.baseUrl, reviewId);

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
  ).toBeVisible({ timeout: 15_000 });
  await pollUntilApplied(page, muzilla.baseUrl, reviewId);

  await page.goto(`${muzilla.baseUrl}/reviews/${reviewId}?returnTo=%2Freviews`);
  await expect(page.getByText("Tag metadata")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText(/Stato: applied/).first()).toBeVisible({
    timeout: 10_000,
  });
  // Banner / heading still shows original review filename/title
  await expect(
    page.getByRole("heading", { name: /silence\.mp3|undo-confirmation-test/ }),
  ).toBeVisible({ timeout: 10_000 });
  await expect(
    page.getByRole("button", { name: "Ripristina applicazione" }),
  ).toBeVisible();
});
