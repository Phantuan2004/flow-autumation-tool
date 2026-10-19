from __future__ import annotations

import asyncio
import logging
from concurrent.futures import Future
from typing import TYPE_CHECKING, Any

from PyQt6.QtCore import QThread, pyqtSignal

from flow_automation.application.image_gallery import ImageGalleryService
from flow_automation.application.job_control import JobControlService
from flow_automation.application.projects import ProjectService
from flow_automation.bootstrap import create_flow_repository
from flow_automation.automation.browser_provider import BrowserProvider
from flow_automation.automation.errors import (
    BrowserAlreadyRunningError,
    BrowserLifecycleError,
)
from flow_automation.automation.flow_adapter import FlowAdapter
from flow_automation.automation.session_manager import SessionManager, SessionStatus
from flow_automation.core.models import JobStatus, JobType
from flow_automation.jobs.image_processor import FlowImageJobProcessor
from flow_automation.jobs.retry_policy import RetryPolicy
from flow_automation.jobs.scheduler import BatchScheduler
from flow_automation.persistence import Database, FlowRepository
from flow_automation.storage.media_verifier import MediaVerifier

if TYPE_CHECKING:
    from flow_automation.automation.browser_manager import BrowserConfig

logger = logging.getLogger(__name__)


class BrowserWorker(QThread):
    """Owns the browser and Playwright event loop away from the UI thread."""

    status_changed = pyqtSignal(str)
    connection_checked = pyqtSignal(str)
    projects_loaded = pyqtSignal(object)
    gallery_loaded = pyqtSignal(object)
    operation_completed = pyqtSignal(str, object)
    scheduler_event = pyqtSignal(object)

    def __init__(self, config: BrowserConfig) -> None:
        super().__init__()
        self._config = config
        self._loop: asyncio.AbstractEventLoop | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._provider: BrowserProvider | None = None
        self._database: Database | None = None
        self._repository: FlowRepository | None = None
        self._adapter: FlowAdapter | None = None
        self._project_service: ProjectService | None = None
        self._gallery_service: ImageGalleryService | None = None
        self._job_control: JobControlService | None = None
        self._batch_task: asyncio.Task[Any] | None = None

    def run(self) -> None:
        try:
            asyncio.run(self._serve())
        except Exception:
            logger.exception("Browser worker stopped unexpectedly.")
            self.status_changed.emit(
                "Browser stopped unexpectedly. Check the application log."
            )

    def request_connection_check(self) -> None:
        self._schedule(
            self._check_connection,
            self._report_connection_result,
            on_unavailable=lambda: self.connection_checked.emit(
                "Browser is not ready. Open it before checking the connection."
            ),
        )

    def request_projects(self) -> None:
        self._schedule(
            self._load_projects,
            self._report_projects,
            operation="projects",
        )

    def request_create_project(self, name: str) -> None:
        self._schedule(
            lambda: self._create_project(name),
            self._report_projects,
            operation="create_project",
        )

    def request_add_scene(self, project_id: str, image_prompt: str) -> None:
        self._schedule(
            lambda: self._add_scene(project_id, image_prompt),
            self._report_projects,
            operation="add_scene",
        )

    def request_load_gallery(self, project_id: str) -> None:
        self._schedule(
            lambda: self._load_gallery(project_id),
            self._report_gallery,
            operation="gallery",
        )

    def request_approve_image(self, scene_id: str, asset_id: str) -> None:
        self._schedule(
            lambda: self._approve_image(scene_id, asset_id),
            self._report_approval,
            operation="approve",
        )

    def request_queue_images(self, project_id: str) -> None:
        self._schedule(
            lambda: self._queue_images(project_id),
            self._report_operation,
            operation="queue",
        )

    def request_start_images(self, project_id: str) -> None:
        self._schedule(
            lambda: self._start_images(project_id),
            self._report_operation,
            operation="start",
        )

    def request_pause(self) -> None:
        self._schedule(
            self._pause,
            self._report_operation,
            operation="pause",
        )

    def request_resume(self) -> None:
        self._schedule(
            self._resume,
            self._report_operation,
            operation="resume",
        )

    def request_cancel_pending(self, project_id: str) -> None:
        self._schedule(
            lambda: self._cancel_pending(project_id),
            self._report_operation,
            operation="cancel",
        )

    def request_retry_failed(self, project_id: str) -> None:
        self._schedule(
            lambda: self._retry_failed(project_id),
            self._report_operation,
            operation="retry",
        )

    def request_stop(self) -> None:
        loop = self._loop
        shutdown_event = self._shutdown_event
        if loop is not None and loop.is_running() and shutdown_event is not None:
            loop.call_soon_threadsafe(shutdown_event.set)

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._shutdown_event = asyncio.Event()
        provider = BrowserProvider(self._config)
        self._provider = provider
        try:
            await provider.start()
        except (BrowserAlreadyRunningError, BrowserLifecycleError) as error:
            self.status_changed.emit(str(error))
            self._provider = None
            return

        try:
            self._initialize_workspace(provider)
            self.status_changed.emit(
                "Chrome is open. Sign in manually, then select Check Connection."
            )
            browser_closed = asyncio.create_task(provider.wait_until_closed())
            shutdown_requested = asyncio.create_task(self._shutdown_event.wait())
            done, pending = await asyncio.wait(
                (browser_closed, shutdown_requested),
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()
        finally:
            await self._stop_batch()
            await provider.close()
            if self._database is not None:
                self._database.close()
                self._database = None
            self._provider = None
            self._shutdown_event = None
            self._loop = None
            self.status_changed.emit("Browser closed.")

    def _initialize_workspace(self, provider: BrowserProvider) -> None:
        profile_root = self._config.profile_path.parent
        database_path = self._config.database_path or (
            profile_root / "flow_automation.sqlite3"
        )
        assets_root = self._config.assets_path or (profile_root / "assets")
        database, repository = create_flow_repository(database_path)
        self._database = database
        self._repository = repository
        adapter = FlowAdapter(provider, SessionManager())
        processor = FlowImageJobProcessor(
            repository,
            adapter,
            result_timeout=self._config.default_timeout_ms / 1000 * 20,
            verifier=MediaVerifier(),
        )
        job_control = JobControlService(
            self._create_scheduler(repository, processor)
        )
        job_control.subscribe(self.scheduler_event.emit)
        self._adapter = adapter
        self._project_service = ProjectService(repository, assets_root)
        self._gallery_service = ImageGalleryService(repository)
        self._job_control = job_control
        recovered = job_control.recover_after_restart()
        if recovered:
            self.status_changed.emit(
                f"Recovery found {len(recovered)} interrupted job(s). "
                "Review job state before retrying."
            )

    def _create_scheduler(
        self,
        repository: FlowRepository,
        processor: FlowImageJobProcessor,
    ) -> BatchScheduler:
        return BatchScheduler(
            repository,
            processor,
            concurrency=1,
            browser_bound=True,
            retry_policy=RetryPolicy(
                base_delay_seconds=self._config.retry_backoff_seconds,
                max_delay_seconds=max(
                    self._config.retry_backoff_seconds,
                    60.0,
                ),
            ),
        )

    def _schedule(
        self,
        coroutine_factory: Any,
        callback: Any,
        *,
        operation: str = "",
        on_unavailable: Any = None,
    ) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            if on_unavailable is not None:
                on_unavailable()
            else:
                self.operation_completed.emit(
                    operation,
                    {"ok": False, "message": "Browser worker is not ready."},
                )
            return
        future = asyncio.run_coroutine_threadsafe(coroutine_factory(), loop)
        future.add_done_callback(
            lambda completed: callback(completed, operation)
            if operation
            else callback(completed)
        )

    def _require_workspace(self) -> tuple[
        FlowAdapter,
        ProjectService,
        ImageGalleryService,
        JobControlService,
    ]:
        if (
            self._adapter is None
            or self._project_service is None
            or self._gallery_service is None
            or self._job_control is None
        ):
            raise RuntimeError("Application workspace has not been initialized.")
        return (
            self._adapter,
            self._project_service,
            self._gallery_service,
            self._job_control,
        )

    async def _load_projects(self) -> list[dict[str, Any]]:
        projects = self._require_workspace()[1].list_projects()
        repository = self._repository
        if repository is None:
            raise RuntimeError("Application repository has not been initialized.")
        return [
            {
                "project": project,
                "scene_count": len(repository.list_scenes(project.id)),
                "queued_count": len(
                    repository.list_jobs(
                        project_id=project.id,
                        job_type=JobType.IMAGE,
                        statuses=(JobStatus.PENDING, JobStatus.RETRY_WAIT),
                    )
                ),
            }
            for project in projects
        ]

    async def _create_project(self, name: str) -> list[dict[str, Any]]:
        _, projects, _, _ = self._require_workspace()
        projects.create_project(name)
        return await self._load_projects()

    async def _add_scene(
        self,
        project_id: str,
        prompt: str,
    ) -> list[dict[str, Any]]:
        _, projects, _, _ = self._require_workspace()
        projects.add_scene(project_id, prompt)
        return await self._load_projects()

    async def _load_gallery(self, project_id: str) -> list[dict[str, Any]]:
        _, _, gallery, _ = self._require_workspace()
        return [
            {
                "scene_id": item.scene.id,
                "scene_number": item.scene.scene_number,
                "prompt": item.scene.image_prompt,
                "asset_id": item.asset.id,
                "path": item.path,
                "selected": item.selected,
                "verification_status": item.asset.verification_status.value,
            }
            for item in gallery.list_project_images(project_id)
        ]

    async def _approve_image(self, scene_id: str, asset_id: str) -> dict[str, str]:
        _, _, gallery, _ = self._require_workspace()
        scene = gallery.approve_image(scene_id, asset_id)
        return {"project_id": scene.project_id, "message": "Image approved."}

    async def _queue_images(self, project_id: str) -> dict[str, Any]:
        adapter, _, _, job_control = self._require_workspace()
        await adapter.require_image_workspace()
        jobs = await job_control.queue_project(
            project_id,
            JobType.IMAGE,
            retry_limit=self._config.max_attempts - 1,
        )
        repository = self._repository
        if repository is None:
            raise RuntimeError("Application repository has not been initialized.")
        queued_count = len(
            repository.list_jobs(
                project_id=project_id,
                job_type=JobType.IMAGE,
                statuses=(JobStatus.PENDING, JobStatus.RETRY_WAIT),
            )
        )
        return {
            "project_id": project_id,
            "count": len(jobs),
            "queued_count": queued_count,
            "message": (
                f"Added {len(jobs)} image job(s); {queued_count} queued job(s) "
                "are available to start."
            ),
        }

    async def _start_images(self, project_id: str) -> dict[str, Any]:
        adapter, _, _, job_control = self._require_workspace()
        await adapter.require_image_workspace()
        self._launch_batch(project_id, job_control)
        return {
            "project_id": project_id,
            "message": "Image batch started. Browser work is serialized.",
        }

    def _launch_batch(
        self,
        project_id: str,
        job_control: JobControlService,
    ) -> None:
        if self._batch_task is not None and not self._batch_task.done():
            raise RuntimeError("An image batch is already running.")
        self._batch_task = asyncio.create_task(
            job_control.start(project_id, job_type=JobType.IMAGE)
        )
        self._batch_task.add_done_callback(
            lambda task: self._report_batch_finished(task, project_id)
        )

    async def _pause(self) -> dict[str, str]:
        await self._require_workspace()[3].pause()
        return {"message": "Pause requested; no new job will start."}

    async def _resume(self) -> dict[str, str]:
        await self._require_workspace()[3].resume()
        return {"message": "Batch resumed."}

    async def _cancel_pending(self, project_id: str) -> dict[str, Any]:
        cancelled = await self._require_workspace()[3].cancel_pending(
            project_id,
            job_type=JobType.IMAGE,
        )
        return {
            "message": (
                f"Cancelled {cancelled} queued job(s). Already-submitted Flow "
                "tasks are not remotely cancelled."
            ),
        }

    async def _retry_failed(self, project_id: str) -> dict[str, Any]:
        adapter, _, _, job_control = self._require_workspace()
        await adapter.require_image_workspace()
        if self._batch_task is not None and not self._batch_task.done():
            raise RuntimeError("Wait for the active image batch to finish before retrying.")
        repository = self._repository
        if repository is None:
            raise RuntimeError("Application repository has not been initialized.")

        jobs = repository.list_jobs(
            project_id=project_id,
            job_type=JobType.IMAGE,
            statuses=(JobStatus.FAILED,),
        )
        retryable: list[str] = []
        for job in jobs:
            try:
                await job_control.retry(job.id)
            except ValueError:
                logger.info(
                    "Image job is not eligible for retry: job_id=%s",
                    job.id,
                )
                continue
            retryable.append(job.id)
        if retryable:
            self._launch_batch(project_id, job_control)
        return {
            "project_id": project_id,
            "count": len(retryable),
            "message": (
                f"Started {len(retryable)} eligible failed job retry attempt(s)."
                if retryable
                else "No failed image jobs are eligible for retry."
            ),
        }

    async def _stop_batch(self) -> None:
        task = self._batch_task
        job_control = self._job_control
        if task is None or task.done():
            return
        if job_control is not None:
            await job_control.stop_dispatch()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=10)
        except TimeoutError:
            logger.warning("Image batch did not stop within shutdown grace period.")
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._batch_task = None

    def _report_projects(self, future: Future[Any], operation: str) -> None:
        try:
            projects = future.result()
        except Exception as error:
            logger.exception("Workspace operation failed: %s", operation)
            self.operation_completed.emit(
                operation,
                {"ok": False, "message": str(error)},
            )
            return
        self.projects_loaded.emit(projects)
        self.operation_completed.emit(operation, {"ok": True, "message": "Done."})

    def _report_gallery(self, future: Future[Any], operation: str) -> None:
        try:
            items = future.result()
        except Exception as error:
            logger.exception("Gallery operation failed.")
            self.operation_completed.emit(
                operation,
                {"ok": False, "message": str(error)},
            )
            return
        self.gallery_loaded.emit(items)

    def _report_approval(self, future: Future[Any], operation: str) -> None:
        try:
            result = future.result()
        except Exception as error:
            logger.exception("Image approval failed.")
            self.operation_completed.emit(
                operation,
                {"ok": False, "message": str(error)},
            )
            return
        self.operation_completed.emit(operation, {"ok": True, **result})
        self.request_load_gallery(result["project_id"])

    def _report_operation(self, future: Future[Any], operation: str) -> None:
        try:
            result = future.result()
        except Exception as error:
            logger.exception("Job operation failed: %s", operation)
            self.operation_completed.emit(
                operation,
                {"ok": False, "message": str(error)},
            )
            return
        self.operation_completed.emit(operation, {"ok": True, **result})

    def _report_batch_finished(
        self,
        task: asyncio.Task[Any],
        project_id: str,
    ) -> None:
        try:
            results = task.result()
        except asyncio.CancelledError:
            message = "Batch stopped locally; any submitted Flow task may still run."
            logger.warning(message)
        except Exception:
            logger.exception("Image batch stopped unexpectedly.")
            message = "Image batch failed. Check job history and application log."
        else:
            message = f"Image batch finished; {len(results)} job result(s) processed."
        self.operation_completed.emit(
            "batch_finished",
            {"project_id": project_id, "message": message},
        )

    async def _check_connection(self) -> SessionStatus:
        provider = self._provider
        if provider is None:
            return SessionStatus.BROWSER_UNAVAILABLE
        adapter = FlowAdapter(provider, SessionManager())
        return await adapter.check_session()

    def _report_connection_result(self, future: Future[SessionStatus]) -> None:
        try:
            status = future.result()
        except Exception:
            logger.exception("Flow connection check failed.")
            message = "Connection check failed safely. See the application log."
        else:
            message = {
                SessionStatus.AUTHENTICATED: (
                    "Connected: Flow dashboard access verified. "
                    "Media capabilities are not verified."
                ),
                SessionStatus.UNAUTHENTICATED: "Not signed in to Flow. Sign in manually.",
                SessionStatus.AUTH_REQUIRED: (
                    "Google sign-in is required. Complete it manually in Chrome."
                ),
                SessionStatus.UNKNOWN: (
                    "Browser is connected, but Flow workspace access could not "
                    "be verified. No automation job was submitted."
                ),
                SessionStatus.BROWSER_UNAVAILABLE: (
                    "Browser is unavailable. Open Chrome and try again."
                ),
            }[status]
        self.connection_checked.emit(message)
