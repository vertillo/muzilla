"""`muzilla match` — fetch, rank, and stage provider candidates from
the terminal (docs/PLAN.md §10/§9's CLI/API parity: `muzilla match
group <id>` and the `POST /api/groups/{id}/stage` endpoint call the
exact same service functions).
"""

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

app = typer.Typer(help="Fetch and stage metadata matches from configured providers.")


def _print_candidates(
    candidates: Sequence[matching_service.CandidateRow],
    auto_applicable: bool,
    needs_confirmation: bool,
) -> None:
    rows = list(candidates)
    if not rows:
        typer.echo("no candidates found")
        return
    for i, c in enumerate(rows):
        dup = f" [dup-of: {c.is_duplicate_of}]" if c.is_duplicate_of else ""
        corr = f" [corroborated by: {', '.join(c.corroborated_by)}]" if c.corroborated_by else ""
        typer.echo(
            f"  [{i}] {c.source:12s} {c.album!r} / {c.album_artist!r} "
            f"({c.year}) distance={c.adjusted_distance:.3f} ref={c.ref_id}{dup}{corr}"
        )
    if auto_applicable:
        typer.echo("  -> top candidate is auto-applicable")
    elif needs_confirmation:
        typer.echo("  -> top candidate needs confirmation")


@app.command("group")
def match_group(
    group_id: Annotated[int, typer.Argument()],
    stage: Annotated[
        str | None, typer.Option(help="Stage from candidate 'source:ref_id', e.g. 'musicbrainz:abc-123'.")
    ] = None,
) -> None:
    """Fetch and rank candidates for an album group; optionally stage one."""
    config = load_config()
    run_migrations(config)
    provider_set = build_provider_set(config)

    async def _run() -> None:
        with session_scope(config) as session:
            if stage is not None:
                source, _, ref_id = stage.partition(":")
                if not ref_id:
                    typer.echo("error: --stage must be 'source:ref_id'", err=True)
                    raise typer.Exit(code=1)
                try:
                    cs = await matching_service.stage_group_match(
                        session, provider_set, group_id, source=source, ref_id=ref_id
                    )
                except ValueError as exc:
                    typer.echo(f"error: {exc}", err=True)
                    raise typer.Exit(code=1) from exc
                session.commit()
                typer.echo(f"staged changeset #{cs.id} from {source}:{ref_id}")
                return

            try:
                proposal = await matching_service.propose_group_candidates(
                    session, provider_set, group_id
                )
            except ValueError as exc:
                typer.echo(f"error: {exc}", err=True)
                raise typer.Exit(code=1) from exc
            _print_candidates(
                proposal.candidates, proposal.auto_applicable, proposal.needs_confirmation
            )

    try:
        asyncio.run(_run())
    finally:
        for client in provider_set.clients:
            asyncio.run(client.aclose())


@app.command("track")
def match_track(
    track_id: Annotated[int, typer.Argument()],
    stage: Annotated[
        str | None, typer.Option(help="Stage from candidate 'source:ref_id', e.g. 'musicbrainz:abc-123'.")
    ] = None,
) -> None:
    """Fetch and rank recording candidates for a singleton track; optionally stage one."""
    config = load_config()
    run_migrations(config)
    provider_set = build_provider_set(config)

    async def _run() -> None:
        with session_scope(config) as session:
            if stage is not None:
                source, _, ref_id = stage.partition(":")
                if not ref_id:
                    typer.echo("error: --stage must be 'source:ref_id'", err=True)
                    raise typer.Exit(code=1)
                try:
                    cs = await matching_service.stage_track_match(
                        session, provider_set, track_id, source=source, ref_id=ref_id
                    )
                except ValueError as exc:
                    typer.echo(f"error: {exc}", err=True)
                    raise typer.Exit(code=1) from exc
                session.commit()
                typer.echo(f"staged changeset #{cs.id} from {source}:{ref_id}")
                return

            try:
                proposal = await matching_service.propose_track_candidates(
                    session, provider_set, track_id
                )
            except ValueError as exc:
                typer.echo(f"error: {exc}", err=True)
                raise typer.Exit(code=1) from exc
            _print_candidates(
                proposal.candidates, proposal.auto_applicable, proposal.needs_confirmation
            )

    try:
        asyncio.run(_run())
    finally:
        for client in provider_set.clients:
            asyncio.run(client.aclose())
