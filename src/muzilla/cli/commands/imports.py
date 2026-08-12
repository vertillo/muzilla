"""`muzilla import` — start and inspect a resumable whole-library
import from the terminal (docs/product-spec.md, §10, Phase 4).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from muzilla.config.loader import load_config
from muzilla.services import imports as imports_service
from muzilla.services import jobs as jobs_service
from muzilla.services.db import session_scope
from muzilla.services.migrate import run_migrations
from muzilla.services.providers import build_provider_set

app = typer.Typer(help="Start and inspect resumable whole-library imports.")


@app.command("start")
def import_start(
    root: Annotated[Path, typer.Argument(help="Library root to import.")],
    wait: Annotated[bool, typer.Option(help="Run the import inline and print progress.")] = False,
) -> None:
    """Start a scan->fingerprint->group->match import session."""
    config = load_config()
    run_migrations(config)

    with session_scope(config) as session:
        summary = imports_service.start_import(session, str(root))

    typer.echo(f"import session #{summary.id} started (job #{summary.job_id})")

    if not wait:
        return

    provider_set = build_provider_set(config)

    async def _run() -> jobs_service.JobDetail:
        with session_scope(config) as session:
            assert summary.job_id is not None
            return await jobs_service.run_job_once(session, config, provider_set, summary.job_id)

    try:
        detail = asyncio.run(_run())
    finally:
        for client in provider_set.clients:
            asyncio.run(client.aclose())

    with session_scope(config) as session:
        final = imports_service.get_import_session(session, summary.id)
    assert final is not None

    typer.echo(f"import session #{summary.id}: {final.state}")
    for task in final.tasks:
        typer.echo(f"  {task.stage:<12} {task.state}")
    if final.error:
        typer.echo(f"  error: {final.error}", err=True)
    if detail.state != "succeeded":
        raise typer.Exit(code=1)


@app.command("show")
def import_show(import_session_id: Annotated[int, typer.Argument()]) -> None:
    """Show an import session's stage progress and produced changesets."""
    config = load_config()
    run_migrations(config)
    with session_scope(config) as session:
        detail = imports_service.get_import_session(session, import_session_id)

    if detail is None:
        typer.echo(f"import session {import_session_id} not found", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Import session #{detail.id} — {detail.library_root}")
    typer.echo(f"  state: {detail.state}   job: #{detail.job_id}")
    if detail.error:
        typer.echo(f"  error: {detail.error}")
    typer.echo("")
    for task in detail.tasks:
        marker = {"done": "x", "failed": "!", "running": ">", "pending": " ", "skipped": "-"}.get(
            task.state, "?"
        )
        typer.echo(f"  [{marker}] {task.stage}")
        if task.error:
            typer.echo(f"      error: {task.error}")
    typer.echo("")
    typer.echo(f"  changesets: {len(detail.changeset_ids)}")


@app.command("resume")
def import_resume(import_session_id: Annotated[int, typer.Argument()]) -> None:
    """Re-enqueue an import session's job — resumes from whichever
    stage was last incomplete (a crash, or a previous failure)."""
    config = load_config()
    run_migrations(config)
    with session_scope(config) as session:
        try:
            summary = imports_service.resume_import(session, import_session_id)
        except ValueError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    typer.echo(f"import session #{summary.id} resumed (job #{summary.job_id})")
