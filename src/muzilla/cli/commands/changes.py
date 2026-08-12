"""`muzilla changes` — apply/undo/show ChangeSets from the terminal
(docs/product-spec.md Phase 2 milestone: "changes apply/undo").
"""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from muzilla.config.loader import load_config
from muzilla.services import changesets as changesets_service
from muzilla.services import jobs as jobs_service
from muzilla.services.db import session_scope
from muzilla.services.migrate import run_migrations
from muzilla.services.providers import build_provider_set

app = typer.Typer(help="Inspect and apply staged ChangeSets.")


@app.command()
def show(change_set_id: Annotated[int, typer.Argument()]) -> None:
    """Render a ChangeSet's field-level diff in the terminal."""
    config = load_config()
    run_migrations(config)
    with session_scope(config) as session:
        detail = changesets_service.get_changeset(session, change_set_id)

    if detail is None:
        typer.echo(f"changeset {change_set_id} not found", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"ChangeSet #{detail.id} — {detail.title}")
    typer.echo(f"  source: {detail.source}   state: {detail.state}")
    if detail.candidate_source:
        typer.echo(f"  candidate: {detail.candidate_source} {detail.candidate_ref}")
    typer.echo("")
    for c in detail.changes:
        marker = {"accepted": "+", "rejected": "-", "pending": "?"}[c.decision]
        flags = []
        if c.severity == "destructive":
            flags.append("DESTRUCTIVE")
        if c.is_manual:
            flags.append("manual")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        typer.echo(
            f"  [{marker}] {c.entity_type}#{c.entity_id} {c.field}: "
            f"{c.old_value!r} -> {c.new_value!r}{flag_str}"
        )


@app.command()
def apply(
    change_set_id: Annotated[int, typer.Argument()],
    backup: Annotated[
        bool | None,
        typer.Option(
            "--backup/--no-backup",
            help="Copy each file's original to storage.backup_dir before its "
            "first write this apply (docs/product-spec.md). Omit to use the "
            "configured apply.backup default.",
        ),
    ] = None,
) -> None:
    """Apply every `accepted` Change in a DRAFT ChangeSet to disk.

    Enqueues an `apply_changeset` job (the same path the API's
    POST .../apply uses) and runs it immediately in-process — no
    separate `muzilla jobs worker` process is required for a one-off
    CLI invocation.
    """
    config = load_config()
    run_migrations(config)
    provider_set = build_provider_set(config)

    async def _run() -> jobs_service.JobDetail:
        with session_scope(config) as session:
            try:
                job_id = changesets_service.apply(session, change_set_id, backup=backup)
            except ValueError as exc:
                typer.echo(f"error: {exc}", err=True)
                raise typer.Exit(code=1) from exc
            return await jobs_service.run_job_once(session, config, provider_set, job_id)

    try:
        detail = asyncio.run(_run())
    finally:
        for client in provider_set.clients:
            asyncio.run(client.aclose())

    if detail.state != "succeeded":
        typer.echo(f"changeset {change_set_id}: job {detail.state}", err=True)
        if detail.error:
            typer.echo(f"  {detail.error}", err=True)
        raise typer.Exit(code=1)

    result = detail.result or {}
    state = result.get("state", "unknown")
    typer.echo(f"changeset {change_set_id}: {state}")
    applied = result.get("applied_track_ids")
    conflicted = result.get("conflicted_track_ids")
    errors = result.get("errors")
    if isinstance(applied, list) and applied:
        typer.echo(f"  applied: {len(applied)} track(s)")
    if isinstance(conflicted, list) and conflicted:
        typer.echo(f"  conflicted: {len(conflicted)} track(s)")
        if isinstance(errors, dict):
            for track_id, error in errors.items():
                typer.echo(f"    track {track_id}: {error}")
    if state != "applied":
        raise typer.Exit(code=1)


@app.command()
def undo(change_set_id: Annotated[int, typer.Argument()]) -> None:
    """Synthesize and apply an inverse ChangeSet for an already-applied
    ChangeSet — files revert, the new undo ChangeSet is itself
    undo-able (undo-of-undo is redo)."""
    config = load_config()
    run_migrations(config)
    provider_set = build_provider_set(config)

    async def _run() -> tuple[jobs_service.JobDetail, int]:
        with session_scope(config) as session:
            try:
                undo_job_id = changesets_service.undo(session, change_set_id)
            except ValueError as exc:
                typer.echo(f"error: {exc}", err=True)
                raise typer.Exit(code=1) from exc
            undo_detail = await jobs_service.run_job_once(session, config, provider_set, undo_job_id)
            if undo_detail.state != "succeeded":
                typer.echo(f"undo of changeset {change_set_id}: job {undo_detail.state}", err=True)
                if undo_detail.error:
                    typer.echo(f"  {undo_detail.error}", err=True)
                raise typer.Exit(code=1)
            raw_undo_change_set_id = (undo_detail.result or {})["undo_change_set_id"]
            assert isinstance(raw_undo_change_set_id, int | str)
            undo_change_set_id = int(raw_undo_change_set_id)

            apply_job_id = changesets_service.apply(session, undo_change_set_id)
            apply_detail = await jobs_service.run_job_once(session, config, provider_set, apply_job_id)
            return apply_detail, undo_change_set_id

    try:
        apply_detail, undo_change_set_id = asyncio.run(_run())
    finally:
        for client in provider_set.clients:
            asyncio.run(client.aclose())

    result = apply_detail.result or {}
    state = result.get("state", "unknown")
    typer.echo(f"undo changeset {undo_change_set_id}: {state}")
    if state != "applied":
        raise typer.Exit(code=1)


@app.command(name="list")
def list_changesets(
    state: Annotated[str | None, typer.Option(help="Filter by state (draft/applied/...).")] = None,
) -> None:
    """List ChangeSets, most recent first."""
    config = load_config()
    run_migrations(config)
    with session_scope(config) as session:
        page = changesets_service.list_changesets(session, state=state, limit=50)

    for cs in page.items:
        typer.echo(f"  #{cs.id:<6} {cs.state:<12} {cs.source:<20} {cs.title}")
    typer.echo(f"{len(page.items)} of {page.total} changeset(s)")
