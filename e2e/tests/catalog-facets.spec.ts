import { test, expect } from "./fixtures";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..", "..");
const VENV_PYTHON = path.join(REPO_ROOT, ".venv", "bin", "python");

function seedHighCardinality(libraryDir: string) {
  const dbPath = path.join(path.dirname(libraryDir), "muzilla.db");
  const script = [
    "import sys",
    "from pathlib import Path",
    "from datetime import datetime, UTC",
    "from muzilla.db.engine import create_db_engine, create_session_factory",
    "from muzilla.db.models import Track",
    "db_path = Path(sys.argv[1])",
    "factory = create_session_factory(create_db_engine(db_path))",
    "now = datetime.now(UTC)",
    "with factory() as session:",
    "    for i in range(600):",
    "        has_art = (i % 4 != 0)",
    '        probe = "read error" if i != 0 and i % 10 == 0 else None',
    '        album = None if i != 0 and i % 20 == 0 else f"Album {i:04d}"',
    '        genre = ("Rock",) if i % 3 == 0 else ("Electronic",) if i % 3 == 1 else ("Pop",)',
    '        fmt = "flac" if i % 2 == 0 else "mp3"',
    '        t = Track(path=f"/music/artist_{i:04d}/track.mp3", filename="track.mp3", ext="mp3", size_bytes=1000+i, mtime_ns=i, title=f"Track {i:04d}", artist=f"Artist {i:04d}", album=album, album_artist=f"Artist {i:04d}" if album else None, genre=genre, format=fmt, has_embedded_art=has_art, has_lyrics=bool(i%2), probe_error=probe, missing_since=None, first_seen_at=now, last_scanned_at=now, duration_ms=180000+(i%10)*1000, bitrate=320)',
    "        session.add(t)",
    "    session.commit()",
  ].join("\n");
  const res = spawnSync(VENV_PYTHON, ["-c", script, dbPath], {
    encoding: "utf8",
    timeout: 15000,
  });
  if (res.status !== 0)
    throw new Error(res.stderr || "seed high-cardinality failed");
}

function encodeFacetCursor(value: string): string {
  const raw = JSON.stringify(value);
  // Node base64url without padding mirrors Python urlsafe_b64encode
  return Buffer.from(raw).toString("base64url");
}

test("catalog facets expose combinable searchable chips, counts, keyboard, URL, clear-all, back, and cursor pagination beyond 500", async ({
  page,
  muzilla,
}) => {
  test.setTimeout(90_000);
  seedHighCardinality(muzilla.libraryDir);
  await page.goto(`${muzilla.baseUrl}/catalog`);

  // All four facet combobox triggers + flag buttons visible
  await expect(
    page.getByRole("button", { name: "Tutti gli artisti" }),
  ).toBeVisible({ timeout: 10_000 });
  await expect(
    page.getByRole("button", { name: "Tutti gli album" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Tutti i generi" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Tutti i formati" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "File mancante" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Senza cover" })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Da identificare" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Errori di lettura" }),
  ).toBeVisible();

  // Open artist facet, verify searchable listbox, counts, and Carica altri (high cardinality)
  await page.getByRole("button", { name: "Tutti gli artisti" }).click();
  await expect(page.getByPlaceholder("Cerca artista…")).toBeVisible();
  await expect(page.getByRole("listbox", { name: "Artista" })).toBeVisible();
  await expect(
    page
      .getByRole("listbox", { name: "Artista" })
      .getByRole("option", { name: /Artist 0000/ })
      .first(),
  ).toBeVisible();
  await expect(page.getByText("(1)").first()).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Carica altri", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("100 mostrati")).toBeVisible();

  // Facet search filters values case-insensitive, shows only matching
  const artistSearch = page.getByPlaceholder("Cerca artista…");
  await artistSearch.fill("Artist 0001");
  await expect(
    page
      .getByRole("listbox", { name: "Artista" })
      .getByRole("option", { name: /Artist 0001/ })
      .first(),
  ).toBeVisible();
  // Ensure non-matching option is not visible (search is working, not just appending)
  await expect(
    page
      .getByRole("listbox", { name: "Artista" })
      .getByRole("option", { name: "Artist 0002" }),
  ).toHaveCount(0);
  await artistSearch.fill("");
  await expect(
    page
      .getByRole("listbox", { name: "Artista" })
      .getByRole("option", { name: /Artist 0000/ })
      .first(),
  ).toBeVisible();

  // Select via keyboard Home->Enter (first item Artist 0000), verify chip, count, URL
  await page.keyboard.press("Home");
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("Filtri attivi")).toBeVisible();
  const artistChip = page.getByRole("button", {
    name: "Rimuovi filtro Artista: Artist 0000",
  });
  await expect(artistChip).toBeVisible();
  await expect(page).toHaveURL(/artist=Artist(%20|\+)0000/);
  // Count for this artist should be 1, track list total should reflect filtering (1 file, since each artist unique)
  // The header shows "X di Y file"
  await expect(page.getByText("1 di 1 file")).toBeVisible({ timeout: 5_000 });

  // Verify album facet is narrowed by artist filter (combinable) — album facet still shows its own alternatives filtered
  await page.getByRole("button", { name: "Tutti gli album" }).click();
  await expect(page.getByPlaceholder("Cerca album…")).toBeVisible();
  await expect(page.getByRole("listbox", { name: "Album" })).toBeVisible();
  // Artist 0000's album is missing (since i%20==0 -> album None for i=0, so this artist is unmatched)
  // Instead Artist 0001 has Album 0001, so pick a different artist to test combinable.
  // Clear previous and select Artist 0001 which has album
  await page
    .getByRole("button", { name: "Rimuovi filtro Artista: Artist 0000" })
    .click();
  await expect(artistChip).not.toBeVisible();
  // Reopen artist and select Artist 0001 via click
  await page.getByRole("button", { name: "Tutti gli artisti" }).click();
  await expect(page.getByPlaceholder("Cerca artista…")).toBeVisible();
  await page.getByPlaceholder("Cerca artista…").fill("Artist 0001");
  await page
    .getByRole("listbox", { name: "Artista" })
    .getByRole("option", { name: "Artist 0001" })
    .first()
    .click();
  await expect(
    page.getByRole("button", { name: "Rimuovi filtro Artista: Artist 0001" }),
  ).toBeVisible();
  await expect(page).toHaveURL(/artist=Artist(%20|\+)0001/);
  await expect(page.getByText("1 di 1 file")).toBeVisible();
  // Now album facet should show only Album 0001 (self-excluded artist filter, but album filtered by artist)
  await page.getByRole("button", { name: "Tutti gli album" }).click();
  await expect(page.getByRole("listbox", { name: "Album" })).toBeVisible();
  await expect(
    page
      .getByRole("listbox", { name: "Album" })
      .getByRole("option", { name: /Album 0001/ })
      .first(),
  ).toBeVisible();
  await expect(page.getByText("(1)").first()).toBeVisible();
  // Select album 0001 via click, verify second chip and URL combinable
  await page
    .getByRole("listbox", { name: "Album" })
    .getByRole("option", { name: "Album 0001" })
    .first()
    .click();
  const albumChip = page.getByRole("button", {
    name: "Rimuovi filtro Album: Album 0001",
  });
  await expect(albumChip).toBeVisible();
  await expect(page).toHaveURL(/artist=Artist(%20|\+)0001/);
  await expect(page).toHaveURL(/album=Album(%20|\+)0001/);
  await expect(page.getByText("1 di 1 file")).toBeVisible();

  // Verify genre facet searchable and counts consistent with server filtering
  await page.getByRole("button", { name: "Tutti i generi" }).click();
  await expect(page.getByPlaceholder("Cerca genere…")).toBeVisible();
  await expect(page.getByRole("listbox", { name: "Genere" })).toBeVisible();
  // Filtered by Artist 0001 (genre Electronic), so genre facet should show Electronic count 1
  // (genre facet excludes its own filter, includes other filters)
  await expect(page.getByText("Electronic")).toBeVisible();
  await expect(
    page
      .getByRole("listbox", { name: "Genere" })
      .getByRole("option", { name: /Electronic/ })
      .first(),
  ).toBeVisible();
  // Search genre within filtered set — should filter to Electronic
  await page.getByPlaceholder("Cerca genere…").fill("ele");
  await expect(
    page
      .getByRole("listbox", { name: "Genere" })
      .getByRole("option", { name: /Electronic/ })
      .first(),
  ).toBeVisible();
  await page.getByPlaceholder("Cerca genere…").fill("");
  await expect(
    page
      .getByRole("listbox", { name: "Genere" })
      .getByRole("option", { name: /Electronic/ })
      .first(),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("listbox", { name: "Genere" })).not.toBeVisible();
  // Escape should return focus to trigger
  await expect(
    page.getByRole("button", { name: /Tutti i generi|Genere:/ }),
  ).toBeFocused();

  // Verify format facet — filtered by Artist 0001 (mp3), so only mp3 should appear
  await page.getByRole("button", { name: "Tutti i formati" }).click();
  await expect(page.getByPlaceholder("Cerca formato…")).toBeVisible();
  await expect(page.getByRole("listbox", { name: "Formato" })).toBeVisible();
  await expect(
    page
      .getByRole("listbox", { name: "Formato" })
      .getByRole("option", { name: /mp3/ })
      .first(),
  ).toBeVisible();
  // flac is filtered out when an mp3 artist is selected, verify it's not in filtered list
  await expect(
    page
      .getByRole("listbox", { name: "Formato" })
      .getByRole("option", { name: /flac/ }),
  ).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("listbox", { name: "Formato" }),
  ).not.toBeVisible();
  await expect(
    page.getByRole("button", { name: /Tutti i formati|Formato:/ }),
  ).toBeFocused();

  // Test keyboard Escape returns focus for artist as well
  await page
    .getByRole("button", { name: "Artista: Artist 0001", exact: true })
    .click();
  await expect(page.getByPlaceholder("Cerca artista…")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("listbox", { name: "Artista" }),
  ).not.toBeVisible();
  await expect(
    page.getByRole("button", { name: "Artista: Artist 0001", exact: true }),
  ).toBeFocused();

  // Clear-all clears all facet chips and URL, but keeps pagination/catalog state
  await expect(page.getByLabel("Filtri attivi")).toBeVisible();
  await page.getByRole("button", { name: "Cancella tutto" }).click();
  await expect(
    page.getByRole("button", { name: "Rimuovi filtro Artista: Artist 0001" }),
  ).not.toBeVisible();
  await expect(
    page.getByRole("button", { name: "Rimuovi filtro Album: Album 0001" }),
  ).not.toBeVisible();
  await expect(page.getByLabel("Filtri attivi")).not.toBeVisible();
  await expect(page).toHaveURL(new RegExp(`${muzilla.baseUrl}/catalog$`));

  // Browser Back restores cleared filters
  await page.getByRole("button", { name: "Tutti gli artisti" }).click();
  await page.getByPlaceholder("Cerca artista…").fill("Artist 0002");
  await page
    .getByRole("listbox", { name: "Artista" })
    .getByRole("option", { name: "Artist 0002" })
    .first()
    .click();
  const urlAfterArtist = page.url();
  await expect(
    page.getByRole("button", { name: "Rimuovi filtro Artista: Artist 0002" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Rimuovi filtro Artista: Artist 0002" })
    .click();
  await expect(
    page.getByRole("button", { name: "Rimuovi filtro Artista: Artist 0002" }),
  ).not.toBeVisible();
  await page.goBack();
  await expect(
    page.getByRole("button", { name: "Rimuovi filtro Artista: Artist 0002" }),
  ).toBeVisible();
  await expect(page).toHaveURL(urlAfterArtist);

  // URL restore on direct load
  await page.goto(`${muzilla.baseUrl}/catalog?artist=Artist%200003&genre=Rock`);
  await expect(
    page.getByRole("button", { name: "Rimuovi filtro Artista: Artist 0003" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Rimuovi filtro Genere: Rock" }),
  ).toBeVisible();
  await expect(page).toHaveURL(/artist=Artist(%20|\+)0003/);
  await expect(page).toHaveURL(/genre=Rock/);

  // Clear again for pagination test
  await page.goto(`${muzilla.baseUrl}/catalog`);
  await expect(page.getByLabel("Filtri attivi")).not.toBeVisible();

  // Cursor pagination beyond 500 via UI: Carica altri repeatedly, verify no 422, reach 0599
  await page.getByRole("button", { name: "Tutti gli artisti" }).click();
  await expect(page.getByRole("listbox", { name: "Artista" })).toBeVisible();
  await expect(
    page
      .getByRole("listbox", { name: "Artista" })
      .getByRole("option", { name: /Artist 0000/ })
      .first(),
  ).toBeVisible();
  // 100 shown initially, click 5 times to reach 600 (100->200->300->400->500->600)
  for (let i = 0; i < 5; i++) {
    const btn = page.getByRole("button", { name: "Carica altri", exact: true });
    await expect(btn).toBeVisible();
    const expectedFirstOfNextPage = `Artist ${String((i + 1) * 100).padStart(4, "0")}`;
    await btn.click();
    await expect(
      page
        .getByRole("listbox", { name: "Artista" })
        .getByRole("option", { name: new RegExp(expectedFirstOfNextPage) })
        .first(),
    ).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText(`${(i + 2) * 100} mostrati`)).toBeVisible();
  }
  await expect(
    page
      .getByRole("listbox", { name: "Artista" })
      .getByRole("option", { name: /Artist 0599/ })
      .first(),
  ).toBeVisible({ timeout: 5_000 });
  await expect(page.getByText("600 mostrati")).toBeVisible();
  // One more click should not add more (or button may disappear if no more), but should not error
  // Verify total browsed facets still functional
  await expect(page.getByRole("listbox", { name: "Artista" })).toBeVisible();

  // Also verify album pagination similarly (album facet also high cardinality)
  await page.getByPlaceholder("Cerca artista…").click();
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("listbox", { name: "Artista" }),
  ).not.toBeVisible();
  await page.getByRole("button", { name: "Tutti gli album" }).click();
  await expect(page.getByRole("listbox", { name: "Album" })).toBeVisible();
  await expect(
    page
      .getByRole("listbox", { name: "Album" })
      .getByRole("option", { name: /Album 0001/ })
      .first(),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Carica altri", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Carica altri", exact: true }).click();
  await expect(page.getByText("200 mostrati")).toBeVisible({ timeout: 5_000 });
  // After loading second page, some album beyond 100 should be visible (e.g. 0101 since 0100 is missing)
  await expect(
    page
      .getByRole("listbox", { name: "Album" })
      .getByRole("option", { name: /Album 01/ })
      .first(),
  ).toBeVisible({ timeout: 5_000 });
  await page.getByPlaceholder("Cerca album…").click();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("listbox", { name: "Album" })).not.toBeVisible();
});

test("catalog facets handle empty vs error distinct and retry", async ({
  page,
  muzilla,
}) => {
  test.setTimeout(60_000);
  seedHighCardinality(muzilla.libraryDir);
  let facetsCalls = 0;
  await page.route("**/api/tracks/facets*", async (route) => {
    facetsCalls += 1;
    if (facetsCalls <= 4) {
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "boom" }),
      });
    } else {
      await route.continue();
    }
  });
  await page.goto(`${muzilla.baseUrl}/catalog`);
  await expect(
    page.getByRole("button", { name: "Tutti gli artisti" }),
  ).toBeVisible({ timeout: 10_000 });

  // Error: first facets load returns 500, verify distinct error UI and retry (distinct from empty)
  await page.getByRole("button", { name: "Tutti gli artisti" }).click();
  await expect(page.getByText("Impossibile caricare i filtri.")).toBeVisible({
    timeout: 8_000,
  });
  await expect(page.getByRole("button", { name: "Riprova" })).toBeVisible();
  await expect(page.getByText(/Nessun risultato/)).not.toBeVisible();
  await page.getByRole("button", { name: "Riprova" }).click();
  await expect(page.getByRole("listbox", { name: "Artista" })).toBeVisible();
  await expect(
    page
      .getByRole("listbox", { name: "Artista" })
      .getByRole("option", { name: /Artist 0000/ })
      .first(),
  ).toBeVisible();
  await page.getByPlaceholder("Cerca artista…").click();
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("listbox", { name: "Artista" }),
  ).not.toBeVisible();
  await page.unroute("**/api/tracks/facets*");

  // Empty: search with nonexistent term shows Nessun risultato, not error, on same loaded data
  await page.getByRole("button", { name: "Tutti gli artisti" }).click();
  await expect(page.getByPlaceholder("Cerca artista…")).toBeVisible();
  await page.getByPlaceholder("Cerca artista…").fill("nonexistent-zzzzz");
  await expect(page.getByText(/Nessun risultato/)).toBeVisible();
  await expect(page.getByText(/Prova un termine diverso/)).toBeVisible();
  await expect(
    page.getByText("Impossibile caricare i filtri"),
  ).not.toBeVisible();
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("listbox", { name: "Artista" }),
  ).not.toBeVisible();
  await expect(
    page.getByRole("button", { name: "Tutti gli artisti" }),
  ).toBeFocused();
});

test("catalog facets high-cardinality pagination API respects limit 500 and cursor", async ({
  page,
  muzilla,
}) => {
  test.setTimeout(60_000);
  seedHighCardinality(muzilla.libraryDir);

  // Verify via direct fetch (Node fetch) that API enforces limit 500 and cursor pagination works beyond 500 without OFFSET/422
  const r1 = await fetch(`${muzilla.baseUrl}/api/tracks/facets?limit=100`);
  expect(r1.status).toBe(200);
  const j1 = await r1.json();
  expect(j1.artists.length).toBe(100);
  expect(j1.artists[0].value).toBe("Artist 0000");
  expect(j1.artists[0].count).toBe(1);

  const lastOfFirst = j1.artists[j1.artists.length - 1].value as string;
  const cursor = encodeFacetCursor(lastOfFirst);
  const r2 = await fetch(
    `${muzilla.baseUrl}/api/tracks/facets?limit=100&cursor=${encodeURIComponent(cursor)}`,
  );
  expect(r2.status).toBe(200);
  const j2 = await r2.json();
  expect(j2.artists.length).toBe(100);
  expect(j2.artists[0].value).toBe("Artist 0100");
  // No overlap between pages
  const set1 = new Set(j1.artists.map((a: { value: string }) => a.value));
  const set2 = new Set(j2.artists.map((a: { value: string }) => a.value));
  for (const v of set2) expect(set1.has(v)).toBe(false);

  const r500 = await fetch(`${muzilla.baseUrl}/api/tracks/facets?limit=500`);
  expect(r500.status).toBe(200);
  const j500 = await r500.json();
  expect(j500.artists.length).toBe(500);

  const r600 = await fetch(`${muzilla.baseUrl}/api/tracks/facets?limit=600`);
  expect(r600.status).toBe(422);

  const rSearch = await fetch(
    `${muzilla.baseUrl}/api/tracks/facets?facet_q=Artist%200001`,
  );
  expect(rSearch.status).toBe(200);
  const jSearch = await rSearch.json();
  expect(jSearch.artists.length).toBeGreaterThan(0);
  for (const a of jSearch.artists)
    expect(a.value.toLowerCase()).toContain("artist 0001");

  // Verify that repeated cursor pagination can reach beyond 500 without offset param
  let cur: string | undefined;
  let totalFetched = 0;
  const seen = new Set<string>();
  for (let i = 0; i < 6; i++) {
    const url = cur
      ? `${muzilla.baseUrl}/api/tracks/facets?limit=100&cursor=${encodeURIComponent(cur)}`
      : `${muzilla.baseUrl}/api/tracks/facets?limit=100`;
    const r = await fetch(url);
    expect(r.status).toBe(200);
    const j = await r.json();
    expect(j.artists.length).toBeGreaterThan(0);
    for (const a of j.artists) {
      expect(seen.has(a.value)).toBe(false);
      seen.add(a.value);
    }
    totalFetched += j.artists.length;
    cur = encodeFacetCursor(j.artists[j.artists.length - 1].value);
    // Ensure we never sent offset
    expect(url.includes("offset")).toBe(false);
  }
  expect(totalFetched).toBe(600);
  expect(seen.has("Artist 0599")).toBe(true);

  // Also verify in browser that the facet still works after API pagination
  await page.goto(`${muzilla.baseUrl}/catalog`);
  await expect(
    page.getByRole("button", { name: "Tutti gli artisti" }),
  ).toBeVisible({ timeout: 10_000 });
  await page.getByRole("button", { name: "Tutti gli artisti" }).click();
  await expect(
    page
      .getByRole("listbox", { name: "Artista" })
      .getByRole("option", { name: /Artist 0000/ })
      .first(),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Carica altri", exact: true }),
  ).toBeVisible();
});
