from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from inspect import isawaitable
from pathlib import Path

from flow_automation.automation.errors import AuthenticationRequiredError
from flow_automation.core.models import (
    GenerationJob,
    JobStatus,
    JobType,
    SceneDraft,
)
from flow_automation.bootstrap import create_job_control_service
from flow_automation.application.job_control import JobControlService
from flow_automation.jobs.checkpoints import CheckpointStore
from flow_automation.jobs.job_runner import (
    JobExecutionContext,
    JobExecutionResult,
)
from flow_automation.jobs.retry_policy import RetryPolicy, RetryableJobError
from flow_automation.jobs.scheduler import (
    BatchScheduler,
    SchedulerEvent,
    SchedulerEventType,
)
from flow_automation.persistence.database import Database
from flow_automation.persistence.repository import FlowRepository


class FakeProcessor:
    def __init__(
        self,
        callback: Callable[
            [GenerationJob, JobExecutionContext],
            JobExecutionResult | Awaitable[JobExecutionResult] | None,
        ]
        | None = None,
    ) -> None:
        self.callback = callback
        self.calls: list[str] = []
        self.active = 0
        self.max_active = 0

    async def execute(
        self,
        job: GenerationJob,
        context: JobExecutionContext,
    ) -> JobExecutionResult:
        self.calls.append(job.id)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0)
            if self.callback is not None:
                result = self.callback(job, context)
                if isawaitable(result):
                    result = await result
                if result is not None:
                    return result
            return JobExecutionResult(result_verified=True)
        finally:
            self.active -= 1


def create_repository(path: Path) -> tuple[Database, FlowRepository]:
    database = Database.from_path(path)
    database.initialize()
    return database, FlowRepository(database)


def test_scheduler_runs_a_60_scene_batch_with_limited_concurrency(
    tmp_path: Path,
) -> None:
    async def run() -> tuple[list[GenerationJob], FakeProcessor, list[SchedulerEvent]]:
        database, repository = create_repository(tmp_path / "scheduler-60.sqlite3")
        project = repository.create_project("Sixty scenes", str(tmp_path / "project"))
        repository.create_scenes(
            project.id,
            [SceneDraft(index, f"image prompt {index}") for index in range(1, 61)],
        )
        processor = FakeProcessor()
        scheduler = BatchScheduler(
            repository,
            processor,
            concurrency=3,
            browser_bound=False,
        )
        events: list[SchedulerEvent] = []
        scheduler.subscribe(events.append)
        queued = await scheduler.queue_project(project.id, retry_limit=0)
        results = await scheduler.start(project.id)

        assert len(queued) == 60
        assert len(results) == 60
        assert all(job.status is JobStatus.SUCCEEDED for job in results)
        assert processor.max_active == 3
        assert len(
            [
                event
                for event in events
                if event.event_type is SchedulerEventType.COMPLETED
            ]
        ) == 1
        database.close()
        return results, processor, events

    results, processor, events = asyncio.run(run())
    assert len(results) == len(processor.calls) == 60
    assert any(event.event_type is SchedulerEventType.PROGRESS for event in events)


def test_pause_blocks_new_jobs_and_resume_continues_queue(tmp_path: Path) -> None:
    async def run() -> tuple[list[GenerationJob], list[str]]:
        database, repository = create_repository(tmp_path / "pause.sqlite3")
        project = repository.create_project("Pause", str(tmp_path / "pause"))
        repository.create_scenes(
            project.id,
            [SceneDraft(index, f"prompt {index}") for index in range(1, 4)],
        )
        entered = asyncio.Event()
        finish_first = asyncio.Event()
        starts: list[str] = []

        async def callback(
            job: GenerationJob,
            context: JobExecutionContext,
        ) -> JobExecutionResult:
            del context
            starts.append(job.id)
            if len(starts) == 1:
                entered.set()
                await finish_first.wait()
            return JobExecutionResult(result_verified=True)

        scheduler = BatchScheduler(
            repository,
            FakeProcessor(callback),
            concurrency=1,
        )
        await scheduler.queue_project(project.id)
        dispatch = asyncio.create_task(scheduler.start(project.id))
        await asyncio.wait_for(entered.wait(), timeout=5)
        await scheduler.pause()
        finish_first.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(starts) == 1
        assert not dispatch.done()

        await scheduler.resume()
        results = await asyncio.wait_for(dispatch, timeout=5)
        assert len(starts) == 3
        assert all(job.status is JobStatus.SUCCEEDED for job in results)
        database.close()
        return results, starts

    results, starts = asyncio.run(run())
    assert len(results) == len(starts) == 3


def test_cancel_waiting_job_and_safe_active_job(tmp_path: Path) -> None:
    async def run() -> tuple[JobStatus, JobStatus, list[str]]:
        database, repository = create_repository(tmp_path / "cancel.sqlite3")
        project = repository.create_project("Cancel", str(tmp_path / "cancel"))
        (scene_a, scene_b) = repository.create_scenes(
            project.id,
            [SceneDraft(1, "first"), SceneDraft(2, "second")],
        )
        active = repository.create_job(project.id, scene_a.id, JobType.IMAGE)
        queued = repository.create_job(project.id, scene_b.id, JobType.IMAGE)
        entered = asyncio.Event()
        allow_checkpoint = asyncio.Event()

        async def callback(
            job: GenerationJob,
            context: JobExecutionContext,
        ) -> JobExecutionResult:
            del job
            entered.set()
            await allow_checkpoint.wait()
            await context.checkpoint("before_external_submission")
            return JobExecutionResult(result_verified=True)

        scheduler = BatchScheduler(repository, FakeProcessor(callback))
        dispatch = asyncio.create_task(scheduler.start(project.id))
        await entered.wait()
        queued_outcome = await scheduler.cancel(queued.id)
        active_outcome = await scheduler.cancel(active.id)
        allow_checkpoint.set()
        await dispatch
        statuses = (
            repository.get_job(active.id).status,
            repository.get_job(queued.id).status,
        )
        database.close()
        return statuses[0], statuses[1], [queued_outcome, active_outcome]

    active_status, queued_status, outcomes = asyncio.run(run())
    assert active_status is JobStatus.CANCELLED
    assert queued_status is JobStatus.CANCELLED
    assert outcomes == [
        "cancelled_before_start",
        "cancellation_requested",
    ]


def test_retry_limit_authentication_and_uncertain_outcomes(tmp_path: Path) -> None:
    async def run() -> tuple[JobStatus, int, JobStatus, int, JobStatus, int]:
        database, repository = create_repository(tmp_path / "retry.sqlite3")
        project = repository.create_project("Retries", str(tmp_path / "retry"))
        scenes = repository.create_scenes(
            project.id,
            [SceneDraft(index, f"prompt {index}") for index in range(1, 4)],
        )
        retry_job = repository.create_job(
            project.id,
            scenes[0].id,
            JobType.IMAGE,
            retry_limit=1,
        )
        limited_job = repository.create_job(
            project.id,
            scenes[1].id,
            JobType.IMAGE,
            retry_limit=1,
        )
        auth_job = repository.create_job(
            project.id,
            scenes[2].id,
            JobType.IMAGE,
            retry_limit=3,
        )
        calls: dict[str, int] = {}

        async def callback(
            job: GenerationJob,
            context: JobExecutionContext,
        ) -> JobExecutionResult:
            del context
            calls[job.id] = calls.get(job.id, 0) + 1
            if job.id == retry_job.id and calls[job.id] == 1:
                raise RetryableJobError("temporary")
            if job.id == limited_job.id:
                raise RetryableJobError("keeps failing")
            if job.id == auth_job.id:
                raise AuthenticationRequiredError("sign in manually")
            return JobExecutionResult(result_verified=True)

        scheduler = BatchScheduler(
            repository,
            FakeProcessor(callback),
            retry_policy=RetryPolicy(base_delay_seconds=0, max_delay_seconds=0),
        )
        await scheduler.start(project.id)
        statuses = [
            repository.get_job(job.id).status
            for job in (retry_job, limited_job, auth_job)
        ]
        database.close()
        return (
            statuses[0],
            calls.get(retry_job.id, 0),
            statuses[1],
            calls.get(limited_job.id, 0),
            statuses[2],
            calls.get(auth_job.id, 0),
        )

    retried, retry_calls, limited, limit_calls, auth, auth_calls = asyncio.run(run())
    assert (retried, retry_calls) == (JobStatus.SUCCEEDED, 2)
    assert (limited, limit_calls) == (JobStatus.FAILED, 2)
    assert (auth, auth_calls) == (JobStatus.FAILED, 1)


def test_uncertain_submission_is_not_retried_and_restart_requires_reconciliation(
    tmp_path: Path,
) -> None:
    async def run() -> tuple[JobStatus, JobStatus, int, str, str]:
        database_path = tmp_path / "recovery.sqlite3"
        database, repository = create_repository(database_path)
        project = repository.create_project("Recovery", str(tmp_path / "recovery"))
        scenes = repository.create_scenes(
            project.id,
            [SceneDraft(1, "uncertain"), SceneDraft(2, "crashed")],
        )
        uncertain = repository.create_job(
            project.id,
            scenes[0].id,
            JobType.IMAGE,
            retry_limit=4,
        )

        async def uncertain_callback(
            job: GenerationJob,
            context: JobExecutionContext,
        ) -> JobExecutionResult:
            del job
            await context.checkpoint(
                "external_submission_possible",
                external_submission_possible=True,
            )
            raise RetryableJobError("connection dropped after submit")

        uncertain_scheduler = BatchScheduler(
            repository,
            FakeProcessor(uncertain_callback),
            retry_policy=RetryPolicy(base_delay_seconds=0, max_delay_seconds=0),
        )
        await uncertain_scheduler.start(project.id)
        uncertain_job = repository.get_job(uncertain.id)
        uncertain_status = uncertain_job.status
        uncertain_reason = str(uncertain_job.reconciliation_details.get("reason"))
        assert len(repository.list_job_events(uncertain.id)) >= 4

        crashed = repository.create_job(project.id, scenes[1].id, JobType.IMAGE)
        repository.transition_job(crashed.id, JobStatus.RUNNING, message="Started.")
        checkpoints = CheckpointStore(repository)
        checkpoints.record(
            crashed.id,
            "external_submission_possible",
            external_submission_possible=True,
        )
        database.close()

        restarted_database, restarted_repository = create_repository(database_path)
        recovery_processor = FakeProcessor()
        recovery_scheduler = BatchScheduler(restarted_repository, recovery_processor)
        recovered = recovery_scheduler.recover_after_restart(project.id)
        recovered_job = restarted_repository.get_job(crashed.id)
        details = recovered_job.reconciliation_details
        assert recovered == [recovered_job]
        restarted_database.close()
        return (
            uncertain_status,
            recovered_job.status,
            len(recovery_processor.calls),
            str(details.get("last_checkpoint")),
            uncertain_reason,
        )

    uncertain, recovered, recovery_calls, checkpoint, uncertain_reason = asyncio.run(run())
    assert uncertain is JobStatus.RECONCILIATION_REQUIRED
    assert recovered is JobStatus.RECONCILIATION_REQUIRED
    assert recovery_calls == 0
    assert checkpoint == "external_submission_possible"
    assert uncertain_reason == "uncertain_execution_outcome"


def test_cancel_after_submission_requires_reconciliation(tmp_path: Path) -> None:
    async def run() -> JobStatus:
        database, repository = create_repository(tmp_path / "cancel-uncertain.sqlite3")
        project = repository.create_project("Uncertain cancel", str(tmp_path / "cancel"))
        (scene,) = repository.create_scenes(project.id, [SceneDraft(1, "prompt")])
        job = repository.create_job(project.id, scene.id, JobType.IMAGE)
        submission_started = asyncio.Event()
        finish = asyncio.Event()

        async def callback(
            job: GenerationJob,
            context: JobExecutionContext,
        ) -> JobExecutionResult:
            del job
            await context.checkpoint(
                "external_submission_possible",
                external_submission_possible=True,
            )
            submission_started.set()
            await finish.wait()
            return JobExecutionResult(result_verified=True)

        scheduler = BatchScheduler(repository, FakeProcessor(callback))
        dispatch = asyncio.create_task(scheduler.start(project.id))
        await asyncio.wait_for(submission_started.wait(), timeout=5)
        assert (
            await scheduler.cancel(job.id)
            == "cancellation_requested"
        )
        finish.set()
        await asyncio.wait_for(dispatch, timeout=5)
        final_job = repository.get_job(job.id)
        database.close()
        assert (
            final_job.reconciliation_details["reason"]
            == "local_cancel_after_possible_submission"
        )
        return final_job.status

    assert asyncio.run(run()) is JobStatus.RECONCILIATION_REQUIRED


def test_cancel_pending_stops_dispatch_without_claiming_remote_cancel(
    tmp_path: Path,
) -> None:
    async def run() -> tuple[list[JobStatus], int, list[SchedulerEvent]]:
        database, repository = create_repository(tmp_path / "cancel-pending.sqlite3")
        project = repository.create_project("Stop dispatch", str(tmp_path / "stop"))
        repository.create_scenes(
            project.id,
            [SceneDraft(index, f"prompt {index}") for index in range(1, 4)],
        )
        entered = asyncio.Event()
        release = asyncio.Event()

        async def callback(
            job: GenerationJob,
            context: JobExecutionContext,
        ) -> JobExecutionResult:
            del job
            entered.set()
            await release.wait()
            await context.checkpoint("before_external_submission")
            return JobExecutionResult(result_verified=True)

        processor = FakeProcessor(callback)
        scheduler = BatchScheduler(repository, processor)
        events: list[SchedulerEvent] = []
        scheduler.subscribe(events.append)
        await scheduler.queue_project(project.id)
        dispatch = asyncio.create_task(scheduler.start(project.id))
        await asyncio.wait_for(entered.wait(), timeout=5)
        cancelled_queued = await scheduler.cancel_pending(project.id)
        release.set()
        await asyncio.wait_for(dispatch, timeout=5)
        statuses = [
            job.status for job in repository.list_jobs(project_id=project.id)
        ]
        database.close()
        return statuses, cancelled_queued, events

    statuses, cancelled_queued, events = asyncio.run(run())
    assert cancelled_queued == 2
    assert statuses.count(JobStatus.CANCELLED) == 3
    assert max(event.completed for event in events) == 3


def test_unverified_processor_result_requires_reconciliation(tmp_path: Path) -> None:
    async def run() -> JobStatus:
        database, repository = create_repository(tmp_path / "unverified.sqlite3")
        project = repository.create_project("Unverified", str(tmp_path / "unverified"))
        (scene,) = repository.create_scenes(project.id, [SceneDraft(1, "prompt")])
        job = repository.create_job(project.id, scene.id, JobType.IMAGE)

        async def callback(
            job: GenerationJob,
            context: JobExecutionContext,
        ) -> JobExecutionResult:
            del job, context
            return JobExecutionResult(result_verified=False)

        scheduler = BatchScheduler(
            repository,
            FakeProcessor(callback),
        )
        await scheduler.start(project.id)
        status = repository.get_job(job.id).status
        database.close()
        return status

    assert asyncio.run(run()) is JobStatus.RECONCILIATION_REQUIRED


def test_application_control_service_forwards_commands_and_progress(
    tmp_path: Path,
) -> None:
    async def run() -> tuple[JobStatus, list[SchedulerEvent]]:
        database, repository = create_repository(tmp_path / "control-service.sqlite3")
        project = repository.create_project("Application boundary", str(tmp_path / "app"))
        repository.create_scenes(project.id, [SceneDraft(1, "prompt")])
        controls: JobControlService = create_job_control_service(
            repository,
            FakeProcessor(),
        )
        events: list[SchedulerEvent] = []
        controls.subscribe(events.append)
        await controls.pause()
        await controls.resume()
        jobs = await controls.queue_project(project.id, JobType.IMAGE, retry_limit=0)
        results = await controls.start(project.id)
        database.close()
        assert len(jobs) == len(results) == 1
        return results[0].status, events

    status, events = asyncio.run(run())
    assert status is JobStatus.SUCCEEDED
    assert any(event.event_type is SchedulerEventType.PAUSED for event in events)
    assert any(event.event_type is SchedulerEventType.RESUMED for event in events)
    assert any(event.event_type is SchedulerEventType.PROGRESS for event in events)
