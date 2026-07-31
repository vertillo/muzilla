# Documentation map

## Current recovery documents

Read in this order when changing the application:

1. [Recovery execution guide](recovery-execution-guide.md) — next steps, model/effort
   selection and copy-ready prompts.
2. [Recovery plan](recovery-plan.md) — target architecture, migration and slice gates.
3. [UX redesign](ux-redesign.md) — target journeys, navigation and interaction contracts.
4. [Issues matrix](issues-matrix.md) — canonical status of requested and discovered
   issues.
5. [Recovery audit](recovery-audit.md) — evidence, current architecture and root causes.

Repository-wide agent instructions live in [`AGENTS.md`](../AGENTS.md).

## Operational documentation

- [`README.md`](../README.md) — product warning, Docker deployment and configuration.
- [`CONTRIBUTING.md`](../CONTRIBUTING.md) — local setup and verification.

## Legacy implementation references

- [PLAN.md](PLAN.md) — historical implementation plan for the current pre-recovery
  architecture. Many code comments link to its sections; it is no longer normative.
- [PROGRESS.md](PROGRESS.md) — historical decisions/gotchas ledger. Verify every product
  decision against the recovery documents before reusing it.
- [KNOWN_BUGS.md](KNOWN_BUGS.md) — legacy bug notes retained for code/test references;
  active tracking is in the issues matrix.

Do not add per-slice plans, generated chat transcripts or scratch prompts under `docs/`.
Update the execution guide and issues matrix instead.
