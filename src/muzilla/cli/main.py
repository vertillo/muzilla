from __future__ import annotations

import typer
import uvicorn

from muzilla import __about__
from muzilla.cli.commands.catalog import app as catalog_app

app = typer.Typer(name="muzilla", help="Self-hosted music metadata manager.")
app.add_typer(catalog_app)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__about__.__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True,
        help="Show the muzilla version and exit.",
    ),
) -> None:
    pass


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind host."),
    port: int = typer.Option(8080, help="Bind port."),
    reload: bool = typer.Option(False, help="Enable autoreload (development only)."),
) -> None:
    """Run the muzilla web server (API + GUI)."""
    uvicorn.run("muzilla.api.app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
