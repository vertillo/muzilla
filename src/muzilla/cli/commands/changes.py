"""`muzilla changes` — apply/undo/show ChangeSets from the terminal
(docs/PLAN.md Phase 2 milestone: "changes apply/undo").
"""

from __future__ import annotations

from typing import Annotated

import typer

from muzilla.config.loader import load_config
from muzilla.services import changesets as changesets_service
from muzilla.services.db import session_scope
from muzilla.services.migrate import run_migrations

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
def apply(change_set_id: Annotated[int, typer.Argument()]) -> None:
    """Apply every `accepted` Change in a DRAFT ChangeSet to disk."""
    config = load_config()
    run_migrations(config)
    with session_scope(config) as session:
        try:
            result = changesets_service.apply(session, change_set_id)
        except ValueError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    typer.echo(f"changeset {change_set_id}: {result.state}")
    if result.applied_track_ids:
        typer.echo(f"  applied: {len(result.applied_track_ids)} track(s)")
    if result.conflicted_track_ids:
        typer.echo(f"  conflicted: {len(result.conflicted_track_ids)} track(s)")
        for track_id, error in result.errors.items():
            typer.echo(f"    track {track_id}: {error}")
    if result.state != "applied":
        raise typer.Exit(code=1)


@app.command()
def undo(change_set_id: Annotated[int, typer.Argument()]) -> None:
    """Synthesize and apply an inverse ChangeSet for an already-applied
    ChangeSet — files revert, the new undo ChangeSet is itself
    undo-able (undo-of-undo is redo)."""
    config = load_config()
    run_migrations(config)
    with session_scope(config) as session:
        try:
            undo_detail = changesets_service.undo(session, change_set_id)
            result = changesets_service.apply(session, undo_detail.id)
        except ValueError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    typer.echo(f"undo changeset {undo_detail.id}: {result.state}")
    if result.state != "applied":
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
