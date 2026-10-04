# Completion matrix

The original implementation backlog in this file is exhausted. The active remediation
backlog is [code-review-2026-10-04.md](code-review-2026-10-04.md): use R01–R30 directly
for goals under [AGENTS.md](../AGENTS.md), without copying them into this table.
An empty table does not certify production readiness while review findings remain open.

This matrix contains only unfinished work from the original implementation backlog. Product
decisions belong in [product-spec.md](product-spec.md); a resolved decision row is removed
after its decision is recorded in normative documentation and any remaining implementation is
represented by an actionable row. A finished item is removed only after its acceptance
evidence exists. Muzilla is fully ready only when this file has no actionable rows and every
gate in
[production-readiness.md](production-readiness.md) passes on the candidate revision.

Risk uses S1 for core correctness or a potentially unsafe boundary, S2 for an important
workflow gap, and S3 for usability, maintainability, or additional assurance.

| ID   | Type | Current state and why unfinished | Expected final behavior and acceptance | Relevant areas | Dependencies | Risk |
| ---- | ---- | ---------------------------------- | -------------------------------------- | ---------------- | ------------ | ---: |
