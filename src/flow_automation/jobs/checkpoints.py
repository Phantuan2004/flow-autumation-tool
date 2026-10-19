from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flow_automation.persistence.repository import FlowRepository

_CHECKPOINTS = frozenset(
    {
        "job_started",
        "before_external_submission",
        "external_submission_possible",
        "result_observed",
        "result_verified",
    }
)


@dataclass(frozen=True, slots=True)
class JobCheckpoint:
    job_id: str
    name: str
    external_submission_possible: bool


class CheckpointStore:
    """Persists safe execution checkpoints in the job event history."""

    def __init__(self, repository: FlowRepository) -> None:
        self._repository = repository

    def record(
        self,
        job_id: str,
        name: str,
        *,
        external_submission_possible: bool = False,
    ) -> JobCheckpoint:
        if name not in _CHECKPOINTS:
            raise ValueError(f"Unsupported job checkpoint {name!r}.")
        self._repository.record_job_note(
            job_id,
            f"Execution checkpoint: {name}.",
            technical_details={
                "checkpoint": name,
                "external_submission_possible": external_submission_possible,
            },
        )
        return JobCheckpoint(job_id, name, external_submission_possible)

    def latest(self, job_id: str) -> JobCheckpoint | None:
        for event in reversed(self._repository.list_job_events(job_id)):
            name = event.technical_details.get("checkpoint")
            if isinstance(name, str) and name in _CHECKPOINTS:
                uncertain = event.technical_details.get(
                    "external_submission_possible",
                    False,
                )
                return JobCheckpoint(job_id, name, bool(uncertain))
        return None

    def reconciliation_details(
        self,
        job_id: str,
        *,
        reason: str,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("A reconciliation reason is required.")
        checkpoint = self.latest(job_id)
        details: dict[str, Any] = {"reason": reason}
        if checkpoint is not None:
            details["last_checkpoint"] = checkpoint.name
            details["external_submission_possible"] = (
                checkpoint.external_submission_possible
            )
        return details
