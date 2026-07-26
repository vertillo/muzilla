"""`muzilla jobs` — inspect, cancel, and run background jobs from the
terminal (docs/PLAN.md §10, Phase 4).
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from typing import Annotated

import typer

from muzilla.config.loader import load_config
from muzilla.services import jobs as jobs_service
from muzilla.services.changesets import recover_apply_journal
from muzilla.services.db import session_scope
from muzilla.services.migrate import run_migrations
from muzilla.services.providers import build_provider_set

app = typer.Typer(help="Inspect, cancel, and run background jobs.")


@app.command(name="list")
def list_jobs(
    state: Annotated[str | None, typer.Option(help="Filter by state (pending/running/...).")] = None,
) -> None:
    """List jobs, most recent first."""
    config = load_config()
    run_migrations(config)
    with session_scope(config) as session:
        page = jobs_service.list_jobs(session, state=state, limit=50)

    for job in page.items:
        progress = ""
        if job.progress_total is not None:
            progress = f" [{job.progress_current}/{job.progress_total}]"
        typer.echo(f"  #{job.id:<6} {job.type:<16} {job.state:<10}{progress}")
    typer.echo(f"{len(page.items)} job(s)")


@app.command()
def show(job_id: Annotated[int, typer.Argument()]) -> None:
    """Show a job's full detail, including its payload/result."""
    config = load_config()
    run_migrations(config)
    with session_scope(config) as session:
        detail = jobs_service.get_job(session, job_id)

    if detail is None:
        typer.echo(f"job {job_id} not found", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Job #{detail.id} — {detail.type}")
    typer.echo(f"  state: {detail.state}   attempts: {detail.attempts}")
    if detail.progress_total is not None:
        typer.echo(f"  progress: {detail.progress_current}/{detail.progress_total}")
    if detail.progress_message:
        typer.echo(f"  message: {detail.progress_message}")
    if detail.error:
        typer.echo(f"  error: {detail.error}")
    typer.echo(f"  payload: {detail.payload}")
    if detail.result is not None:
        typer.echo(f"  result: {detail.result}")


@app.command()
def cancel(job_id: Annotated[int, typer.Argument()]) -> None:
    """Request cancellation of a pending/running job."""
    config = load_config()
    run_migrations(config)
    with session_scope(config) as session:
        try:
            jobs_service.request_job_cancel(session, job_id)
        except ValueError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    typer.echo(f"cancellation requested for job {job_id}")


@app.command()
def worker() -> None:
    """Run the worker pool in the foreground — for a docker-compose
    deployment that wants jobs processed independently of the API
    process, or local development without `muzilla serve` running.

    Runs the same startup crash recovery api/app.py's lifespan does,
    then the worker pool, until interrupted (Ctrl-C).
    """
    config = load_config()
    run_migrations(config)

    with session_scope(config) as recovery_session:
        jobs_service.recover_stuck_jobs(recovery_session)
        recover_apply_journal(recovery_session)
        recovery_session.commit()

    provider_set = build_provider_set(config)

    async def _run() -> None:
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        with contextlib.suppress(NotImplementedError):
            # Windows has no add_signal_handler; Ctrl-C there falls
            # through to KeyboardInterrupt instead.
            loop.add_signal_handler(signal.SIGINT, stop_event.set)
        await jobs_service.run_worker_pool(config, provider_set, stop_event)

    typer.echo(f"worker pool started (concurrency={config.jobs.worker_concurrency}), Ctrl-C to stop")
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
    finally:
        for client in provider_set.clients:
            asyncio.run(client.aclose())
