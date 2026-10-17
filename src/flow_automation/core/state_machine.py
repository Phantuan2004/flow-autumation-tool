from __future__ import annotations

from flow_automation.core.models import JobStatus, SceneStatus


class InvalidStateTransition(ValueError):
    """Raised when a domain object cannot make the requested state transition."""


_JOB_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.PENDING: frozenset({JobStatus.RUNNING, JobStatus.CANCELLED}),
    JobStatus.RUNNING: frozenset(
        {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.RETRY_WAIT,
            JobStatus.CANCELLED,
            JobStatus.INTERRUPTED,
            JobStatus.RECONCILIATION_REQUIRED,
        }
    ),
    JobStatus.RETRY_WAIT: frozenset(
        {JobStatus.PENDING, JobStatus.CANCELLED, JobStatus.INTERRUPTED}
    ),
    JobStatus.INTERRUPTED: frozenset(
        {JobStatus.RECONCILIATION_REQUIRED, JobStatus.PENDING, JobStatus.FAILED}
    ),
    JobStatus.RECONCILIATION_REQUIRED: frozenset(
        {JobStatus.SUCCEEDED, JobStatus.PENDING, JobStatus.FAILED}
    ),
    JobStatus.FAILED: frozenset({JobStatus.RETRY_WAIT}),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}

_SCENE_TRANSITIONS: dict[SceneStatus, frozenset[SceneStatus]] = {
    SceneStatus.READY: frozenset(
        {SceneStatus.IMAGE_QUEUED, SceneStatus.FAILED, SceneStatus.SKIPPED}
    ),
    SceneStatus.IMAGE_QUEUED: frozenset(
        {
            SceneStatus.IMAGE_RUNNING,
            SceneStatus.READY,
            SceneStatus.FAILED,
            SceneStatus.SKIPPED,
        }
    ),
    SceneStatus.IMAGE_RUNNING: frozenset(
        {SceneStatus.NEEDS_REVIEW, SceneStatus.READY, SceneStatus.FAILED}
    ),
    SceneStatus.NEEDS_REVIEW: frozenset(
        {
            SceneStatus.IMAGE_APPROVED,
            SceneStatus.IMAGE_QUEUED,
            SceneStatus.FAILED,
            SceneStatus.SKIPPED,
        }
    ),
    SceneStatus.IMAGE_APPROVED: frozenset(
        {
            SceneStatus.NEEDS_REVIEW,
            SceneStatus.VIDEO_QUEUED,
            SceneStatus.IMAGE_QUEUED,
            SceneStatus.SKIPPED,
        }
    ),
    SceneStatus.VIDEO_QUEUED: frozenset(
        {
            SceneStatus.VIDEO_RUNNING,
            SceneStatus.VIDEO_QUEUED,
            SceneStatus.IMAGE_APPROVED,
            SceneStatus.FAILED,
            SceneStatus.SKIPPED,
        }
    ),
    SceneStatus.VIDEO_RUNNING: frozenset(
        {SceneStatus.VIDEO_READY, SceneStatus.IMAGE_APPROVED, SceneStatus.FAILED}
    ),
    SceneStatus.VIDEO_READY: frozenset(
        {SceneStatus.IMAGE_APPROVED, SceneStatus.SKIPPED}
    ),
    SceneStatus.FAILED: frozenset(
        {
            SceneStatus.READY,
            SceneStatus.IMAGE_QUEUED,
            SceneStatus.VIDEO_QUEUED,
            SceneStatus.NEEDS_REVIEW,
            SceneStatus.VIDEO_READY,
            SceneStatus.SKIPPED,
        }
    ),
    SceneStatus.SKIPPED: frozenset({SceneStatus.READY}),
}


def transition_job(
    current: JobStatus,
    target: JobStatus,
    *,
    retry_count: int,
    retry_limit: int,
    result_verified: bool = False,
    safe_to_retry: bool = False,
) -> None:
    if target not in _JOB_TRANSITIONS[current]:
        raise InvalidStateTransition(
            f"Job cannot transition from {current.value} to {target.value}."
        )
    if retry_count < 0 or retry_limit < 0 or retry_count > retry_limit:
        raise ValueError("Job retry count and limit are inconsistent.")
    if target is JobStatus.SUCCEEDED and not result_verified:
        raise InvalidStateTransition(
            "A job can succeed only after its result has been verified."
        )
    if target is JobStatus.RETRY_WAIT and retry_count >= retry_limit:
        raise InvalidStateTransition("The job has reached its retry limit.")
    if target is JobStatus.PENDING and current in {
        JobStatus.INTERRUPTED,
        JobStatus.RECONCILIATION_REQUIRED,
    } and not safe_to_retry:
        raise InvalidStateTransition(
            "An interrupted job must be reconciled before it can be retried."
        )


def transition_scene(
    current: SceneStatus,
    target: SceneStatus,
    *,
    result_verified: bool = False,
    selected_image_verified: bool = False,
) -> None:
    if target not in _SCENE_TRANSITIONS[current]:
        raise InvalidStateTransition(
            f"Scene cannot transition from {current.value} to {target.value}."
        )
    if target in {SceneStatus.NEEDS_REVIEW, SceneStatus.VIDEO_READY} and not result_verified:
        raise InvalidStateTransition(
            "A scene can advance to a completed media state only after its result "
            "has been verified."
        )
    if target in {SceneStatus.IMAGE_APPROVED, SceneStatus.VIDEO_QUEUED} and not selected_image_verified:
        raise InvalidStateTransition(
            "A selected, verified image is required for approval or video creation."
        )
