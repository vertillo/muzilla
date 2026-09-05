# UX Manual Matrix — Acceptance Record

This document records repeatable manual/responsive/accessibility acceptance for primary journeys. Automated checks are in `e2e/tests/ux-manual-matrix.spec.ts` and `e2e/tests/review-bulk-edge.spec.ts` (stable). Inherently visual judgments are checklist rows below, verified manually and re-checked on the same viewports.

## Scope

Primary journeys: Dashboard → Catalog (grid/cards) → Review inbox (filtered list, bulk) → Review detail (candidate, edits, cover, grouping, apply/undo) → Settings (effective policy). Spec reference: `docs/product-spec.md` Interaction quality; this document is the acceptance record.

## Viewports and Conditions

- Desktop: 1280×800, 1280×1024
- Tablet: 820×1180 (portrait), 1180×820 (landscape)
- Mobile: 390×844 (drawer), 375×667 (small phone)
- 200% zoom equivalent: 640×720 (1280/2), 320×256 (640/2) — halved viewports per WCAG 1.4.10 reflow
- Low height / landscape: 900×500, 844×390 (mobile landscape)
- Font scaling: browser default 16px + 200% zoom proxy (small viewports above) — manual check for OS font scaling beyond zoom proxy
- Safe-area: `env(safe-area-inset-bottom)` class presence on sticky bar verified in e2e; AppShell drawer/footer device insets require manual device check
- Long text: filename 200 chars + extension, path 300 chars, lyrics 500 chars, track title 120 chars
- Focus: dialog focus trap and focus return to trigger automated; Tab order/out-line visibility and focus not obscured behind sticky are manual checklist items
- Reduced motion: `prefers-reduced-motion: reduce` via `page.emulateMedia({ reducedMotion: 'reduce' })` — spot transition check automated, full animation audit manual
- No-hover: essential state textual (Badge text, not tooltip-only) automated; pointer: coarse interaction is manual

## Automated Checks (stable)

Run: `cd frontend && npm run build && cd ../e2e && npm run test -- ux-manual-matrix`

- No horizontal scroll: `document.documentElement.scrollWidth <= window.innerWidth` for Dashboard, Catalog, Inbox, Detail at each viewport
- Safe-area class present on sticky bar: `.sticky.bottom-0` contains `safe-area-inset-bottom` (automated). AppShell drawer/footer insets are manual — e2e runs with auth disabled so footer not rendered; device verification required.
- Dialog focus return: open Edit modal → close via Annulla → focus returns to Modifica trigger (automated via `previouslyFocused` restore)
- Reduced-motion disables transitions: spot check `transitionDuration` is `0s`/`0.01ms` when `emulateMedia({ reducedMotion: 'reduce' })` (automated)
- No-hover textual state: Badges/State labels (Pronta/Needs attention/etc.) visible without hover (automated query without hover). Coarse-pointer hover-only behavior is manual — Playwright does not reliably emulate `pointer: coarse` for this check.
- Long-text wraps: long filename/title/path `break-words`/`break-all` without `scrollWidth` overflow at 390px (automated)
- Keyboard reachability spot: Inbox Apri button reachable via `Tab` and `focus()` (automated spot). Full Tab-visible outline and focus-not-obscured behind sticky bar are manual checklist items.
- Bulk/anchor/discard: covered in `e2e/tests/review-bulk-edge.spec.ts` (automated) — not duplicated here

## Visual Checklist (manual, repeatable)

| # | Journey/Condition | Check | Result | Notes |
| --- | ------------------- | ------- | -------- | ------- |
| 1 | Desktop 1280×800 – Inbox | Filters readable, confidence/session visible, chips removable, bulk bar sticky not overlapping | PASS | |
| 2 | Tablet 820×1180 – Detail | Candidate snapshot, cover, operations readable, grouping preview not clipped | PASS | |
| 3 | Mobile 390×844 – Shell | Menu drawer opens/closes, navigation reachable, content not behind drawer/safe-area | PASS | |
| 4 | 200% zoom 640×720 – Inbox/Detail | No horizontal scroll, filters wrap, badges still textual, load-more reachable | PASS | |
| 5 | 200% zoom 320×256 – Detail | Extreme reflow: sticky Apply bar still shows counts, buttons wrap, no clipped content; Modal reflows within viewport | PASS | Fixed: flex-wrap on action bar + Modal `max-w-[calc(100vw-1rem)] max-h-[calc(100dvh-1rem)] overflow-y-auto` |
| 6 | Low height 900×500 – Detail | Sticky footer not obscuring scroll, scrollable main, focus not hidden behind bar | PASS | Manual: `pb-28` on detail container + `safe-area` — automated only checks sticky class, not `getBoundingClientRect` overlap |
| 7 | Long text – Inbox/Detail | 200-char filename breaks, 300-char path breaks with `break-all`, 500-char lyrics textarea scrollable, not overlapping | PASS | Automated `scrollWidth` check + manual visual |
| 8 | Focus return – Edit modal | Open Modifica → Salva/Annulla → focus returns to Modifica trigger | PASS | Automated: Modal `previouslyFocused` restore (focus lands on Close first, then returns) |
| 9 | Keyboard – Detail | J/K roving focus moves, not firing inside dialog/input, Tab order logical, Shortcuts dialog traps focus | PASS | Manual + automated spot: J/K + Tab spot |
| 10 | Reduced motion – All | With `prefers-reduced-motion: reduce`, no animation, transitions instant | PASS | Automated spot (full set, not slice) + CSS `@media (prefers-reduced-motion: reduce)` |
| 11 | No-hover – Inbox | State (Ready/Needs attention), confidence, provider, issues visible as Badges without hover | PASS | Automated badge visibility; coarse-pointer manual |
| 12 | Safe-area – Mobile | `pb-[max(0.75rem,env(safe-area-inset-bottom))]` on sticky bar and drawer footer | PASS | Sticky class automated; drawer/footer device insets manual (auth off in e2e) |
| 13 | Bulk – Inbox | Select visible, count preview, Rifiuta selezionate only selected, undo toast + inline undo restores selection, partial failure message | PASS | See `review-bulk-edge.spec.ts` |
| 14 | Anchor – Detail | Neighbors 404 anchored banner visible immediately after reject, detail stays open, Close restores filtered list without anchored item | PASS | Fixed: invalidate `review-neighbors` on decision success |
| 15 | Unsaved – Detail | Modifica dirty → Annulla/Escape/Back blocked → Restare stays, Scartare discards, no implicit save; Back Scartare navigates to actual previous destination | PASS | Fixed: Back guard pushes detailHref and Scartare does `history.back()` to prev, not duplicate |

## Blockers Fixed

- **200% zoom 320×256 wrapping & Modal reflow**: ReviewInbox filters and Detail action bar used `flex-wrap` already but long confidence labels could overflow; added `flex-wrap`. Modal `w-[380px]` overflowed at 320px — fixed to `max-w-[calc(100vw-1rem)] max-h-[calc(100dvh-1rem)] overflow-y-auto p-4` with scroll, verified at 320×256.
- **Sticky bar obscuring focus**: Detail container `pb-28` already present; sticky class verified, detailed overlap is manual visual at 900×500.
- **Reduced motion**: Added global `@media (prefers-reduced-motion: reduce)` disabling durations; verified via emulated media spot check over full element set.
- **Long text overflow**: Verified `break-words`/`break-all` on filename/path/title, textarea scrollable via `scrollWidth`; fixed strict `getByText` ambiguity.
- **Drawer safe-area**: AppShell already uses `pb-[max(...)]`; sticky class verified, device insets manual.
- **Bulk never implicit & undo restores selection**: Select-visible only, no select-all-filter; bulk undo now restores prior successful selection (`setSelected` on undo), partial failures retain failed selection; verified in frontend unit (2 selezionate after undo) and e2e.
- **Anchor banner immediate**: `useReviewOperationDecisions` now invalidates `review-neighbors` so banner appears without manual refresh; verified in e2e.
- **Back while dirty**: Removed duplicate guard `pushState` on dirty mount; popstate handler now pushes `detailHref` and Scartare does single `history.back()` to actual previous destination; Annulla/Escape/Back all route through Restare/Scartare with honest reachable boundary.

## How to Re-run

1. `cd frontend && npm run lint && npm run typecheck && npm run build`
2. `cd e2e && npm run test -- ux-manual-matrix --reporter=list`
3. `cd e2e && npm run test -- review-bulk-edge --reporter=list`
4. Manual visual spot-check on the viewports above using the checklist table (5 min).

## Release-candidate verification 2026-09-05 (d0824b6)

Candidate `d0824b6b3c9137cec823af250d59c63f6a8dc807` (clean tree), frontend rebuilt
from exact source and served by the real backend (`uvicorn muzilla.api.app:app`)
with deterministic `e2e/mock_provider_server.py` and disposable scratch
config/library/DB per run. Browser environment: macOS 26.6 arm64, Chromium
"Google Chrome for Testing 151.0.7922.34" via Playwright `@playwright/test` 1.62,
headed=False.

- Console/runtime/network (all contexts: mobile 390x844, desktop 1280x800,
  four zoom/low viewports x two routes, coarse-pointer context): PASS — zero
  console errors, zero page errors, zero failed/4xx+ requests.
- A. Mobile safe-area / device-inset (390x844, mobile + touch, DSF 2): PASS —
  drawer opens/closes via tap and Escape, Dashboard `scrollWidth == innerWidth`
  (no horizontal overflow), sticky bar carries
  `pb-[max(0.75rem,env(safe-area-inset-bottom))]`, counts text visible.
- B. Keyboard Tab visible focus + dialog return (1280x800): PASS — 19-stop Tab
  walk, every stop with a visible focus ring (`invisibleCount: 0`), no content
  stop obscured behind the sticky bar (sole overlap is the sticky's own child);
  Edit dialog traps focus and Annulla returns focus to the Modifica trigger;
  Shortcuts dialog traps focus.
- C. Sticky/grid clipping at 200% zoom + low viewports (640x720, 320x256,
  900x500, 844x390 on `/reviews` and `/reviews/1`): PASS —
  `scrollWidth == innerWidth` everywhere, sticky counts in viewport, modal fits
  320px viewport (`max-w-[calc(100vw-1rem)]`), `pb-28` scroll mitigation present.
- D. Coarse-pointer / touch targets (390x844, touch,
  `matchMedia(pointer:coarse) == true`): PASS — no target below the WCAG 2.2 AA
  24px minimum, tap on the first inbox action navigates to detail, badge/state
  text present without hover, no indispensable hover-only control found.

Residual limitations (observed, non-blocking; not readiness failures):

1. Desktop Chrome reports `env(safe-area-inset-bottom) = 0`, so computed sticky
   padding falls back to 0.75rem; true notch/gesture-bar overlap needs a
   physical-device check (class presence plus drawer/no-overflow behavior were
   verified here).
2. Forced `scrollIntoView({block:"end"})` can tuck a content button behind the
   sticky bar, while native Tab minimal-scroll keeps every stop visible
   (verified); root `scroll-padding` is `auto` and `pb-28` is the mitigation.
3. Stacked `Apri`/`Rifiuta` buttons have a 4px vertical gap (below 8px touch-spacing
   guidance) and 24px height, which meets the WCAG 2.2 AA 24px web minimum but is
   below native iOS 44pt guidance — residual polish only.

Artifacts (ephemeral, outside repo): 18 PNGs in `/tmp/muzilla-manual/` (A/B/C/D
series) plus run scripts `verify.mjs`, `followup.mjs`, `padcheck.mjs`.

## Evidence

- Frontend unit: `frontend/src/pages/ReviewInbox.test.tsx` (bulk, partial, filter URL), `frontend/src/pages/ReviewDetail.test.tsx` (anchor, unsaved guard, filter URL)
- E2E: `e2e/tests/ux-manual-matrix.spec.ts` (viewports, long-text, focus return, reduced-motion, no-hover, safe-area), `e2e/tests/review-bulk-edge.spec.ts` (bulk, anchor, discard)
- Styles: `frontend/src/styles/index.css` reduced-motion
- Inbox/Detail: `frontend/src/pages/ReviewInbox.tsx` (bulk, confidence/session), `frontend/src/pages/ReviewDetail.tsx` (anchor, Restare/Scartare)

Last verified: 2026-09-05 on candidate d0824b6 — Chromium "Google Chrome for Testing 151.0.7922.34" via Playwright 1.62 (headed=False) on macOS 26.6 arm64; console/network clean, mobile safe-area/drawer, full 19-stop Tab outline with dialog focus return, sticky clipping at 200%-zoom/low viewports, and coarse-pointer checks all exercised (see release-candidate verification above). Prior 2026-09-03 automated record (sticky class, focus return via Close, reduced-motion spot, badge visibility, long-text scrollWidth, Modal reflow at 320x256) remains valid; no manual checklist item remains unexercised on this candidate apart from the physical-device notch residual noted above.
