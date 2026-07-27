"""`muzilla enrich` — ReplayGain/art/lyrics enrichment jobs from the
terminal (docs/PLAN.md §Phase-6).
"""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from muzilla.config.loader import load_config
from muzilla.services import jobs as jobs_service
from muzilla.services.db import session_scope
from muzilla.services.migrate import run_migrations
from muzilla.services.providers import build_provider_set

app = typer.Typer(help="Run enrichment jobs (ReplayGain, art, lyrics).")


@app.command("replaygain")
def replaygain(
    wait: Annotated[bool, typer.Option(help="Run the job inline and print progress.")] = False,
) -> None:
    """Compute ReplayGain for every group with an unanalyzed track."""
    config = load_config()
    run_migrations(config)

    with session_scope(config) as session:
        summary = jobs_service.enqueue_replaygain(session)

    typer.echo(f"replaygain job #{summary.id} enqueued")

    if not wait:
        return

    provider_set = build_provider_set(config)

    async def _run() -> jobs_service.JobDetail:
        with session_scope(config) as session:
            return await jobs_service.run_job_once(session, config, provider_set, summary.id)

    try:
        detail = asyncio.run(_run())
    finally:
        for client in provider_set.clients:
            asyncio.run(client.aclose())

    typer.echo(f"replaygain job #{summary.id}: {detail.state}")
    if detail.result:
        typer.echo(f"  {detail.result}")
    if detail.error:
        typer.echo(f"  error: {detail.error}", err=True)
    if detail.state != "succeeded":
        raise typer.Exit(code=1)
