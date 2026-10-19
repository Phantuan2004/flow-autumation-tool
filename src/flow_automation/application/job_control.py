from __future__ import annotations

from collections.abc import Callable

from flow_automation.core.models import GenerationJob, JobType
from flow_automation.jobs.scheduler import BatchScheduler, SchedulerEvent


class JobControlService:
    """Application boundary for queue commands and push-based progress events."""

    def __init__(self, scheduler: BatchScheduler) -> None:
        self._scheduler = scheduler

    def subscribe(self, listener: Callable[[SchedulerEvent], None]) -> Callable[[], None]:
        return self._scheduler.subscribe(listener)

    async def queue_project(
        self,
        project_id: str,
        job_type: JobType = JobType.IMAGE,
        *,
        retry_limit: int = 3,
    ) -> list[GenerationJob]:
        return await self._scheduler.queue_project(
            project_id,
            job_type,
            retry_limit=retry_limit,
        )

    async def start(
        self,
        project_id: str | None = None,
        *,
        job_type: JobType | None = None,
    ) -> list[GenerationJob]:
        return await self._scheduler.start(project_id, job_type=job_type)

    async def pause(self) -> None:
        await self._scheduler.pause()

    async def resume(self) -> None:
        await self._scheduler.resume()

    async def cancel(self, job_id: str) -> str:
        return await self._scheduler.cancel(job_id)

    async def cancel_pending(
        self,
        project_id: str | None = None,
        *,
        job_type: JobType | None = None,
    ) -> int:
        return await self._scheduler.cancel_pending(project_id, job_type=job_type)

    async def retry(self, job_id: str) -> None:
        await self._scheduler.retry(job_id)

    async def stop_dispatch(self) -> None:
        await self._scheduler.stop_dispatch()

    def recover_after_restart(
        self,
        project_id: str | None = None,
    ) -> list[GenerationJob]:
        return self._scheduler.recover_after_restart(project_id)
