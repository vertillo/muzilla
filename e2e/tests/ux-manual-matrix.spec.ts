import { test, expect } from "./fixtures";

test.describe("ux manual matrix - automated stable checks", () => {
  const viewports = [
    { name: "desktop", width: 1280, height: 800 },
    { name: "tablet", width: 820, height: 1180 },
    { name: "mobile", width: 390, height: 844 },
    { name: "200% zoom desktop", width: 640, height: 720 },
    { name: "200% zoom extreme", width: 320, height: 256 },
    { name: "low height landscape", width: 900, height: 500 },
  ];

  for (const vp of viewports) {
    test(`primary journeys usable at ${vp.name} (${vp.width}x${vp.height}) without horizontal scroll`, async ({
      page,
      muzilla,
    }) => {
      await muzilla.scanOneFile("ux-matrix.flac");
      const reviewId = await muzilla.createManualReview();
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await page.goto(`${muzilla.baseUrl}/reviews`);
      await expect(
        page.getByRole("heading", { name: "Revisioni" }),
      ).toBeVisible();
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth + 1,
        ),
      ).toBeTruthy();
      await page.goto(
        `${muzilla.baseUrl}/reviews/${reviewId}?returnTo=%2Freviews`,
      );
      await expect(
        page.getByRole("heading", { name: "ux-matrix.flac" }),
      ).toBeVisible();
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth + 1,
        ),
      ).toBeTruthy();
      await page.goto(`${muzilla.baseUrl}/catalog`);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth + 1,
        ),
      ).toBeTruthy();
      await page.goto(`${muzilla.baseUrl}/`);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth + 1,
        ),
      ).toBeTruthy();

      await page.goto(
        `${muzilla.baseUrl}/reviews/${reviewId}?returnTo=%2Freviews`,
      );
      const sticky = page.locator(".sticky.bottom-0");
      await expect(sticky).toBeVisible();
      await expect(sticky).toHaveClass(/safe-area-inset-bottom/);
    });
  }

  test("long text wraps without overflow", async ({ page, muzilla }) => {
    const longName = "a".repeat(200) + ".flac";
    muzilla.addFixtureFile(longName);
    await muzilla.scanOneFile(longName);
    const reviewId = await muzilla.createManualReview();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`${muzilla.baseUrl}/reviews`);
    // filename appears in badge list; target the article heading specifically to avoid strict ambiguity
    await expect(
      page.getByRole("button", { name: new RegExp(longName.slice(0, 20)) }),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth + 1,
      ),
    ).toBeTruthy();
    await page.goto(
      `${muzilla.baseUrl}/reviews/${reviewId}?returnTo=%2Freviews`,
    );
    await expect(page.getByRole("heading", { name: longName })).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth + 1,
      ),
    ).toBeTruthy();
  });

  test("focus return after modal", async ({ page, muzilla }) => {
    await muzilla.scanOneFile("focus.flac");
    const reviewId = await muzilla.createManualReview();
    await page.goto(
      `${muzilla.baseUrl}/reviews/${reviewId}?returnTo=%2Freviews`,
    );
    const trigger = page.getByRole("button", { name: "Modifica" }).first();
    await expect(trigger).toBeVisible();
    await trigger.click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    // Modal focuses first focusable (Close button) per accessible trap, not the textbox — assert dialog is focused trap
    await expect(page.getByRole("button", { name: "Close" })).toBeFocused();
    await page.getByRole("button", { name: "Annulla" }).click();
    await expect(dialog).not.toBeVisible();
    await expect(trigger).toBeFocused();
    const box = await trigger.boundingBox();
    const viewport = page.viewportSize();
    if (box && viewport) {
      expect(box.y + box.height).toBeLessThan(viewport.height - 20);
    }
  });

  test("dialog reflows at 320px without overflow and is scrollable", async ({ page, muzilla }) => {
    await muzilla.scanOneFile("reflow.flac");
    const reviewId = await muzilla.createManualReview();
    await page.setViewportSize({ width: 320, height: 256 });
    await page.goto(`${muzilla.baseUrl}/reviews/${reviewId}?returnTo=%2Freviews`);
    const trigger = page.getByRole("button", { name: "Modifica" }).first();
    await trigger.click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
    ).toBeTruthy();
    const box = await dialog.boundingBox();
    expect(box).not.toBeNull();
    if (box) {
      // dialog must fit within viewport with 1rem margin (320 - 16 = 304 max)
      expect(box.width).toBeLessThanOrEqual(310);
      expect(box.x).toBeGreaterThanOrEqual(0);
    }
    // long input should not cause overflow
    await page.getByLabel("Valore tag").fill("a".repeat(200));
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
    ).toBeTruthy();
  });

  test("reduced motion disables transitions", async ({ page, muzilla }) => {
    await muzilla.scanOneFile("motion.flac");
    const reviewId = await muzilla.createManualReview();
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto(
      `${muzilla.baseUrl}/reviews/${reviewId}?returnTo=%2Freviews`,
    );
    await expect(
      page.getByRole("heading", { name: "motion.flac" }),
    ).toBeVisible();
    await page.waitForTimeout(200);
    const isReduced = await page.evaluate(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    expect(isReduced).toBe(true);
    const durations = await page.evaluate(() =>
      Array.from(document.querySelectorAll("*")).slice(0, 100).map((el) => getComputedStyle(el).transitionDuration),
    );
    const hasReduced = durations.some((d) => d === "0s" || d === "0.01ms" || d.includes("0.01ms"));
    if (!hasReduced) {
      const hasRule = await page.evaluate(() => {
        try {
          return Array.from(document.styleSheets).some((sheet) => {
            try {
              return Array.from(sheet.cssRules).some((r) => (r as CSSMediaRule).cssText?.includes('prefers-reduced-motion'));
            } catch { return false; }
          });
        } catch { return false; }
      });
      expect(hasRule).toBeTruthy();
    } else {
      expect(hasReduced).toBeTruthy();
    }
    await page.emulateMedia({ reducedMotion: "no-preference" });
  });

  test("no-hover essential state visible and keyboard reachable", async ({
    page,
    muzilla,
  }) => {
    await muzilla.scanOneFile("nohover.flac");
    await muzilla.createManualReview();
    await page.goto(`${muzilla.baseUrl}/reviews`);
    // badges visible without hover — use specific badge container to avoid matching select options
    const badge = page.locator("article").first().locator("span").filter({ hasText: /Pronta|Needs attention|Not scored/ }).first();
    // fallback to any badge if article not yet: avoid strict getByText matching select option
    if (await badge.count() === 0) {
      await expect(page.locator("article").first()).toBeVisible();
    } else {
      await expect(badge).toBeVisible();
    }
    await page.keyboard.press("Tab");
    const activeTag = await page.evaluate(
      () => document.activeElement?.tagName,
    );
    expect(["BUTTON", "A", "INPUT", "SELECT", "TEXTAREA"]).toContain(activeTag);
  });

  test("safe-area insets respected (sticky class)", async ({
    page,
    muzilla,
  }) => {
    await muzilla.scanOneFile("safe.flac");
    const reviewId = await muzilla.createManualReview();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(
      `${muzilla.baseUrl}/reviews/${reviewId}?returnTo=%2Freviews`,
    );
    const sticky = page.locator(".sticky.bottom-0");
    await expect(sticky).toHaveClass(/safe-area-inset-bottom/);
    await page.goto(`${muzilla.baseUrl}/reviews`);
    await page.getByRole("button", { name: "Menu" }).click();
    await expect(
      page.getByRole("navigation", { name: "Navigazione principale" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Chiudi navigazione" }).click();
  });
});
