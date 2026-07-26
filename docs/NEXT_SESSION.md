# Do these before starting Phase 6

Written 2026-07-27 at the end of the Phase 5 session, by the agent that
implemented Phase 5. Ordered by dependency — 1 and 2 are cheap and make
3 and 4 trustworthy. Delete this file once all four are done.

## Which model to run these with

**Items 1–3: Sonnet 5 is fine.** They're mechanical — un-gitignore a
file, edit an import-linter contract, add a paragraph to CLAUDE.md,
drive a browser and check the disk. Item 1's normative-vs-illustrative
pass over PLAN.md is judgment-ish but narrow, and the two examples that
motivate it are already written down below. Item 3 wants patience and
literalness, not model horsepower.

**Item 4 needs a fresh context, and that constraint is not about model
choice.** The requirement stated in item 4 — *not the Phase 5 agent* —
is a stake conflict, not a capability gap. The Phase 5 agent wrote both
the implementation and the PROGRESS.md entry claiming completion while
`/rename` sat unopened; a reviewer sharing its reading of what §6
"obviously meant" will reproduce its blind spots. **Sonnet 5 in a
genuinely fresh session satisfies this. Sonnet 5 continuing the Phase 5
conversation does not, however the prompt is phrased.**

**One argument for Opus 5 on item 4 specifically:** it wrote PLAN.md.
Not for authority — it retains no memory of doing so — but the review's
core question is whether the tree satisfies the plan's *intent*, and §6
is dense prose where the line between a load-bearing constraint and an
unexecuted sketch is exactly what got missed. That's the one place the
extra headroom plausibly pays, and it's the item where a wrong answer is
most expensive, since it's meant to catch six phases of accumulated
drift.

**Cheap hedge:** run item 4 on Sonnet 5 in a fresh session. If the
output reads thin — divergences listed without the
unknowable-vs-knowable classification, or no willingness to say
"PROGRESS.md overclaims here" — re-run just that item on Opus 5. That
classification requirement is a decent smoke test for whether the review
actually engaged rather than summarized.

**Sequencing note:** nothing in Phase 6 depends on item 4. It is the
least urgent of the four and is more useful with fresh capacity than
rushed. Items 1–3 first is a reasonable split if budget is tight.

---

## 1. Commit `docs/PLAN.md` and tighten the import contract

**Why:** `docs/PLAN.md` is the source of truth and it is currently
gitignored. That means the contract can't be diffed against itself over
time, a fresh clone can't see it, and the only durable record of intent
is `docs/PROGRESS.md` — which is written after the fact by the same
agent that did the implementing. Self-graded.

**Do:**
- Remove `docs/PLAN.md` from `.gitignore` and commit it. Note that
  `CLAUDE.md`'s "Never commit" list currently names it — update that
  line in the same commit so the two don't contradict each other.
- Tighten `[tool.importlinter]` in `pyproject.toml` so the layering
  table in PLAN.md is machine-enforced.

**The specific gap this closes:** PLAN.md's table says `paths → domain`
only. The current `pyproject.toml` contract is coarser and *would have
permitted* `paths → db`. Phase 5 honored the stricter prose across
fifteen commits by choice — nothing would have caught a violation. Any
invariant you actually care about should be a failing check, not prose
an agent is trusted to remember.

**While editing PLAN.md's status, add a normative-vs-illustrative
convention.** `docs/PLAN.md` is written by Opus 5; implementation runs
on Sonnet 5. That split means the plan is authored at an altitude where
some statements are load-bearing contracts and others are sketches that
were never executed — and today the prose doesn't distinguish them.

§10 already does this right: it self-labels as "representative," which
is precisely why shipping `track_ids`/`group_id` instead of its stated
`album_id` was a documented decision rather than drift. §6 does *not*:
it states its function list and example templates flatly, with no
signal that the variable names had never been run against
`domain/fields.py`. Two of Phase 5's three deviations came out of that
one unmarked section.

Pick a light convention — a `(normative)` / `(illustrative)` tag, or a
short "examples in this section are unverified" note per section — and
apply it at minimum to §6's examples and any function/field list the
plan states without having executed. This converts a whole class of
surprise into pre-authorized latitude, and it costs one pass over the
plan.

---

## 2. Add the anti-re-planning rule to `CLAUDE.md`

**Why:** this is the direct fix for the drift pattern across phases.
At the start of the Phase 5 session, "implement phase 5" produced a
1354-line plan-mode document that restated PLAN.md §6 — and introduced
two factual errors §6 did not contain. That paraphrase then becomes a
competing spec, and the paraphrase is what gets implemented against.
Repeat over six phases and the original plan quietly stops being the
thing in force.

This was agent disposition, not tool behavior. Claude Code does not
require re-planning at the start of a phase. One line prevents it.

**Add to `CLAUDE.md`, under Workflow:**

> `docs/PLAN.md` is the plan. Do not write a per-phase planning
> document — no plan-mode restatement of a phase that PLAN.md already
> specifies. If a section is ambiguous or silent, note the ambiguity,
> decide it from the code and tests, and record the decision in the
> commit body and in `docs/PROGRESS.md`. Never paraphrase the plan into
> a second document that implementation then follows instead.

**Do not** adopt a pre-phase delta-approval gate (write a delta, wait
for accept/reject, then proceed). It was considered and rejected: it
cannot anticipate the deviations that actually occur — all three Phase 5
deviations were invisible before implementation started — and an
approved delta manufactures pressure to conform to a document written
while blind. It also contradicts CLAUDE.md's existing and correct rule
about not escalating implementation-level questions.

---

## 3. Click through `/rename` in a browser

**Why this is the one real gap, not a formality:** `RenameTracks.tsx`
typechecks and builds. Nobody has ever opened it. Every prior phase in
this project got a live click-through and each one found something
typechecking cannot — the clearest precedent is the Phase 1 `Checkbox`
bug, where `onClick` sat on the small icon box instead of the `<label>`,
so the natural larger click target silently did nothing.

**The path:**
1. `muzilla serve` against a scratch library containing a real
   multi-track album (not just the 1s fixtures — you want `%aunique`
   and collision detection to have something to chew on).
2. Catalog → select tracks → **Rename** → Preview → Stage →
   **Review & apply**.
3. Confirm the files actually moved on disk.
4. **Then Undo** → confirm they move back. No automated test covers
   `op="move"` composing with the inverse-changeset machinery
   end-to-end; this step is the only thing that proves it.

**Note for whoever runs this:** the previous session's sandboxed
dev-server launch was declined by the user, which is why this is still
open. Ask whether the objection was the sandbox specifically or the
approach, and offer to either run it non-sandboxed or hand over the
exact commands for the user to run themselves.

---

## 4. Run the deferred full-project drift review

**This was always the plan** — once, at the end, rather than
phase-by-phase, agreed explicitly because the app wasn't complete
enough for a per-phase review to mean anything. Phases 0–5 are now
done, so the scope is well-defined.

**Two constraints on how it runs:**

- **After step 1.** The reviewer should be reading a committed,
  diffable plan, not a local-only file.
- **Not the Phase 5 agent.** Phase 5's implementer also wrote Phase 5's
  `PROGRESS.md` entry, including the claim that the phase is complete
  while `/rename` had never been opened. That blind spot is structural,
  not incidental. A fresh agent with no stake, reading only PLAN.md
  against the actual tree, is worth strictly more here.

**Deliverable:** every divergence between PLAN.md and the tree,
classified as one of:
- **plan was wrong** — e.g. §6's example templates use `$albumartist`,
  a variable that never existed (canonical field is `album_artist`).
- **deliberate and documented** — e.g. §10's `POST /api/paths/preview
  {template, album_id}` shipped as `track_ids`/`group_id`, because the
  service supports arbitrary track sets and §10 self-labels as
  "representative."
- **undocumented drift** — the actual defect class. Anything that
  quietly became different with no commit body explaining why.

**The test to apply throughout:** deviation from the plan is not itself
a failure — the plan is a hypothesis, the code is the experiment.
*Undocumented* deviation is the failure. For each divergence, ask
whether a commit body explains it.

**Also do during this review:** prune `docs/PROGRESS.md`. It has to be
read end-to-end for this review anyway. It is 1100+ lines and
append-only, and it mixes three kinds of content that should be treated
differently:

- **Keep forever** — the numbered gotchas (1–17). Facts about the world
  that are not derivable from the repo and cost real time to learn:
  mutagen's WAV/AIFF dispatch-on-`.tags`, `create_all()` not building
  FTS5 virtual tables, import-linter's transitive `forbidden` contracts,
  `VComment.pop` raising where `MP4Tags.pop` doesn't, MP4 `cpil` being a
  scalar `bool`. Also the *why-not* design records, e.g. why per-field
  cross-source merging was rejected.
- **Drop or compute** — test counts, phase status, commit lists, file
  inventories. Already in git, and guaranteed to go stale. The file
  already carries the scar: a "Commits so far" block frozen at Phase 1's
  twelve commits, roughly 60 behind.
- **Demote** — Phase 1's "remaining work" section still reads as a task
  list with ✅ marks long after those tasks stopped being work.

Target shape: a gotchas-and-decisions ledger, not a status report.

---

## On the Opus-plans / Sonnet-implements split

`docs/PLAN.md` was written with Opus 5; implementation runs on Sonnet 5.
Worth stating plainly because it's easy to draw the wrong conclusion
from it.

**What it does explain.** Two of Phase 5's three deviations — §6's
`$albumartist` examples (the canonical field is `album_artist`) and the
`%time{$d,%Y}` lexer collision — are exactly what a planning model
produces at the right altitude without executing anything: plausible,
beets-conventional, internally coherent, and wrong in the one way only
running code reveals. This is not a defect in the planning model. It is
what planning is. The useful reframe: **a plan from a stronger model
isn't more executable, it's more confidently wrong where it can't
check.** Hence the normative-vs-illustrative convention in item 1.

**What it does not explain.** The re-planning that caused drift across
phases — "implement phase X" producing a fresh plan-mode document — was
not a model-mismatch effect. Sonnet didn't paraphrase §6 because it
couldn't read Opus's register; it paraphrased because nothing forbade it
and plan-mode was the path of least resistance. Opus would plausibly
have done the same absent the instruction. **Item 2 is the highest-
leverage fix and is model-independent.** Don't let the model-split
narrative displace it.

**What to watch for in item 4.** Classify each divergence as
*unknowable-at-plan-time* vs *knowable-but-missed*. If most are
unknowable, the fix is tightening the loop where implementation reports
back to the plan — not a better planning model. If many were knowable,
that's a genuinely different problem and worth surfacing.
