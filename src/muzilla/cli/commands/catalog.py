"""`muzilla scan` and `muzilla analyze` — thin shells over services."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from muzilla.config.loader import load_config
from muzilla.services import analyze as analyze_service
from muzilla.services import scan as scan_service
from muzilla.services.db import session_scope

app = typer.Typer(help="Catalog scanning and analysis.")


@app.command()
def scan(
    path: Annotated[Path, typer.Argument(help="Library root to scan.")],
) -> None:
    """Walk PATH, probe audio files, and update the catalog."""
    config = load_config()
    with session_scope(config) as session:
        stats = scan_service.run_scan(session, path)

    typer.echo(
        f"scanned {stats.scanned} "
        f"(added {stats.added}, updated {stats.updated}, unchanged {stats.unchanged}, "
        f"errors {stats.errored}, missing {stats.missing})"
    )


@app.command()
def analyze() -> None:
    """Print a library analysis report: album/single split, tag
    completeness, duplicate candidates, format/bitrate breakdown."""
    config = load_config()
    with session_scope(config) as session:
        report = analyze_service.analyze_library(session)

    typer.echo(f"Total tracks:      {report.total_tracks}")
    typer.echo(f"  with errors:     {report.tracks_with_errors}")
    typer.echo(f"  missing:         {report.tracks_missing}")
    typer.echo("")
    typer.echo(f"Album tracks:      {report.album_track_count} ({report.inferred_album_count} inferred albums)")
    typer.echo(f"Singleton tracks:  {report.singleton_track_count}")
    typer.echo("")
    typer.echo("Field completeness:")
    for fc in report.field_completeness:
        if fc.total == 0:
            continue
        typer.echo(f"  {fc.label:<28} {fc.present}/{fc.total} ({fc.present_ratio:.0%})")
    typer.echo("")
    typer.echo(f"Duplicate candidates: {len(report.duplicate_groups)}")
    for dupe in report.duplicate_groups[:20]:
        typer.echo(f"  {dupe.artist} — {dupe.title} ({len(dupe.track_ids)} copies)")
    if len(report.duplicate_groups) > 20:
        typer.echo(f"  ... and {len(report.duplicate_groups) - 20} more")
    typer.echo("")
    typer.echo("Formats:")
    for fb in report.format_breakdown:
        typer.echo(f"  {fb.format:<10} {fb.count}")
    if report.avg_bitrate is not None:
        typer.echo(f"Average bitrate: {report.avg_bitrate:.0f}")
