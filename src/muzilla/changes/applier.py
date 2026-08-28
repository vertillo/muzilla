"""Legacy shim: re-export native writer primitives for compatibility.

# ponytail: applier is now a thin shim; native writer owns file mutation.
# Upgrade path: update callers to import from muzilla.changes.writer directly,
# then remove this shim.
"""

from muzilla.changes.writer import (  # noqa: F401
    RECOVERY_RESTORED_MESSAGE,
    SourcePrecondition,
)

# Legacy ChangeSet appliers removed; native ReviewBundle path uses writer directly.


def apply_changeset(*_args, **_kwargs):  # type: ignore[no-untyped-def]
    raise NotImplementedError("ChangeSet applier removed: use ReviewBundle writer")


def recover_apply_journal(*_args, **_kwargs):  # type: ignore[no-untyped-def]
    # No-op for legacy recovery; native ReviewFileJournal recovery is via writer/reviews
    from dataclasses import dataclass

    @dataclass(frozen=True, slots=True)
    class RecoveryReport:
        reverted: int = 0
        confirmed_done: int = 0
        failed: int = 0

    return RecoveryReport()
