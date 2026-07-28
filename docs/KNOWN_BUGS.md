# Known bugs — pending a fix plan

Found during Phase 8 frontend test-infrastructure work (`docs/PLAN.md`
§12e, step 4.3) while writing Playwright coverage against the real app.
Both are characterized (not fixed) by the e2e specs referenced below.
This file exists so a future planning session doesn't have to
rediscover them from `docs/PROGRESS.md`'s chronological log — once a
fix is planned and landed, delete the relevant entry (or the whole
file) rather than letting it go stale.

---

## 1. Catalog's "no results" empty state shows the wrong message for a zero-result search

**Where:** `frontend/src/pages/Catalog.tsx`, the empty-state branch
around the track list (`isLoading ? … : visibleTracks.length === 0 ? …`).

**What's wrong:** The branch picks its message with
`total === 0 ? "Run muzilla scan…" : "No tracks match…"`. `total` comes
from `data?.pages[0]?.total`, which is the **search-scoped** count
returned by the API (`db/repo/tracks.py::list_tracks`'s `total` —
computed after the search/filter query, not over the whole library).
So searching a non-empty library for a term that matches nothing also
has `total === 0`, and the user is told to "Run `muzilla scan <path>`
to index your library" — the message meant for a genuinely empty
library — instead of "No tracks match the current search and filters."

**Why it happens:** `total` is being used as a proxy for "is the
library empty" when it actually means "did this specific query match
anything." The two questions need different data — the library-empty
check needs an unfiltered/unsearched count (or a separate signal from
the API), not the current query's `total`.

**Repro:** `e2e/tests/catalog.spec.ts`, test `"search filters the
track list"` — asserts the *actual* (wrong) message is shown after
searching for a nonexistent term, with a comment explaining why.

**Scope note:** This is the same defect class `docs/PHASE8_BRIEF.md`
Step 5.4 already targets generally ("Loading states are `EmptyState
title="Loading…"` everywhere" / stale empty-state copy under a query).
Worth fixing as part of that step rather than as a standalone patch.

---

## 2. None of the five `grouping_correction` actions apply themselves

**Where:** `src/muzilla/services/grouping.py` (backend: `pin_group`,
`merge_groups`, `split_group`, `reassign_group`, `force_to_singleton` —
all five call `build_changeset` and return; none call `apply` or
enqueue an `apply_changeset` job) and `frontend/src/pages/Groups.tsx`
(the Pin / Merge… / Merge into button handlers — none of them apply
the returned changeset either).

**What's wrong:** `default_decision_for_kind` (`changes/builder.py`)
auto-accepts changes with `source="grouping_correction"`, but
"accepted" only means *ready to apply* — it doesn't apply anything.
So clicking **Pin** in the UI never flips `Group.is_pinned`, and
completing a **Merge** never actually merges the groups as seen by
`GET /api/groups`. From the user's perspective both actions silently
no-op beyond clearing the merge-mode banner; nothing tells them the
change is sitting as an unapplied draft.

**Why it happens:** These five actions were built as changeset
producers (correct, per `docs/PLAN.md` §4's "nothing touches disk
until applied" contract) but the *apply* half of the round trip was
never wired up for this particular changeset source — unlike, say,
manual tag edits, which the review screen makes the user explicitly
apply, these have no equivalent follow-through step in `Groups.tsx` at
all.

**Repro:** `e2e/tests/groups.spec.ts`, test `"pin stages a
grouping_correction changeset, but the list is not pinned until it is
applied"` — asserts `is_pinned` stays `false` after clicking Pin,
via the real API.

**The actual product decision needed before this can be fixed:**
should pin/merge/split/reassign/force-singleton auto-apply immediately
(matching what the button label implies — "Pin" reads as an instant
action, not "stage a pin for later review"), or should the UI gain an
explicit apply step/confirmation (consistent with every other
changeset-producing action in the app)? This isn't a bug-fix-in-place;
it's a UX call. Already flagged for the Phase 7 stop-and-ask list
(`docs/PHASE8_BRIEF.md` Phase 7 suggestion #6, "the grouping workspace
is missing half its actions") — this bug is the concrete mechanism
behind that suggestion, not a separate item.
