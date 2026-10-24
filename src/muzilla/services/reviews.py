"""Service-facing compatibility exports for ReviewBundle persistence."""

from muzilla.config.schema import Config
from muzilla.jobs.sync import run_sync
from muzilla.pipeline.reviews import *  # noqa: F403
from muzilla.pipeline.reviews import (
    ReviewBundleDetail,
    capture_refresh_source,
    observe_refresh_source,
    refresh_review_bundle,
)
from muzilla.services.db import session_scope


async def refresh_review_bundle_async(config: Config, bundle_id: int) -> ReviewBundleDetail:
    """Supervise source observation and promotion with a thread-owned session."""

    def invoke() -> ReviewBundleDetail:
        with session_scope(config) as session:
            source = capture_refresh_source(
                session, bundle_id, library_root=config.storage.library_root
            )
        observation = observe_refresh_source(source)
        with session_scope(config) as session:
            detail = refresh_review_bundle(session, bundle_id, observation=observation)
            session.commit()
            return detail

    return await run_sync(invoke)
