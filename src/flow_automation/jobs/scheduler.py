from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from flow_automation.core.models import (
    GenerationJob,
    JobStatus,
    JobType,
    SceneStatus,
)
from flow_automation.jobs.checkpoints import CheckpointStore
from flow_automation.jobs.job_runner import JobProcessor, JobRunner
from flow_automation.jobs.retry_policy import RetryPolicy
from flow_automation.persistence.repository import FlowRepository

logger = logging.getLogger(__name__)


class SchedulerEventType(StrEnum):
    QUEUED = "queued"
    STARTED = "started"
    PAUSED = "paused"
    RESUMED = "resumed"
    PROGRESS = "progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    RECOVERED = "recovered"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SchedulerEvent:
    event_type: SchedulerEventType
    project_id: str | None
    job_id: str | None
    status: JobStatus | None
    completed: int
    total: int
    message: str


EventListener = Callable[[SchedulerEvent], None]


class BatchScheduler:
    """Async scheduler for persisted jobs; default concurrency protects one browser page."""

    def __init__(
        self,
        repository: FlowRepository,
        processor: JobProcessor,
        *,
        concurrency: int = 1,
        browser_bound: bool = True,
        retry_policy: RetryPolicy | None = None,
        checkpoints: CheckpointStore | None = None,
    ) -> None:
        if concurrency < 1:
            raise ValueError("Scheduler concurrency must be at least one.")
        if browser_bound and concurrency != 1:
            raise ValueError(
                "A browser-bound processor must use concurrency=1 to serialize page access."
            )
        self._repository = repository
        self._checkpoints = checkpoints or CheckpointStore(repository)
        self._runner = JobRunner(
            repository,
            processor,
            retry_policy=retry_policy,
            checkpoints=self._checkpoints,
        )
        self._concurrency = concurrency if not browser_bound else 1
        self._resume_gate = asyncio.Event()
        self._resume_gate.set()
        self._stop_dispatch = asyncio.Event()
        self._paused = False
        self._dispatch_lock = asyncio.Lock()
        self._active_ids: set[str] = set()
        self._listeners: set[EventListener] = set()
        self._dispatch_ids: set[str] = set()
        self._completed_ids: set[str] = set()
        self._project_id: str | None = None
        self._total = 0
        self._completed = 0

    @property
    def concurrency(self) -> int:
        return self._concurrency

    @property
    def is_paused(self) -> bool:
        return self._paused

    def subscribe(self, listener: EventListener) -> Callable[[], None]:
        self._listeners.add(listener)

        def unsubscribe() -> None:
            self._listeners.discard(listener)

        return unsubscribe

    async def queue_project(
        self,
        project_id: str,
        job_type: JobType = JobType.IMAGE,
        *,
        retry_limit: int = 3,
    ) -> list[GenerationJob]:
        if retry_limit < 0:
            raise ValueError("Retry limit must not be negative.")
        created: list[GenerationJob] = []
        active_scene_ids = {
            job.scene_id
            for job in self._repository.list_jobs(
                project_id=project_id,
                job_type=job_type,
                statuses=(
                    JobStatus.PENDING,
                    JobStatus.RUNNING,
                    JobStatus.RETRY_WAIT,
                    JobStatus.INTERRUPTED,
                    JobStatus.RECONCILIATION_REQUIRED,
                ),
            )
        }
        for scene in self._repository.list_scenes(project_id):
            if scene.id in active_scene_ids:
                continue
            if job_type is JobType.IMAGE and scene.status not in {
                SceneStatus.READY,
                SceneStatus.NEEDS_REVIEW,
                SceneStatus.IMAGE_APPROVED,
                SceneStatus.FAILED,
            }:
                continue
            if job_type is JobType.VIDEO and scene.status is not SceneStatus.IMAGE_APPROVED:
                continue
            created.append(
                self._repository.create_job(
                    project_id,
                    scene.id,
                    job_type,
                    retry_limit=retry_limit,
                )
            )
        self._emit(
            SchedulerEventType.QUEUED,
            project_id=project_id,
            job_id=None,
            status=JobStatus.PENDING if created else None,
            message=f"Queued {len(created)} {job_type.value.lower()} jobs.",
        )
        return created

    async def start(
        self,
        project_id: str | None = None,
        *,
        job_type: JobType | None = None,
    ) -> list[GenerationJob]:
        async with self._dispatch_lock:
            self._stop_dispatch.clear()
            self._project_id = project_id
            jobs = self._repository.list_jobs(
                project_id=project_id,
                job_type=job_type,
                statuses=(JobStatus.PENDING, JobStatus.RETRY_WAIT),
            )
            job_ids = [job.id for job in jobs]
            self._total = len(job_ids)
            self._completed = 0
            self._dispatch_ids = set(job_ids)
            self._completed_ids.clear()
            self._emit(
                SchedulerEventType.STARTED,
                project_id=project_id,
                job_id=None,
                status=None,
                message=f"Starting {self._total} queued jobs.",
            )
            if not job_ids:
                self._emit(
                    SchedulerEventType.COMPLETED,
                    project_id=project_id,
                    job_id=None,
                    status=None,
                    message="No resumable jobs are queued.",
                )
                return []

            queue: asyncio.Queue[str] = asyncio.Queue()
            for job_id in job_ids:
                queue.put_nowait(job_id)
            results: list[GenerationJob] = []
            results_lock = asyncio.Lock()

            async def worker() -> None:
                while not queue.empty():
                    if not await self._wait_until_permitted():
                        return
                    try:
                        job_id = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    current = self._repository.get_job(job_id)
                    if current.status not in {JobStatus.PENDING, JobStatus.RETRY_WAIT}:
                        self._mark_completed(job_id)
                        queue.task_done()
                        self._emit(
                            SchedulerEventType.PROGRESS,
                            project_id=project_id,
                            job_id=job_id,
                            status=current.status,
                            message="Queued job is already terminal.",
                        )
                        continue
                    self._active_ids.add(job_id)
                    try:
                        result = await self._runner.run(
                            job_id,
                            wait_until_resumed=self._wait_until_permitted,
                            progress=self._on_progress,
                        )
                        async with results_lock:
                            results.append(result)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("Scheduler worker failed: job_id=%s", job_id)
                        self._emit(
                            SchedulerEventType.FAILED,
                            project_id=project_id,
                            job_id=job_id,
                            status=self._repository.get_job(job_id).status,
                            message="Worker error; remaining jobs remain queued.",
                        )
                    finally:
                        self._active_ids.discard(job_id)
                        self._mark_completed(job_id)
                        queue.task_done()
                        self._emit(
                            SchedulerEventType.PROGRESS,
                            project_id=project_id,
                            job_id=job_id,
                            status=self._repository.get_job(job_id).status,
                            message="Job processing finished.",
                        )

            workers = [
                asyncio.create_task(worker(), name=f"flow-job-worker-{index}")
                for index in range(self._concurrency)
            ]
            await asyncio.gather(*workers)
            self._emit(
                SchedulerEventType.COMPLETED,
                project_id=project_id,
                job_id=None,
                status=None,
                message="Queued job pass completed.",
            )
            return sorted(results, key=lambda result: result.created_at)

    async def pause(self) -> None:
        self._paused = True
        self._resume_gate.clear()
        self._emit(
            SchedulerEventType.PAUSED,
            project_id=self._project_id,
            job_id=None,
            status=None,
            message="Paused: no new jobs or retry attempts will start.",
        )

    async def resume(self) -> None:
        self._paused = False
        self._resume_gate.set()
        self._emit(
            SchedulerEventType.RESUMED,
            project_id=self._project_id,
            job_id=None,
            status=None,
            message="Scheduler resumed.",
        )

    async def cancel(self, job_id: str) -> str:
        job = self._repository.get_job(job_id)
        if job.status in {JobStatus.PENDING, JobStatus.RETRY_WAIT}:
            updated = self._repository.transition_job(
                job.id,
                JobStatus.CANCELLED,
                message="Queued job cancelled before execution.",
            )
            self._emit(
                SchedulerEventType.CANCELLED,
                project_id=job.project_id,
                job_id=job.id,
                status=updated.status,
                message="Queued job cancelled; no Flow operation was started.",
            )
            self._mark_completed(job.id)
            self._emit(
                SchedulerEventType.PROGRESS,
                project_id=job.project_id,
                job_id=job.id,
                status=updated.status,
                message="Queued job cancellation recorded.",
            )
            return "cancelled_before_start"
        if job.status is JobStatus.RUNNING and self._runner.request_cancel(job_id):
            return "cancellation_requested"
        return "not_cancellable"

    async def cancel_pending(
        self,
        project_id: str | None = None,
        *,
        job_type: JobType | None = None,
    ) -> int:
        self._paused = True
        self._resume_gate.clear()
        self._stop_dispatch.set()
        for job_id in tuple(self._active_ids):
            active_job = self._repository.get_job(job_id)
            if project_id is not None and active_job.project_id != project_id:
                continue
            if job_type is not None and active_job.job_type is not job_type:
                continue
            self._runner.request_cancel(job_id)
        cancelled = 0
        for job in self._repository.list_jobs(
            project_id=project_id,
            job_type=job_type,
            statuses=(JobStatus.PENDING, JobStatus.RETRY_WAIT),
        ):
            result = await self.cancel(job.id)
            if result == "cancelled_before_start":
                cancelled += 1
        return cancelled

    async def stop_dispatch(self) -> None:
        """Stop taking new jobs while allowing the active job to finish safely."""
        self._stop_dispatch.set()

    async def retry(self, job_id: str) -> None:
        job = self._repository.get_job(job_id)
        if job.status is not JobStatus.FAILED:
            raise ValueError("Only a known failed job can be explicitly retried.")
        updated = self._repository.transition_job(
            job.id,
            JobStatus.RETRY_WAIT,
            message="User requested a bounded retry of the existing job.",
            error=job.last_error or "ExplicitRetry",
        )
        self._emit(
            SchedulerEventType.QUEUED,
            project_id=job.project_id,
            job_id=job.id,
            status=updated.status,
            message="Existing job queued for an explicit retry.",
        )

    def recover_after_restart(self, project_id: str | None = None) -> list[GenerationJob]:
        interrupted: list[GenerationJob] = []
        for job in self._repository.list_jobs(
            project_id=project_id,
            statuses=(JobStatus.RUNNING,),
        ):
            updated = self._repository.transition_job(
                job.id,
                JobStatus.RECONCILIATION_REQUIRED,
                message="Application restarted while this job was running; inspect Flow before retrying.",
                error="ApplicationRestartedDuringExecution",
                reconciliation_details=self._checkpoints.reconciliation_details(
                    job.id,
                    reason="application_restart",
                ),
            )
            interrupted.append(updated)
            self._emit(
                SchedulerEventType.RECOVERED,
                project_id=job.project_id,
                job_id=job.id,
                status=updated.status,
                message="Interrupted running job requires reconciliation; it was not resent.",
            )
        return interrupted

    async def _wait_until_permitted(self) -> bool:
        if self._stop_dispatch.is_set():
            return False
        if self._resume_gate.is_set():
            return True
        resume_waiter = asyncio.create_task(self._resume_gate.wait())
        stop_waiter = asyncio.create_task(self._stop_dispatch.wait())
        done, pending = await asyncio.wait(
            (resume_waiter, stop_waiter),
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if stop_waiter in done and stop_waiter.result():
            return False
        return self._resume_gate.is_set() and not self._stop_dispatch.is_set()

    def _on_progress(self, stage: str, job: GenerationJob, message: str) -> None:
        self._emit(
            SchedulerEventType.PROGRESS,
            project_id=job.project_id,
            job_id=job.id,
            status=job.status,
            message=f"{stage}: {message}".strip(),
        )

    def _mark_completed(self, job_id: str) -> None:
        if job_id not in self._dispatch_ids or job_id in self._completed_ids:
            return
        self._completed_ids.add(job_id)
        self._completed += 1

    def _emit(
        self,
        event_type: SchedulerEventType,
        *,
        project_id: str | None,
        job_id: str | None,
        status: JobStatus | None,
        message: str,
    ) -> None:
        event = SchedulerEvent(
            event_type=event_type,
            project_id=project_id,
            job_id=job_id,
            status=status,
            completed=self._completed,
            total=self._total,
            message=message,
        )
        for listener in tuple(self._listeners):
            try:
                listener(event)
            except Exception:
                logger.exception("Scheduler event listener failed.")
