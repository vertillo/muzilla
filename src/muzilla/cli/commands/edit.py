"""`muzilla edit` — manual tag editing from the terminal, a thin shell
over services.edit (docs/PLAN.md Phase 2 milestone)."""

from __future__ import annotations

from typing import Annotated

import typer

from muzilla.config.loader import load_config
from muzilla.services import edit as edit_service
from muzilla.services.db import session_scope
from muzilla.services.migrate import run_migrations

app = typer.Typer(help="Manual tag editing.")


def _parse_field_values(pairs: list[str]) -> dict[str, str]:
    field_values: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise typer.BadParameter(f"expected field=value, got {pair!r}")
        field, _, value = pair.partition("=")
        field_values[field.strip()] = value
    return field_values


@app.command()
def edit(
    track_id: Annotated[int, typer.Argument(help="Track id to edit.")],
    field: Annotated[
        list[str],
        typer.Option("--field", "-f", help="field=value, repeatable. Use field= (empty) to clear."),
    ],
) -> None:
    """Stage a manual edit as a DRAFT ChangeSet — does not touch disk.
    Review with `muzilla changes show <id>` and apply with
    `muzilla changes apply <id>`."""
    config = load_config()
    run_migrations(config)
    field_values_raw = _parse_field_values(field)
    field_values: dict[str, object] = {k: (v if v != "" else None) for k, v in field_values_raw.items()}

    with session_scope(config) as session:
        try:
            cs = edit_service.edit_track(session, track_id=track_id, field_values=field_values)
        except edit_service.EditValidationError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        except ValueError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        session.commit()

    typer.echo(f"created changeset {cs.id} (draft) with {len(cs.changes)} change(s)")
    typer.echo(f"review: muzilla changes show {cs.id}")
    typer.echo(f"apply:  muzilla changes apply {cs.id}")
