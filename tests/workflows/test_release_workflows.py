"""Checks for release-candidate workflow boundaries.

These assertions deliberately inspect the workflow text: GitHub Actions executes
the YAML outside pytest, so the ordering and ref constraints need a local,
deterministic guard against accidental weakening.
"""

import subprocess
from pathlib import Path
from typing import Any

import yaml

WORKFLOWS = Path(__file__).parents[2] / ".github" / "workflows"


def _workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text()


def _workflow_steps(name: str) -> list[dict[str, Any]]:
    document: dict[str, Any] = yaml.safe_load(_workflow(name))
    return [step for job in document["jobs"].values() for step in job["steps"]]


def _workflow_run_scripts(name: str) -> list[str]:
    scripts: list[str] = []
    for step in _workflow_steps(name):
        script = step.get("run")
        if isinstance(script, str):
            scripts.append(script)
    return scripts


def test_ci_and_publish_run_scripts_pass_bash_syntax_check() -> None:
    for workflow_name in ("ci.yml", "publish.yml"):
        for script in _workflow_run_scripts(workflow_name):
            for line in script.splitlines():
                assert not line.rstrip().endswith("\\\\"), (
                    f"{workflow_name} has a doubled shell continuation: {line!r}"
                )
            result = subprocess.run(
                ["bash", "-n"],
                input=script,
                capture_output=True,
                text=True,
                check=False,
            )
            assert result.returncode == 0, (
                f"invalid bash in {workflow_name}: {result.stderr}\n{script}"
            )


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
    assert "python tests/container/reset_review_smoke.py" in workflow

    compose_smoke = workflow.index("python tests/container/compose_smoke.py")
    backup_restore_smoke = workflow.index("python tests/container/backup_restore_smoke.py")
    reset_review_smoke = workflow.index("python tests/container/reset_review_smoke.py")
    assert compose_smoke < backup_restore_smoke < reset_review_smoke


def test_release_gates_the_requested_sha_before_versioning() -> None:
    workflow = _workflow("release.yml")

    assert "source_sha:" in workflow
    assert "uses: ./.github/workflows/ci.yml" in workflow
    assert "ref: ${{ inputs.source_sha }}" in workflow
    assert "needs: verify-source" in workflow
    shell_continuations = workflow.replace(" \\\n            ", " ")
    assert (
        'git merge-base --is-ancestor "$RELEASE_SHA" refs/remotes/origin/main'
        in shell_continuations
    )
    assert "semantic-release version --no-vcs-release" in workflow
    # The release job checks out with persist-credentials: false, so the
    # source guard must validate the locally fetched origin/main ref
    # fail-closed without an unauthenticated later fetch.
    release_job = workflow.split("needs: verify-source", 1)[1]
    assert "git fetch origin main" not in release_job
    assert "git fetch origin" not in release_job
    assert "git show-ref --verify --quiet refs/remotes/origin/main" in release_job
    assert '"$(git symbolic-ref --quiet HEAD)" != "refs/heads/main"' in release_job


def test_release_checks_out_main_and_guards_the_exact_sha() -> None:
    workflow = _workflow("release.yml")

    assert "ref: main" in workflow
    assert (
        "ref: ${{ inputs.source_sha }}"
        not in workflow.split("needs: verify-source")[1].split("Reject a source SHA")[0]
    )
    assert '"$(git rev-parse HEAD)" != "$RELEASE_SHA"' in workflow


def test_release_uses_builtin_token_and_no_pat() -> None:
    workflow = _workflow("release.yml")

    # No manually managed PAT anywhere in the release chain.
    assert "RELEASE_TOKEN" not in workflow
    release_job = workflow.split("needs: verify-source", 1)[1]
    checkout = release_job.split("Reject a source SHA", 1)[0]
    assert "token: ${{ secrets.GITHUB_TOKEN }}" in checkout
    assert "persist-credentials: false" in checkout
    # semantic-release authenticates its own remote from the built-in token.
    semantic_step = release_job.split("Run semantic-release", 1)[1]
    assert "GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}" in semantic_step
    assert "semantic-release version --no-vcs-release" in semantic_step
    # No actor override or origin rewrite: the built-in token authenticates
    # semantic-release directly.
    assert "GITHUB_ACTOR" not in release_job
    assert "git remote set-url origin" not in release_job
    # Never print the secret: no echo of the token in the release job.
    for line in release_job.splitlines():
        assert "echo ${GH_TOKEN}" not in line
        assert "echo $GH_TOKEN" not in line
        lowered = line.strip().lower()
        assert "echo" not in lowered or "gh_token" not in lowered


def test_release_records_exact_tag_and_invokes_publish_reusably() -> None:
    workflow = _workflow("release.yml")

    release_job = workflow.split("needs: verify-source", 1)[1]
    # The release job must bind Publish to the exact created tag/SHA.
    assert "release_tag: ${{ steps.record.outputs.tag }}" in workflow
    assert "release_sha: ${{ steps.record.outputs.sha }}" in workflow
    record_step = release_job.split("Record the created release tag", 1)[1]
    assert "git describe --tags --exact-match HEAD" in record_step
    assert '[[ "$TAG" =~ ^v[0-9]+\\.[0-9]+\\.[0-9]+$ ]]' in record_step
    assert 'git rev-list -n 1 "$TAG"' in record_step
    # Publish runs in the same audited chain as a reusable workflow with the
    # recorded inputs, not via a tag-push trigger that GITHUB_TOKEN cannot
    # start. Publish additionally waits for the generated-candidate CI gate
    # (verify-release), so the exact tagged SHA passed full CI, not only the
    # pre-generation source SHA.
    publish_job = workflow.split("publish:", 1)[1]
    assert "needs: [release, verify-release]" in publish_job
    assert "uses: ./.github/workflows/publish.yml" in publish_job
    assert "release_tag: ${{ needs.release.outputs.release_tag }}" in publish_job
    assert "release_sha: ${{ needs.release.outputs.release_sha }}" in publish_job
    # Minimal-secret design: no secret (named or inherited) is forwarded
    # into Publish; it authenticates with the automatic per-run token.
    assert "secrets: inherit" not in publish_job
    assert "secrets:" not in publish_job
    assert "packages: write" in publish_job


def test_release_revalidates_generated_candidate_before_publish() -> None:
    workflow = _workflow("release.yml")

    # The generated release commit (version + changelog) differs from the
    # pre-generation source SHA, so the exact recorded SHA must pass the
    # same reusable CI gate before Publish may run.
    assert "verify-release:" in workflow
    verify_job = workflow.split("verify-release:", 1)[1].split("publish:", 1)[0]
    assert "uses: ./.github/workflows/ci.yml" in verify_job
    assert "ref: ${{ needs.release.outputs.release_sha }}" in verify_job
    assert "needs: release" in verify_job
    assert "contents: read" in verify_job
    assert "contents: write" not in verify_job
    # Fail-closed ordering: generated candidate gate sits between the
    # versioning job and publication.
    assert workflow.index("verify-release:") > workflow.index("release_tag:")
    assert workflow.index("publish:") > workflow.index("verify-release:")
    assert workflow.index("needs: [release, verify-release]") > workflow.index("verify-release:")


def test_verify_source_runs_with_read_only_contents() -> None:
    workflow = _workflow("release.yml")

    verify_job = workflow.split("verify-source:", 1)[1].split("release:", 1)[0]
    assert "uses: ./.github/workflows/ci.yml" in verify_job
    assert "contents: read" in verify_job
    assert "contents: write" not in verify_job


def test_publish_only_pushes_the_smoke_tested_tag_candidate() -> None:
    workflow = _workflow("publish.yml")

    # Reusable-only: no push, workflow_run, or dispatch trigger can publish
    # an arbitrary ref outside the audited Release chain.
    assert "workflow_call:" in workflow
    assert "release_tag:" in workflow
    assert "release_sha:" in workflow
    assert "workflow_run:" not in workflow
    assert "workflows: [CI]" not in workflow
    assert "github.event.workflow_run" not in workflow
    assert "workflow_dispatch:" not in workflow
    assert "on:\n  push" not in workflow
    assert "RELEASE_TOKEN" not in workflow
    # Automatic per-run token only: GHCR login and the release step use
    # github.token, so no caller-passed secret is required or read.
    assert "password: ${{ github.token }}" in workflow
    assert "secrets.GITHUB_TOKEN" not in workflow
    assert "GH_TOKEN: ${{ github.token }}" in workflow
    assert "ref: ${{ inputs.release_sha }}" in workflow
    assert '[[ "$RELEASE_SHA" =~ ^[0-9a-f]{40}$ ]]' in workflow
    assert '[[ "$RELEASE_TAG" =~ ^v[0-9]+\\.[0-9]+\\.[0-9]+$ ]]' in workflow
    assert 'git rev-list -n 1 "$RELEASE_TAG"' in workflow
    assert 'git merge-base --is-ancestor "$RELEASE_SHA" origin/main' in workflow
    assert "load: true" in workflow
    assert "candidate-${RELEASE_SHA}" in workflow
    assert "MUZILLA_TEST_IMAGE: ${{ steps.candidate.outputs.image }}" in workflow
    assert (
        "python -m unittest tests.container.test_runtime tests.container.test_build_guard"
        in _workflow_run_scripts("publish.yml")
    )
    assert "tests.container.test_runtime" in workflow
    assert "tests.container.test_build_guard" in workflow
    assert "python tests/container/compose_smoke.py" in workflow
    assert "python tests/container/backup_restore_smoke.py" in workflow
    assert "python tests/container/reset_review_smoke.py" in workflow
    assert 'docker image tag "${CANDIDATE_IMAGE}" "${published_tag}"' in workflow
    assert 'docker image push "${published_tag}"' in workflow

    steps = _workflow_steps("publish.yml")
    publish_script = next(
        step["run"]
        for step in steps
        if step.get("name") == "Publish the smoke-tested candidate image"
    )
    assert publish_script == (
        'for published_tag in "ghcr.io/${GITHUB_REPOSITORY,,}:$VERSION" '
        '"ghcr.io/${GITHUB_REPOSITORY,,}:$MINOR" '
        '"ghcr.io/${GITHUB_REPOSITORY,,}:latest"; do\n'
        '  docker image tag "${CANDIDATE_IMAGE}" "${published_tag}"\n'
        '  docker image push "${published_tag}"\n'
        "done\n"
    )
    runtime_command = (
        "python -m unittest tests.container.test_runtime tests.container.test_build_guard"
    )
    runtime_smoke = next(i for i, step in enumerate(steps) if step.get("run") == runtime_command)
    compose_smoke = next(
        i
        for i, step in enumerate(steps)
        if step.get("run") == "python tests/container/compose_smoke.py"
    )
    backup_restore_smoke = next(
        i
        for i, step in enumerate(steps)
        if step.get("run") == "python tests/container/backup_restore_smoke.py"
    )
    reset_review_smoke = next(
        i
        for i, step in enumerate(steps)
        if step.get("run") == "python tests/container/reset_review_smoke.py"
    )
    login = next(i for i, step in enumerate(steps) if step.get("name") == "Log in to GHCR")
    publish = next(
        i
        for i, step in enumerate(steps)
        if step.get("name") == "Publish the smoke-tested candidate image"
    )
    assert runtime_smoke < login
    assert compose_smoke < login
    assert compose_smoke < backup_restore_smoke < reset_review_smoke < login
    assert login < publish


def test_publish_authorizes_the_main_release_caller_fail_closed() -> None:
    workflow = _workflow("publish.yml")

    # Caller provenance is enforced on server-set github context (not caller
    # inputs): only the top-level main-branch Release workflow dispatched by
    # an operator may proceed; every other caller, ref, or event exits 1.
    assert "Authorize the Release caller" in workflow
    assert "CALLER_WORKFLOW_REF: ${{ github.workflow_ref }}" in workflow
    assert "CALLER_REF: ${{ github.ref }}" in workflow
    assert "CALLER_EVENT: ${{ github.event_name }}" in workflow
    authorize = workflow.split("Authorize the Release caller", 1)[1]
    authorize = authorize.split("uses: actions/checkout@v4", 1)[0]
    assert ".github/workflows/release.yml@refs/heads/main" in authorize
    assert '"$CALLER_REF" != "refs/heads/main"' in authorize
    assert '"$CALLER_EVENT" != "workflow_dispatch"' in authorize
    assert authorize.count("exit 1") >= 3
    authorize_script = next(
        step["run"]
        for step in _workflow_steps("publish.yml")
        if step.get("name") == "Authorize the Release caller"
    )
    assert 'if [[ "$CALLER_WORKFLOW_REF" != "$EXPECTED" ]]; then' in authorize_script
    assert (
        'echo "refusing to publish: caller $CALLER_WORKFLOW_REF != $EXPECTED" >&2'
        in authorize_script
    )
    # The authorization gate runs before any checkout, image, or release
    # mutation in the publish job.
    assert workflow.index("Authorize the Release caller") < workflow.index(
        "ref: ${{ inputs.release_sha }}"
    )
