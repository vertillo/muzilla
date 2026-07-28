from __future__ import annotations

from sqlalchemy.orm import Session

from muzilla.db.models import ChangeSet, Job, Track
from muzilla.metrics import provider_request_counts, record_provider_request
from muzilla.services.metrics import render_metrics


def _make_track(session: Session, *, path: str, **kwargs: object) -> Track:
    t = Track(
        path=path, filename=path.rsplit("/", 1)[-1], ext=".mp3", size_bytes=1000, mtime_ns=1,
        **kwargs,
    )
    session.add(t)
    session.flush()
    return t


def test_render_metrics_reports_track_counts(db_session: Session) -> None:
    _make_track(db_session, path="/a.mp3", album="Album", has_embedded_art=True)
    _make_track(db_session, path="/b.mp3", album=None, has_embedded_art=False)
    db_session.commit()

    body = render_metrics(db_session)

    assert "muzilla_tracks 2" in body
    assert "muzilla_tracks_missing_art 1" in body
    assert "muzilla_tracks_missing_album 1" in body


def test_render_metrics_reports_changesets_by_state(db_session: Session) -> None:
    for state in ("draft", "draft", "applied"):
        db_session.add(ChangeSet(title="x", source="manual_edit", state=state))
    db_session.commit()

    body = render_metrics(db_session)

    assert 'muzilla_changesets{state="draft"} 2' in body
    assert 'muzilla_changesets{state="applied"} 1' in body


def test_render_metrics_reports_jobs_by_state(db_session: Session) -> None:
    db_session.add(Job(type="scan", payload={}, state="pending"))
    db_session.add(Job(type="scan", payload={}, state="succeeded"))
    db_session.commit()

    body = render_metrics(db_session)

    assert 'muzilla_jobs{state="pending"} 1' in body
    assert 'muzilla_jobs{state="succeeded"} 1' in body


def test_render_metrics_reports_provider_requests(db_session: Session) -> None:
    from muzilla.metrics import _provider_requests

    _provider_requests.clear()  # process-global counter; isolate from other tests

    record_provider_request("musicbrainz.org", 200)
    record_provider_request("musicbrainz.org", 200)
    record_provider_request("musicbrainz.org", 503)

    body = render_metrics(db_session)

    assert 'muzilla_provider_requests_total{host="musicbrainz.org",outcome="success"} 2' in body
    assert 'muzilla_provider_requests_total{host="musicbrainz.org",outcome="error"} 1' in body


def test_render_metrics_output_is_valid_prometheus_exposition_shape(db_session: Session) -> None:
    """Every metric family has a HELP line, a TYPE line, and at least
    the family name appears before any value lines -- the minimal
    shape a Prometheus scraper actually parses."""
    body = render_metrics(db_session)
    lines = body.strip().split("\n")
    assert lines[-1] != ""  # no trailing blank line inside the split content
    assert any(line.startswith("# HELP muzilla_tracks ") for line in lines)
    assert any(line.startswith("# TYPE muzilla_tracks ") for line in lines)
    assert body.endswith("\n")  # exposition format requires a trailing newline


def test_record_provider_request_counts_by_host_and_outcome() -> None:
    """Unit test for the counter itself, independent of render_metrics'
    formatting -- proves the (host, outcome) keying and the >=400
    success/error split directly."""
    from muzilla.metrics import _provider_requests

    _provider_requests.clear()  # isolate from other tests sharing process-global state

    record_provider_request("api.deezer.com", 200)
    record_provider_request("api.deezer.com", 404)
    record_provider_request("api.deezer.com", 500)

    counts = provider_request_counts()
    assert counts[("api.deezer.com", "success")] == 1
    assert counts[("api.deezer.com", "error")] == 2
