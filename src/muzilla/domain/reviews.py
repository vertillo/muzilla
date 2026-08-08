"""Review bundle lifecycle and operation vocabulary.

This module is deliberately persistence-free. Database constraints/triggers mirror
these values, while services provide the transactional API used by application flows.
"""

from __future__ import annotations

from enum import StrEnum


class BundleState(StrEnum):
    PREPARING = "preparing"
    READY = "ready"
    NEEDS_ATTENTION = "needs_attention"
    APPLYING = "applying"
    APPLIED = "applied"
    PARTIALLY_APPLIED = "partially_applied"
    FAILED = "failed"
    DISCARDED = "discarded"


class OperationKind(StrEnum):
    SET_TAG = "set_tag"
    WRITE_LYRICS = "write_lyrics"
    EMBED_ART = "embed_art"
    REMOVE_ART = "remove_art"
    MOVE_FILE = "move_file"
    SET_REPLAY_GAIN = "set_replay_gain"
    GROUPING_CORRECTION = "grouping_correction"


ACTIVE_BUNDLE_STATES = frozenset(
    {
        BundleState.PREPARING,
        BundleState.READY,
        BundleState.NEEDS_ATTENTION,
        BundleState.APPLYING,
    }
)
TERMINAL_BUNDLE_STATES = frozenset(BundleState) - ACTIVE_BUNDLE_STATES

_TRANSITIONS: dict[BundleState, frozenset[BundleState]] = {
    BundleState.PREPARING: frozenset(
        {
            BundleState.READY,
            BundleState.NEEDS_ATTENTION,
            BundleState.FAILED,
            BundleState.DISCARDED,
        }
    ),
    BundleState.READY: frozenset(
        {
            BundleState.NEEDS_ATTENTION,
            BundleState.APPLYING,
            BundleState.DISCARDED,
        }
    ),
    BundleState.NEEDS_ATTENTION: frozenset(
        {
            BundleState.READY,
            BundleState.APPLYING,
            BundleState.DISCARDED,
        }
    ),
    BundleState.APPLYING: frozenset(
        {
            BundleState.APPLIED,
            BundleState.PARTIALLY_APPLIED,
            BundleState.FAILED,
        }
    ),
    BundleState.APPLIED: frozenset(),
    # Retry resumes the same frozen ApplyRun/manifest.  Decisions and proposal content
    # remain immutable; only failed per-file work is eligible to run again.
    BundleState.PARTIALLY_APPLIED: frozenset({BundleState.APPLYING}),
    BundleState.FAILED: frozenset({BundleState.APPLYING}),
    # Archiving a rejected proposal is reversible: editing a decision reopens the
    # same stable review rather than creating a replacement inbox row.
    BundleState.DISCARDED: frozenset({BundleState.READY, BundleState.NEEDS_ATTENTION}),
}


class InvalidBundleTransition(ValueError):
    pass


def validate_transition(current: BundleState, target: BundleState) -> None:
    """Raise unless ``current -> target`` is an explicit lifecycle edge.

    Repeating the same target is an idempotent no-op rather than a transition.
    """
    if current == target:
        return
    if target not in _TRANSITIONS[current]:
        raise InvalidBundleTransition(f"invalid review bundle transition: {current} -> {target}")
