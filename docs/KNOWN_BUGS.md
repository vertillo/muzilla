# Known bugs — pending a fix plan

Found during Phase 8 frontend work (`docs/PLAN.md` §12e) while writing
Playwright coverage and, later, honest error states against the real
app. This file exists so a future planning session doesn't have to
rediscover them from `docs/PROGRESS.md`'s chronological log — once a
fix is planned and landed, delete the relevant entry (or the whole
file) rather than letting it go stale.

---

## 1. FIXED (step 5.4) — Catalog's "no results" empty state showed the wrong message for a zero-result search

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

**Fixed in:** `frontend/src/pages/Catalog.tsx` now derives a
`hasActiveSearchOrFilter` flag from the actual UI state (search text
and every facet) instead of the search-scoped `total`, and picks the
empty-state message from that. `e2e/tests/catalog.spec.ts`'s test
updated to assert the corrected message.

---

## 2. Catalog search 500s on FTS5 special characters (found while fixing #1)

**Where:** `src/muzilla/db/repo/tracks.py::_base_query` — binds the raw
search string directly into `tracks_fts MATCH :q` with no escaping.

**What's wrong:** SQLite FTS5's `MATCH` right-hand side is a query
language, not a literal string — `-`, `"`, `*`, `:`, and the bareword
operators `AND`/`OR`/`NOT`/`NEAR` are all syntactically significant.
An ordinary search containing a hyphen (`"post-rock"`, `"co-op"`,
`"24-bit"`, or, as found here, `"nonexistent-search-term"`) can throw
a `sqlite3.OperationalError` inside the MATCH expression, which
propagates as a real `500 Internal Server Error` from `GET
/api/tracks?q=...` — not "zero results," an actual crash on
unremarkable user input. Reproduced directly against a throwaway FTS5
table:

```python
>>> conn.execute('SELECT * FROM t WHERE t MATCH ?', ('nonexistent-search-term',)).fetchall()
sqlite3.OperationalError: no such column: search
```

**Why it happens:** FTS5 parses `word-word` as `word -word` (the `-`
is a NOT-prefix on the following term) unless the whole phrase is
double-quoted or the token is otherwise escaped before binding.
Nothing between the `search` box in `Catalog.tsx` and the `MATCH`
clause quotes or sanitizes the query string.

**How it was found:** Discovered as a side effect of `docs/PLAN.md`
§12e step 5.4 (distinguishing error from empty on every list screen) —
adding a real error branch to Catalog surfaced that the existing
`catalog.spec.ts` search-for-a-nonexistent-term test was actually
hitting a 500, not an empty result set, which the old empty-state-only
UI had been silently swallowing as "no results" the whole time.

**Fix sketch (not yet applied):** Wrap each whitespace-separated token
of `q` in double quotes before binding (`'"post-rock"'` instead of
`post-rock`), which makes FTS5 treat the content literally rather than
as query syntax — verify against a hyphen, a bare `"`, and a `:`
before considering it fixed, since each triggers this differently.

---

## 3. None of the five `grouping_correction` actions apply themselves

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
