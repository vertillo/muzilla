from __future__ import annotations

import typer
import uvicorn

from muzilla import __about__
from muzilla.cli.commands.catalog import app as catalog_app
from muzilla.cli.commands.enrich import app as enrich_app
from muzilla.cli.commands.imports import app as imports_app
from muzilla.cli.commands.jobs import app as jobs_app
from muzilla.cli.commands.match import app as match_app
from muzilla.cli.commands.paths import app as paths_app
from muzilla.config.loader import load_config
from muzilla.logging import configure_logging

app = typer.Typer(name="muzilla", help="Self-hosted music metadata manager.")
app.add_typer(catalog_app)
app.add_typer(paths_app)
app.add_typer(match_app, name="match")
app.add_typer(jobs_app, name="jobs")
app.add_typer(imports_app, name="import")
app.add_typer(enrich_app, name="enrich")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__about__.__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the muzilla version and exit.",
    ),
) -> None:
    # Runs before every subcommand, once from the CLI entry point — each
    # command still calls load_config() itself
    # for its own use, but logging only needs to be configured the one
    # time here since it sets process-global logging state.
    configure_logging(load_config().logging)


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind host."),
    port: int = typer.Option(8080, help="Bind port."),
    reload: bool = typer.Option(False, help="Enable autoreload (development only)."),
    forwarded_allow_ips: str = typer.Option(
        "127.0.0.1",
        help=(
            "Comma-separated list of IPs trusted to set X-Forwarded-For/"
            "X-Forwarded-Proto (a reverse proxy like Cloudflare Tunnel or "
            "Tailscale, terminating on the same host). Must name the proxy "
            "specifically — never '*', which would let any client spoof "
            "its IP and bypass the login rate limiter."
        ),
    ),
) -> None:
    """Run the muzilla web server (API + GUI)."""
    uvicorn.run(
        "muzilla.api.app:app",
        host=host,
        port=port,
        reload=reload,
        proxy_headers=True,
        forwarded_allow_ips=forwarded_allow_ips,
    )


if __name__ == "__main__":
    app()
