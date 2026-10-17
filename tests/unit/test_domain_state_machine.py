from __future__ import annotations

import pytest

from flow_automation.core.models import JobStatus, SceneStatus
from flow_automation.core.state_machine import (
    InvalidStateTransition,
    transition_job,
    transition_scene,
)


def test_job_success_requires_a_verified_result() -> None:
    with pytest.raises(InvalidStateTransition, match="verified"):
        transition_job(
            JobStatus.RUNNING,
            JobStatus.SUCCEEDED,
            retry_count=0,
            retry_limit=3,
        )

    transition_job(
        JobStatus.RUNNING,
        JobStatus.SUCCEEDED,
        retry_count=0,
        retry_limit=3,
        result_verified=True,
    )


def test_interrupted_job_cannot_be_requeued_without_reconciliation() -> None:
    with pytest.raises(InvalidStateTransition, match="reconciled"):
        transition_job(
            JobStatus.INTERRUPTED,
            JobStatus.PENDING,
            retry_count=0,
            retry_limit=3,
        )

    transition_job(
        JobStatus.INTERRUPTED,
        JobStatus.PENDING,
        retry_count=0,
        retry_limit=3,
        safe_to_retry=True,
    )


def test_retry_limit_and_terminal_states_are_enforced() -> None:
    with pytest.raises(InvalidStateTransition, match="retry limit"):
        transition_job(
            JobStatus.RUNNING,
            JobStatus.RETRY_WAIT,
            retry_count=2,
            retry_limit=2,
        )
    with pytest.raises(InvalidStateTransition, match="cannot transition"):
        transition_job(
            JobStatus.SUCCEEDED,
            JobStatus.RUNNING,
            retry_count=0,
            retry_limit=3,
        )


def test_scene_result_and_approval_require_verified_inputs() -> None:
    with pytest.raises(InvalidStateTransition, match="result"):
        transition_scene(
            SceneStatus.IMAGE_RUNNING,
            SceneStatus.NEEDS_REVIEW,
        )
    with pytest.raises(InvalidStateTransition, match="selected, verified image"):
        transition_scene(
            SceneStatus.NEEDS_REVIEW,
            SceneStatus.IMAGE_APPROVED,
        )

    transition_scene(
        SceneStatus.IMAGE_RUNNING,
        SceneStatus.NEEDS_REVIEW,
        result_verified=True,
    )
    transition_scene(
        SceneStatus.NEEDS_REVIEW,
        SceneStatus.IMAGE_APPROVED,
        selected_image_verified=True,
    )
