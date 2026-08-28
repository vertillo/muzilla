"""`muzilla match` — fetch candidates."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Annotated

import typer

from muzilla.config.loader import load_config
from muzilla.services import matching as matching_service
from muzilla.services.db import session_scope
from muzilla.services.migrate import run_migrations
from muzilla.services.providers import build_provider_set

app = typer.Typer(help="Fetch provider candidates.")


def _print_candidates(candidates: Sequence[matching_service.CandidateRow], auto_applicable: bool, needs_confirmation: bool) -> None:
    rows = list(candidates)
    if not rows:
        typer.echo("no candidates found")
        return
    for i, c in enumerate(rows):
        typer.echo(f"  [{i}] {c.source} {c.album!r} ref={c.ref_id}")

@app.command("group")
def match_group(group_id: Annotated[int, typer.Argument()], stage: Annotated[str | None, typer.Option(help="Stage candidate source:ref_id")] = None) -> None:
    config = load_config()
    run_migrations(config)
    provider_set = build_provider_set(config)
    async def _run() -> None:
        with session_scope(config) as session:
            proposal = await matching_service.propose_group_candidates(session, provider_set, group_id)
            _print_candidates(proposal.candidates, proposal.auto_applicable, proposal.needs_confirmation)
            if stage is not None:
                typer.echo("staging via ChangeSet is removed; use web UI ReviewBundle")
    asyncio.run(_run())

@app.command("track")
def match_track(track_id: Annotated[int, typer.Argument()], stage: Annotated[str | None, typer.Option(help="Stage candidate source:ref_id")] = None) -> None:
    config = load_config()
    run_migrations(config)
    provider_set = build_provider_set(config)
    async def _run() -> None:
        with session_scope(config) as session:
            proposal = await matching_service.propose_track_candidates(session, provider_set, track_id)
            _print_candidates(proposal.candidates, proposal.auto_applicable, proposal.needs_confirmation)
            if stage is not None:
                typer.echo("staging via ChangeSet is removed; use web UI ReviewBundle")
    asyncio.run(_run())
