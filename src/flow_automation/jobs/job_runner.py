from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from flow_automation.core.models import GenerationJob, JobStatus
from flow_automation.jobs.checkpoints import CheckpointStore
from flow_automation.jobs.retry_policy import (
    JobCancelledBeforeSubmission,
    JobCancellationUncertain,
    RetryDisposition,
    RetryPolicy,
)
from flow_automation.persistence.repository import FlowRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class JobExecutionResult:
    result_verified: bool
    external_task_reference: str | None = None
    technical_details: Mapping[str, object] = field(default_factory=dict)


class JobProcessor(Protocol):
    async def execute(
        self,
        job: GenerationJob,
        context: JobExecutionContext,
    ) -> JobExecutionResult: ...


@dataclass(slots=True)
class JobExecutionContext:
    job_id: str
    checkpoints: CheckpointStore
    _cancel_requested: asyncio.Event = field(default_factory=asyncio.Event)
    external_submission_possible: bool = False

    def request_cancel(self) -> None:
        self._cancel_requested.set()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_requested.is_set()

    def raise_if_cancel_requested(self) -> None:
        if not self._cancel_requested.is_set():
            return
        if self.external_submission_possible:
            raise JobCancellationUncertain(
                "Cancellation was requested after external submission may have started."
            )
        raise JobCancelledBeforeSubmission(
            "Cancellation was requested before external submission."
        )

    def mark_external_submission_possible(self, possible: bool) -> None:
        self.external_submission_possible = possible

    async def checkpoint(
        self,
        name: str,
        *,
        external_submission_possible: bool = False,
    ) -> None:
        if external_submission_possible:
            self.external_submission_possible = True
        self.checkpoints.record(
            self.job_id,
            name,
            external_submission_possible=self.external_submission_possible,
        )
        self.raise_if_cancel_requested()


ProgressCallback = Callable[[str, GenerationJob, str], None]
WaitCallback = Callable[[], Awaitable[bool]]
Sleep = Callable[[float], Awaitable[None]]


class JobRunner:
    """Executes persisted jobs and applies bounded retry/reconciliation policy."""

    def __init__(
        self,
        repository: FlowRepository,
        processor: JobProcessor,
        *,
        retry_policy: RetryPolicy | None = None,
        checkpoints: CheckpointStore | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._repository = repository
        self._processor = processor
        self._retry_policy = retry_policy or RetryPolicy()
        self._checkpoints = checkpoints or CheckpointStore(repository)
        self._sleep = sleep
        self._contexts: dict[str, JobExecutionContext] = {}

    def request_cancel(self, job_id: str) -> bool:
        context = self._contexts.get(job_id)
        if context is None:
            return False
        context.request_cancel()
        return True

    async def run(
        self,
        job_id: str,
        *,
        wait_until_resumed: WaitCallback,
        progress: ProgressCallback,
    ) -> GenerationJob:
        job = self._repository.get_job(job_id)
        if job.status is JobStatus.RETRY_WAIT:
            if not await wait_until_resumed():
                return job
            job = self._repository.transition_job(
                job.id,
                JobStatus.PENDING,
                message="Retry delay elapsed; job is ready for another attempt.",
            )
        if job.status is not JobStatus.PENDING:
            return job

        while True:
            if not await wait_until_resumed():
                return self._repository.get_job(job_id)
            job = self._repository.get_job(job_id)
            if job.status is not JobStatus.PENDING:
                return job
            job = self._repository.transition_job(
                job.id,
                JobStatus.RUNNING,
                message="Job execution started.",
            )
            progress("running", job, "Job execution started.")
            context = JobExecutionContext(job.id, self._checkpoints)
            self._contexts[job.id] = context
            self._checkpoints.record(job.id, "job_started")
            try:
                result = await self._processor.execute(job, context)
                context.raise_if_cancel_requested()
                if not result.result_verified:
                    updated = self._repository.transition_job(
                        job.id,
                        JobStatus.RECONCILIATION_REQUIRED,
                        message="Processor returned without verified output.",
                        error="UnverifiedProcessorResult",
                        external_task_reference=result.external_task_reference,
                        reconciliation_details={
                            "reason": "unverified_processor_result",
                        },
                    )
                    progress("reconciliation_required", updated, updated.last_error or "")
                    return updated
                self._checkpoints.record(job.id, "result_verified")
                updated = self._repository.transition_job(
                    job.id,
                    JobStatus.SUCCEEDED,
                    message="Job result was verified by its processor.",
                    technical_details=result.technical_details,
                    external_task_reference=result.external_task_reference,
                    result_verified=True,
                )
                progress("succeeded", updated, "Verified result saved.")
                return updated
            except asyncio.CancelledError:
                updated = self._record_cancelled_execution(job, context)
                progress(updated.status.value.casefold(), updated, updated.last_error or "")
                raise
            except Exception as error:
                updated, should_retry = self._handle_error(job, context, error)
                progress(updated.status.value.casefold(), updated, updated.last_error or "")
                if not should_retry:
                    return updated
                delay = self._retry_policy.delay_for_retry(updated.retry_count)
                if delay:
                    await self._sleep(delay)
                if not await wait_until_resumed():
                    return self._repository.get_job(job_id)
                current = self._repository.get_job(job_id)
                if current.status is not JobStatus.RETRY_WAIT:
                    return current
                self._repository.transition_job(
                    current.id,
                    JobStatus.PENDING,
                    message="Retry scheduled for this job.",
                )
            finally:
                self._contexts.pop(job.id, None)

    def _handle_error(
        self,
        job: GenerationJob,
        context: JobExecutionContext,
        error: Exception,
    ) -> tuple[GenerationJob, bool]:
        if context.cancel_requested:
            return self._record_cancelled_execution(job, context), False
        if isinstance(error, JobCancelledBeforeSubmission):
            updated = self._repository.transition_job(
                job.id,
                JobStatus.CANCELLED,
                message="Job cancelled at a safe checkpoint before external submission.",
            )
            return updated, False
        disposition = self._retry_policy.classify(
            error,
            external_submission_possible=context.external_submission_possible,
        )
        safe_error = type(error).__name__
        if disposition is RetryDisposition.RECONCILE:
            updated = self._repository.transition_job(
                job.id,
                JobStatus.RECONCILIATION_REQUIRED,
                message="The external operation outcome is uncertain; inspect before retrying.",
                error=safe_error,
                reconciliation_details=self._checkpoints.reconciliation_details(
                    job.id,
                    reason="uncertain_execution_outcome",
                ),
            )
            return updated, False
        if disposition is RetryDisposition.RETRY and job.retry_count < job.retry_limit:
            updated = self._repository.transition_job(
                job.id,
                JobStatus.RETRY_WAIT,
                message="Transient failure; a bounded retry was scheduled.",
                error=safe_error,
            )
            return updated, True
        updated = self._repository.transition_job(
            job.id,
            JobStatus.FAILED,
            message="Job failed; it will not be retried automatically.",
            error=safe_error,
        )
        logger.warning("Job failed without further retry: job_id=%s error=%s", job.id, safe_error)
        return updated, False

    def _record_cancelled_execution(
        self,
        job: GenerationJob,
        context: JobExecutionContext,
    ) -> GenerationJob:
        if context.external_submission_possible:
            return self._repository.transition_job(
                job.id,
                JobStatus.RECONCILIATION_REQUIRED,
                message="Local cancellation interrupted an uncertain external operation.",
                error="CancellationOutcomeUnknown",
                reconciliation_details=self._checkpoints.reconciliation_details(
                    job.id,
                    reason="local_cancel_after_possible_submission",
                ),
            )
        if context.cancel_requested:
            return self._repository.transition_job(
                job.id,
                JobStatus.CANCELLED,
                message="Job cancelled before external submission.",
            )
        return self._repository.transition_job(
            job.id,
            JobStatus.INTERRUPTED,
            message="Local execution was interrupted before a safe completion point.",
            error="LocalExecutionInterrupted",
        )
