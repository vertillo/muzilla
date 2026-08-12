"""Checks for release-candidate workflow boundaries.

These assertions deliberately inspect the workflow text: GitHub Actions executes
the YAML outside pytest, so the ordering and ref constraints need a local,
deterministic guard against accidental weakening.
"""

from pathlib import Path

WORKFLOWS = Path(__file__).parents[2] / ".github" / "workflows"


def _workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text()


def test_ci_can_be_called_for_an_explicit_commit() -> None:
    workflow = _workflow("ci.yml")

    assert "workflow_call:" in workflow
    assert "ref:" in workflow
    assert "ref: ${{ inputs.ref || github.sha }}" in workflow


def test_ci_runs_backup_restore_after_compose_smoke_on_the_built_image() -> None:
    workflow = _workflow("ci.yml")

    assert "MUZILLA_TEST_IMAGE: muzilla:ci" in workflow
    assert "python tests/container/compose_smoke.py" in workflow
    assert "python tests/container/backup_restore_smoke.py" in workflow

    compose_smoke = workflow.index("python tests/container/compose_smoke.py")
    backup_restore_smoke = workflow.index("python tests/container/backup_restore_smoke.py")
    assert compose_smoke < backup_restore_smoke


def test_release_gates_the_requested_sha_before_versioning() -> None:
    workflow = _workflow("release.yml")

    assert "source_sha:" in workflow
    assert "uses: ./.github/workflows/ci.yml" in workflow
    assert "ref: ${{ inputs.source_sha }}" in workflow
    assert "needs: verify-source" in workflow
    assert 'git merge-base --is-ancestor "$RELEASE_SHA" origin/main' in workflow
    assert "semantic-release version --no-vcs-release" in workflow


def test_publish_only_pushes_the_smoke_tested_tag_candidate() -> None:
    workflow = _workflow("publish.yml")

    assert "workflow_run:" in workflow
    assert "workflows: [CI]" in workflow
    assert "github.event.workflow_run.head_sha" in workflow
    assert "github.event.workflow_run.conclusion == 'success'" in workflow
    assert "github.event.workflow_run.event == 'push'" in workflow
    assert "git rev-list -n 1 \"$RELEASE_TAG\"" in workflow
    assert "git merge-base --is-ancestor \"$RELEASE_SHA\" origin/main" in workflow
    assert "workflow_dispatch:" not in workflow
    assert "load: true" in workflow
    assert "candidate-${RELEASE_SHA}" in workflow
    assert "MUZILLA_TEST_IMAGE: ${{ steps.candidate.outputs.image }}" in workflow
    assert "python -m unittest tests.container.test_runtime tests.container.test_build_guard" in workflow
    assert "python tests/container/compose_smoke.py" in workflow
    assert "python tests/container/backup_restore_smoke.py" in workflow
    assert "docker image tag \"${CANDIDATE_IMAGE}\" \"${published_tag}\"" in workflow
    assert "docker image push \"${published_tag}\"" in workflow

    runtime_smoke = workflow.index("python -m unittest tests.container.test_runtime")
    compose_smoke = workflow.index("python tests/container/compose_smoke.py")
    backup_restore_smoke = workflow.index("python tests/container/backup_restore_smoke.py")
    login = workflow.index("uses: docker/login-action@v3")
    push = workflow.index("docker image push \"${published_tag}\"")
    assert runtime_smoke < login
    assert compose_smoke < login
    assert compose_smoke < backup_restore_smoke < login
    assert login < push
