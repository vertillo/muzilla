"""`muzilla path-test` — render a path template against a track or an
album (TrackGroup) without touching any files (docs/product-spec.md: "let
users iterate without touching files"). A thin shell over
services.paths.preview_rename/render_path_for_track.
"""

from __future__ import annotations

from typing import Annotated

import typer

from muzilla.config.loader import load_config
from muzilla.services import paths as paths_service
from muzilla.services.db import session_scope
from muzilla.services.migrate import run_migrations

app = typer.Typer(help="Path template testing.")


@app.command(name="path-test")
def path_test(
    template: Annotated[str, typer.Argument(help="Path template to render, e.g. '$artist - $title'.")],
    track_id: Annotated[
        int | None, typer.Option("--track", help="Render for a single track.")
    ] = None,
    album: Annotated[
        int | None, typer.Option("--album", help="Render every track in this group (album).")
    ] = None,
) -> None:
    """Renders TEMPLATE against --track or --album, printing the
    resulting path(s) and any errors/collisions. Nothing is moved."""
    if (track_id is None) == (album is None):
        typer.echo("error: pass exactly one of --track or --album", err=True)
        raise typer.Exit(code=1)

    config = load_config()
    run_migrations(config)

    with session_scope(config) as session:
        try:
            if track_id is not None:
                row = paths_service.render_path_for_track(
                    session, track_id, config=config.paths, template_override=template
                )
                rows = [row]
            else:
                rows = paths_service.preview_rename(
                    session, group_id=album, config=config.paths, template_override=template
                )
        except ValueError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    if not rows:
        typer.echo("no tracks found")
        return

    for row in rows:
        typer.echo(f"[{row.track_id}] {row.old_path} -> {row.new_path}")
        for error in row.errors:
            typer.echo(f"    error: {error}", err=True)
        if row.is_collision:
            typer.echo("    collision: another track renders to the same path", err=True)
