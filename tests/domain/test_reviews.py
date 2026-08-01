from __future__ import annotations

import pytest

from muzilla.domain.reviews import BundleState, InvalidBundleTransition, validate_transition


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (BundleState.PREPARING, BundleState.READY),
        (BundleState.PREPARING, BundleState.NEEDS_ATTENTION),
        (BundleState.READY, BundleState.NEEDS_ATTENTION),
        (BundleState.NEEDS_ATTENTION, BundleState.READY),
        (BundleState.READY, BundleState.APPLYING),
        (BundleState.APPLYING, BundleState.APPLIED),
        (BundleState.APPLYING, BundleState.PARTIALLY_APPLIED),
        (BundleState.APPLYING, BundleState.FAILED),
    ],
)
def test_review_bundle_state_machine_accepts_declared_transitions(
    current: BundleState, target: BundleState
) -> None:
    validate_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (BundleState.PREPARING, BundleState.APPLIED),
        (BundleState.READY, BundleState.APPLIED),
        (BundleState.APPLYING, BundleState.READY),
        (BundleState.APPLIED, BundleState.READY),
        (BundleState.DISCARDED, BundleState.PREPARING),
    ],
)
def test_review_bundle_state_machine_rejects_skipped_or_terminal_transitions(
    current: BundleState, target: BundleState
) -> None:
    with pytest.raises(InvalidBundleTransition):
        validate_transition(current, target)
