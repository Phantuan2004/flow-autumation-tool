from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

from flow_automation.automation.flow_adapter import (
    FlowAsset,
    FlowTask,
    ImageRequest,
)
from flow_automation.automation.errors import (
    AuthenticationRequiredError,
    FlowSubmissionNotSentError,
    UnsupportedOperationError,
)
from flow_automation.core.models import (
    AssetKind,
    FileVerificationStatus,
    GenerationJob,
    JobType,
)
from flow_automation.jobs.job_runner import (
    JobExecutionContext,
    JobExecutionResult,
)
from flow_automation.persistence.repository import FlowRepository
from flow_automation.storage.media_verifier import MediaVerifier


class ImageGenerationAdapter(Protocol):
    async def create_image(self, request: ImageRequest) -> FlowTask: ...

    async def wait_for_result(self, task: FlowTask, timeout: float) -> FlowTask: ...

    async def collect_images(self, task: FlowTask) -> list[FlowAsset]: ...

    async def download_asset(self, asset: FlowAsset, destination: Path) -> Path: ...


class FlowImageJobProcessor:
    """Runs one persisted scene image job through Flow and verified asset storage."""

    def __init__(
        self,
        repository: FlowRepository,
        adapter: ImageGenerationAdapter,
        *,
        result_timeout: float = 300,
        verifier: MediaVerifier | None = None,
    ) -> None:
        if result_timeout <= 0:
            raise ValueError("Image result timeout must be greater than zero.")
        self._repository = repository
        self._adapter = adapter
        self._result_timeout = result_timeout
        self._verifier = verifier or MediaVerifier()

    async def execute(
        self,
        job: GenerationJob,
        context: JobExecutionContext,
    ) -> JobExecutionResult:
        if job.job_type is not JobType.IMAGE:
            raise ValueError("FlowImageJobProcessor only accepts image jobs.")
        scene = self._repository.get_scene(job.scene_id)
        project = self._repository.get_project(job.project_id)
        prompt = scene.image_prompt.strip()
        if not prompt:
            raise ValueError("Scene image prompt must not be empty.")

        await context.checkpoint("before_external_submission")
        context.mark_external_submission_possible(True)
        try:
            task = await self._adapter.create_image(ImageRequest(prompt))
        except (
            AuthenticationRequiredError,
            FlowSubmissionNotSentError,
            UnsupportedOperationError,
            ValueError,
        ):
            context.mark_external_submission_possible(False)
            raise
        await context.checkpoint(
            "external_submission_possible",
            external_submission_possible=True,
        )
        await self._adapter.wait_for_result(task, timeout=self._result_timeout)
        await context.checkpoint(
            "result_observed",
            external_submission_possible=True,
        )
        assets = await self._adapter.collect_images(task)
        if len(assets) != 1:
            raise ValueError(
                "An x1 Flow image job must produce exactly one collectable image."
            )

        asset = assets[0]
        filename = self._safe_filename(asset.filename)
        project_root = Path(project.root_path).resolve()
        destination = (
            project_root
            / "scenes"
            / f"{scene.scene_number:03d}"
            / f"{filename}-{job.id[:8]}.jpg"
        )
        if destination.exists():
            raise FileExistsError(
                "The image destination already exists; refusing to overwrite it."
            )
        downloaded_path = await self._adapter.download_asset(asset, destination)
        resolved_path = downloaded_path.resolve()
        if not resolved_path.is_relative_to(project_root):
            raise ValueError("Downloaded image path escaped the project directory.")
        verified = self._verifier.verify_image(resolved_path)
        stored_asset = self._repository.create_asset(
            project.id,
            resolved_path.relative_to(project_root).as_posix(),
            AssetKind.IMAGE,
            scene_id=scene.id,
            job_id=job.id,
            original_filename=resolved_path.name,
            mime_type=verified.mime_type,
            size_bytes=verified.size_bytes,
            sha256=verified.sha256,
            verification_status=FileVerificationStatus.VERIFIED,
        )
        await context.checkpoint(
            "result_verified",
            external_submission_possible=True,
        )
        return JobExecutionResult(
            result_verified=True,
            external_task_reference=task.reference,
            technical_details={
                "asset_id": stored_asset.id,
                "relative_path": stored_asset.relative_path,
                "mime_type": verified.mime_type,
                "size_bytes": verified.size_bytes,
                "sha256": verified.sha256,
                "width": verified.width,
                "height": verified.height,
            },
        )

    @staticmethod
    def _safe_filename(filename: str) -> str:
        candidate = Path(filename).name
        stem = Path(candidate).stem
        sanitized = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-_")
        if not sanitized:
            raise ValueError("Flow returned an unusable image filename.")
        return sanitized
