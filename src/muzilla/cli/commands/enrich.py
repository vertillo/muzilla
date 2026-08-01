"""`muzilla enrich` — ReplayGain/art/lyrics enrichment jobs from the
terminal (docs/PLAN.md §Phase-6).
"""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from muzilla.config.loader import load_config
from muzilla.config.schema import Config
from muzilla.services import capabilities as capabilities_service
from muzilla.services import duplicates as duplicates_service
from muzilla.services import jobs as jobs_service
from muzilla.services.db import session_scope
from muzilla.services.migrate import run_migrations
from muzilla.services.providers import build_provider_set

app = typer.Typer(help="Run enrichment jobs (ReplayGain, art, lyrics).")


def _run_job_and_report(job_id: int, config: Config, wait: bool, label: str) -> None:
    typer.echo(f"{label} job #{job_id} enqueued")
    if not wait:
        return

    provider_set = build_provider_set(config)

    async def _run() -> jobs_service.JobDetail:
        with session_scope(config) as session:
            return await jobs_service.run_job_once(session, config, provider_set, job_id)

    try:
        detail = asyncio.run(_run())
    finally:
        for client in provider_set.clients:
            asyncio.run(client.aclose())

    typer.echo(f"{label} job #{job_id}: {detail.state}")
    if detail.result:
        typer.echo(f"  {detail.result}")
    if detail.error:
        typer.echo(f"  error: {detail.error}", err=True)
    if detail.state != "succeeded":
        raise typer.Exit(code=1)


@app.command("replaygain")
def replaygain(
    wait: Annotated[bool, typer.Option(help="Run the job inline and print progress.")] = False,
) -> None:
    """Compute ReplayGain for every group with an unanalyzed track."""
    config = load_config()
    run_migrations(config)

    try:
        capabilities_service.require_replaygain(config)
    except capabilities_service.CapabilityUnavailableError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    with session_scope(config) as session:
        summary = jobs_service.enqueue_replaygain(session)

    _run_job_and_report(summary.id, config, wait, "replaygain")


@app.command("art")
def art(
    wait: Annotated[bool, typer.Option(help="Run the job inline and print progress.")] = False,
) -> None:
    """Fetch and embed album art for every group with a matched release and no art yet."""
    config = load_config()
    run_migrations(config)

    with session_scope(config) as session:
        summary = jobs_service.enqueue_art(session)

    _run_job_and_report(summary.id, config, wait, "art")


@app.command("lyrics")
def lyrics(
    wait: Annotated[bool, typer.Option(help="Run the job inline and print progress.")] = False,
) -> None:
    """Fetch lyrics from LRCLIB for every track with title+artist and no lyrics yet."""
    config = load_config()
    run_migrations(config)

    with session_scope(config) as session:
        summary = jobs_service.enqueue_lyrics(session)

    _run_job_and_report(summary.id, config, wait, "lyrics")


@app.command("duplicates")
def duplicates(
    wait: Annotated[bool, typer.Option(help="Run the job inline and print progress.")] = False,
) -> None:
    """Detect fingerprint-based duplicate tracks (same recording at different bitrates)."""
    config = load_config()
    run_migrations(config)

    with session_scope(config) as session:
        summary = jobs_service.enqueue_duplicate_detection(session)

    _run_job_and_report(summary.id, config, wait, "detect_duplicates")


@app.command("duplicates-list")
def duplicates_list(
    include_dismissed: Annotated[bool, typer.Option(help="Also show dismissed groups.")] = False,
) -> None:
    """List detected duplicate-track groups."""
    config = load_config()
    run_migrations(config)

    with session_scope(config) as session:
        groups = duplicates_service.list_duplicate_groups(session, include_dismissed=include_dismissed)

    if not groups:
        typer.echo("no duplicate groups found")
        return

    for group in groups:
        marker = " [dismissed]" if group.dismissed else ""
        typer.echo(f"group #{group.id} ({group.mb_recording_id}){marker}")
        for track in group.tracks:
            typer.echo(f"  {track.path}  [{track.format or '?'} {track.bitrate or '?'}kbps]")
