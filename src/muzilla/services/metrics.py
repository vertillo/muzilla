"""Builds the plain-text Prometheus exposition format body for
`GET /api/metrics`.

No `prometheus_client` dependency — the metric set is small enough that
hand-formatting the exposition format avoids a dependency for one
endpoint. Every query here is a `COUNT(*)` (cheap even without a
covering index — a single sequential scan, no per-row app-level work)
since this endpoint is meant to be scraped every ~15s; no query here
loads full ORM rows.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from muzilla.db.models import ChangeSet, Job, Track
from muzilla.metrics import provider_request_counts


def _count(session: Session, *whereclauses: object) -> int:
    stmt = select(func.count()).select_from(Track)
    for clause in whereclauses:
        stmt = stmt.where(clause)  # type: ignore[arg-type]
    return session.scalar(stmt) or 0


def render_metrics(session: Session) -> str:
    """Returns the full exposition-format body — one HELP/TYPE/value
    triple per metric family, matching the format Prometheus itself
    generates so this can be scraped with no format negotiation."""
    lines: list[str] = []

    # No `_total` suffix on any of these: Prometheus's own naming
    # convention (prometheus.io/docs/practices/naming) reserves `_total`
    # for counters ("an accumulating count") — these are gauges (can
    # decrease: a track can be deleted, a changeset/job can move out of
    # a state), and `promtool check metrics` flags a `_total`-suffixed
    # gauge as a naming-convention violation.
    tracks_total = _count(session)
    tracks_missing_art = _count(session, Track.has_embedded_art.is_(False))
    tracks_missing_album = _count(session, Track.album.is_(None))
    lines += [
        "# HELP muzilla_tracks Total tracks in the catalog.",
        "# TYPE muzilla_tracks gauge",
        f"muzilla_tracks {tracks_total}",
        "# HELP muzilla_tracks_missing_art Tracks with no embedded art.",
        "# TYPE muzilla_tracks_missing_art gauge",
        f"muzilla_tracks_missing_art {tracks_missing_art}",
        "# HELP muzilla_tracks_missing_album Tracks with no album tag.",
        "# TYPE muzilla_tracks_missing_album gauge",
        f"muzilla_tracks_missing_album {tracks_missing_album}",
    ]

    changeset_counts: dict[str, int] = dict(
        session.execute(select(ChangeSet.state, func.count()).group_by(ChangeSet.state)).all()  # type: ignore[arg-type]
    )
    lines += [
        "# HELP muzilla_changesets ChangeSets by state.",
        "# TYPE muzilla_changesets gauge",
    ]
    for state, count in sorted(changeset_counts.items()):
        lines.append(f'muzilla_changesets{{state="{state}"}} {count}')

    job_counts: dict[str, int] = dict(
        session.execute(select(Job.state, func.count()).group_by(Job.state)).all()  # type: ignore[arg-type]
    )
    lines += [
        "# HELP muzilla_jobs Jobs by state.",
        "# TYPE muzilla_jobs gauge",
    ]
    for state, count in sorted(job_counts.items()):
        lines.append(f'muzilla_jobs{{state="{state}"}} {count}')

    lines += [
        "# HELP muzilla_provider_requests_total Provider HTTP requests by host and outcome, since process start.",
        "# TYPE muzilla_provider_requests_total counter",
    ]
    for (host, outcome), count in sorted(provider_request_counts().items()):
        lines.append(
            f'muzilla_provider_requests_total{{host="{host}",outcome="{outcome}"}} {count}'
        )

    return "\n".join(lines) + "\n"
