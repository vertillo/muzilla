# Known bugs — pending a fix plan

Found during Phase 8 frontend work (`docs/PLAN.md` §12e) while writing
Playwright coverage and, later, honest error states against the real
app. This file exists so a future planning session doesn't have to
rediscover them from `docs/PROGRESS.md`'s chronological log — once a
fix is planned and landed, delete the relevant entry (or the whole
file) rather than letting it go stale.

---

## Catalog search 500s on FTS5 special characters (found while fixing an earlier empty-state bug)

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
