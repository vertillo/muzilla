# Full-project drift review — Phases 0–5

Run 2026-07-27, per `docs/NEXT_SESSION.md` item 4, against commit
`3fae00c`. Reviewer read `docs/PLAN.md` end-to-end and checked it
against the tree directly; `docs/PROGRESS.md` was read only afterward,
to compare its claims against what the code actually shows.

**Method.** Every divergence below was found by reading the plan and
then verifying against source, not by reading PROGRESS.md's account of
what was built. Each is classified per item 4's taxonomy:

- **plan was wrong** — the plan stated something unexecutable or false
- **deliberate and documented** — deviation with a commit body or code
  comment explaining why
- **undocumented drift** — quietly different, no explanation anywhere

The test applied throughout: *deviation from the plan is not the
failure; undocumented deviation is.*

---

## Verdict

**The tree is in substantially better shape than the review expected.**
82 commits, 574 tests passing, all four import-linter contracts kept,
`ruff`/`mypy --strict`/frontend `lint`+`typecheck`+`build` all clean.

The load-bearing constraints held. Every one of the plan's ★-marked
decisions survived six phases intact:

- **One release, one source** (§3) — no per-field merging leaked in
  anywhere. `CandidatePicker.tsx` carries a comment actively defending
  the boundary and naming `winnerOverrides` as rejected.
- **Flat-library grouping** (§7b) — no `dir_path` grouping input; the
  confidence-scored cascade with `grouping_basis` shipped as specified.
- **Nothing touches disk until a ChangeSet is applied** — held; the
  rename path routes through the same applier and journal.
- **Undo is a synthesized inverse ChangeSet, not a special mechanism**
  (§4) — verified live this session through five apply/undo round-trips
  including undo-of-undo.
- **Single-writer SQLite, cursor pagination only** — zero `.offset()`
  calls in `src/`.
- **`domain/fields.py` as canonical registry** — the `$albumartist`
  alias problem (below) was resolved *toward* the registry, not around it.

**Two findings are genuine undocumented drift** (§1, §2 below). The rest
is either the plan being wrong at a point it could not have checked, or
deliberate deferral that was written down properly.

**Classification counts:** 2 undocumented drift, 6 deliberate and
documented, 3 plan-was-wrong, 4 not-yet-due (Phase 6+ scope, listed only
to show they were checked and are not drift).

---

## Undocumented drift (the actual defect class)

### 1. OpenAPI→TS codegen was silently replaced by hand-written types

**Plan:** §9 — *"Type safety: OpenAPI → TS via `openapi-typescript` +
`openapi-fetch`, regenerated in CI with a failing diff check.
**Hand-written API types guarantee drift.**"* Risk #8 names
frontend/backend type drift as a genuine risk and cites this codegen as
its sole mitigation.

**Tree:** `frontend/src/lib/types.ts` is hand-written. No
`openapi-typescript` or `openapi-fetch` dependency exists in
`frontend/package.json`. No CI diff check exists. The file's header
comment says only *"Mirrors muzilla.api.schemas.* field-for-field."*

**Documentation:** none. Searched all 82 commit bodies — **the string
"openapi" does not appear in any of them.** The file was introduced in
`4c52b33` (Phase 1) and has been extended by hand in every frontend
commit since.

**Why this one matters most.** It is not a missing feature; it is a
named risk whose stated mitigation was dropped without a decision being
recorded. The plan predicted the exact failure mode and the tree adopted
it anyway. Every future frontend commit widens the surface by hand.

**Classification: knowable at plan time.** Nothing about running the
code made codegen impractical — it was simply never set up, and the
faster path (hand-write the interface you need right now) was taken 12
times without anyone writing down that a decision was being made.

**Recommendation:** either wire up `openapi-typescript` with the CI
diff check as planned, or record an explicit decision in PLAN.md that
hand-written types are accepted and why. The current state — plan says
one thing, tree does another, nothing acknowledges the gap — is the
worst of the three options.

### 2. `strip` relocated from `tags/` to `services/` with no rationale

**Plan:** §Package Structure lists `tags/  # reader.py, writer.py,
mapping.py, strip.py`.

**Tree:** `src/muzilla/services/strip.py`. No `tags/strip.py` exists.

**Documentation:** partial and insufficient. Commit `1e1de77` describes
what `strip.py` *does* (*"stages a strip_tags ChangeSet clearing
domain.fields.default_strip_fields()"*) but never notes that the plan
placed it a layer lower, nor why it moved.

**Assessment.** The move is very likely *correct* — the module stages a
ChangeSet, which requires `changes/`, and `tags/` may only import
`domain` under the layering contract, so a literal `tags/strip.py`
could not have done this job. That is a good reason. It just was never
written down, so a reader comparing plan to tree finds an unexplained
discrepancy in the one document that is supposed to be authoritative.

**Classification: unknowable at plan time** (the layering conflict only
becomes visible once `strip` is understood as a ChangeSet producer), but
**the omission of the note was knowable** — the constraint was obvious
to whoever moved it.

---

## Plan was wrong

### 3. §6's example templates use `$albumartist`, a field that never existed

The canonical field in `domain/fields.py` is `album_artist`. §6's
example templates and the shipped `config/defaults.yaml` both say
`$albumartist`.

**Resolved well.** `paths/render.py:27-35` implements a beets-style
alias map so both spellings resolve to the same value, with a comment
citing §6 explicitly and naming `domain/fields.py` as the single source
of truth. This is the right fix: it honors the plan's examples without
letting a second field-name universe exist.

**Classification: unknowable at plan time.** This is exactly the
failure mode `docs/NEXT_SESSION.md` predicts of a plan written at
altitude without executing anything — plausible, beets-conventional,
and wrong in the one way only running code reveals.

### 4. `%time{$date,%Y}` collides with the engine's own `%func{` syntax

A bare `%Y` inside a template argument is indistinguishable from an
attempted `%Y{...}` function call to the lexer, which has no
per-function argument semantics.

**Resolved and documented** in `23b36ce`'s commit body, which explains
the collision and the fix (require `%%` escaping, reusing the escape the
engine already defines for a literal `%`) rather than special-casing the
lexer per function.

**Classification: unknowable at plan time.** Surfaced only by writing
tests against real render calls.

### 5. `paths/compile.py` vs shipped `paths/compiler.py`, plus five unplanned modules

§Package Structure names `lexer.py, parser.py, compile.py, functions.py,
sanitize.py`. Shipped: `compiler.py` (renamed) plus `ast.py`,
`collisions.py`, `context.py`, `errors.py`, `query.py`, `render.py`.

**Not drift.** The extra modules are all plan-mandated *features* (§6's
collision detection, query-keyed overrides, the `DisambiguationResolver`
Protocol, precise error offsets) that simply needed their own files. The
`context.py` split in particular is documented at length in `bd74563`,
citing the layering table as the reason the DB-backed resolver lives in
`services/paths.py` instead. `compile.py`→`compiler.py` is a trivial
rename (`compile` shadows a Python builtin).

**Classification: plan was under-specified, not wrong.**

---

## Deliberate and documented

These are deviations with a real explanation on record. Listed to
confirm the review checked them, and because the quality of the record
is itself the thing being audited.

| # | Divergence | Where documented |
|---|---|---|
| 6 | No `/settings` page; template editor deferred | `44b2183` body — names §6's requirement, explains why `/rename`'s inline preview covers the immediate need, notes no `/settings` exists for *any* config |
| 7 | `settings` + `users` tables absent (§5 names both) | `db/models.py:11` — *"settings/users still land whenever DB-backed config/auth actually needs them"*; auth reads the password from env, so no user table is needed yet |
| 8 | Network-guard fixture scoped to `tests/providers/`, not global as §Testing specifies | `tests/providers/conftest.py:1-7` — explains that a repo-wide guard would interfere with `tests/api/`'s in-process `TestClient`, which never goes over real HTTP |
| 9 | Catalog facets computed client-side over loaded pages only | `4c52b33` body — *"client-side facets … over the loaded pages"* stated plainly |
| 10 | Idempotency cache is in-process, not DB-backed | `api/idempotency.py:1-13` — explains the single-container/single-writer tradeoff and when to revisit |
| 11 | Jobs router pulled forward into Phase 4 | PLAN.md §Phase 4 carries an explicit implementation-order note added at the time |

**Note on #10:** the *implementation* (in-process cache) is well
documented, but §10 says *"All mutating endpoints accept
`Idempotency-Key`"* and only `changesets.py` does —
`tracks/groups/paths/imports/jobs` do not. That narrowing is not
mentioned anywhere. It is a smaller instance of the same defect as
finding #1: the how is documented, the scope reduction is not.

---

## Checked, not yet due (Phase 6+)

Listed so a future reviewer does not re-flag them: `audio/replaygain.py`,
`audio/probe.py`, `pipeline/enrich.py`, art/binary diff UI, the
`/api/blobs/{id}` endpoint, global `GET /api/events` SSE, and the `/`
Dashboard route (§9 lists it; the app currently redirects `/` →
`/catalog`). Blob storage is implemented and tested but has no producer
until Phase 6's art pipeline — correctly noted in PROGRESS.md.

`PUT /api/changesets/{id}/candidate` (§10) also does not exist;
re-staging is served by `POST /api/groups/{id}/stage` with a
`{source, ref_id}` body. §10 self-labels as *"representative"*, so this
falls under the same pre-authorized latitude as the
`track_ids`/`group_id` case already recorded in `NEXT_SESSION.md`.

---

## Testing-strategy gaps

Distinct from plan-vs-tree drift, and worth separating because the plan
calls one of these *"the highest-value test asset in the project."*

1. **No Hypothesis property tests exist.** `hypothesis>=6.115` is a
   declared dev dependency with **zero usages** in `tests/`. §Testing
   specifies property tests in two places: the tag round-trip invariant
   (`write(read(f) ⊕ changes) → read → assert changes present ∧
   everything else unchanged`, explicitly to catch the ID3v2.3-vs-2.4
   and Vorbis-multi-value bugs "that otherwise ship") and fuzzing the
   template parser. **Undocumented** — no commit body mentions
   deferring them.

2. **Scoring corpus is 13 scenarios, not ~50.** 9 album + 4 singleton,
   against §Testing's *"~50 real-world album scenarios"* for what it
   calls the project's highest-value test asset. The harness exists and
   works (`tests/matching/test_scoring_corpus.py`); only the corpus is
   thin. Partially excusable — real-world scenarios need real-world
   data — but the shortfall is not recorded anywhere.

3. **Format matrix is missing WavPack, WMA, and DSF.** §Testing names
   ten formats; seven have fixtures. `mutagen` supports all ten. Minor,
   but undocumented.

4. **No beets template-compatibility suite.** §Testing calls for
   *"porting beets' own template test cases as a compatibility suite."*
   `tests/paths/` has thorough hand-written tests instead. Reasonable
   substitution; not recorded.

---

## Answering item 4's diagnostic question

`docs/NEXT_SESSION.md` asks whether divergences are mostly
*unknowable-at-plan-time* or *knowable-but-missed*, because the two
imply different fixes.

**The split is roughly even, and that is the informative result.**

- **Unknowable** (#3 `$albumartist`, #4 `%time`, #5 module layout, #2's
  underlying layering conflict): the plan was confidently wrong exactly
  where it could not execute. These were all caught and fixed well
  during implementation — the loop worked.
- **Knowable-but-missed** (#1 OpenAPI codegen, the #10 scope narrowing,
  and all four testing gaps): nothing about running the code made these
  hard. They are places where the faster path was taken and the decision
  simply was not written down.

**The implication:** a better planning model would not have prevented
the knowable half, and the unknowable half was already handled correctly.
Neither category argues for changing how planning is done. Both argue
for the same narrow fix — **when implementation departs from a stated
plan requirement, the commit body must say so.** Most commits here do
this genuinely well (`44b2183`, `bd74563`, `23b36ce`, `1e1de77` are all
models of it). The failures cluster in one place: **things that were
never started at all** (codegen, property tests, corpus scale) produce
no commit, and therefore no commit body, and therefore no record. A
deferral that is never begun leaves no trace unless someone writes one
deliberately.

That is the structural gap worth closing, and it is not a model-choice
problem.

---

## On PROGRESS.md's reliability

Item 4 flagged that Phase 5's implementer wrote Phase 5's completion
claim while `/rename` had never been opened. That specific overclaim was
real, and the same session's commit body (`44b2183`) was in fact honest
about it — *"Live browser click-through was not performed this session"*.
So the commit record was accurate while the status document overclaimed.

Checked against the tree, PROGRESS.md's phase-completion claims are
otherwise **substantially accurate** — the ✅ marks correspond to code
that exists and passes. Its real problem is the one item 4 identified:
it mixes durable knowledge with status reporting that goes stale. The
`## Commits so far` block frozen at Phase 1's twelve commits (~70
behind) is the clearest symptom.

Pruned in the companion commit to this review.
