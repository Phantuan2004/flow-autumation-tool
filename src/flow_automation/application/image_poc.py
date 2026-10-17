from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from flow_automation.automation.errors import (
    AuthenticationRequiredError,
    FlowStateUnknownError,
    FlowSubmissionNotSentError,
    UnsupportedOperationError,
)
from flow_automation.automation.flow_adapter import (
    FlowAdapter,
    FlowAsset,
    FlowTask,
    ImageRequest,
)
from flow_automation.core.models import (
    Asset,
    AssetKind,
    FileVerificationStatus,
    JobStatus,
    JobType,
    SceneDraft,
    new_id,
)
from flow_automation.persistence.repository import FlowRepository
from flow_automation.storage.media_verifier import MediaVerificationError, MediaVerifier

logger = logging.getLogger(__name__)


class ImageAdapter(Protocol):
    async def create_image(self, request: ImageRequest) -> FlowTask: ...

    async def wait_for_result(self, task: FlowTask, timeout: float) -> FlowTask: ...

    async def collect_images(self, task: FlowTask) -> list[FlowAsset]: ...

    async def download_asset(self, asset: FlowAsset, destination: Path) -> Path: ...


@dataclass(frozen=True, slots=True)
class ImagePocResult:
    project_id: str
    scene_id: str
    job_id: str
    asset: Asset
    path: Path
    width: int
    height: int


class FlowImagePocService:
    """Coordinates one image job from Flow submission through verified persistence."""

    def __init__(
        self,
        repository: FlowRepository,
        adapter: ImageAdapter,
        asset_root: Path,
        *,
        media_verifier: MediaVerifier | None = None,
    ) -> None:
        self._repository = repository
        self._adapter = adapter
        self._asset_root = asset_root
        self._media_verifier = media_verifier or MediaVerifier()

    async def run_once(self, prompt: str, *, timeout: float = 300) -> ImagePocResult:
        if not prompt.strip():
            raise ValueError("Image prompt must not be empty.")
        if timeout <= 0:
            raise ValueError("Result wait timeout must be greater than zero.")

        project_id = new_id()
        project_root = self._asset_root / project_id
        project = self._repository.create_project(
            "Flow image proof of concept",
            str(project_root),
            description="Single authorized image-generation proof of concept.",
            project_id=project_id,
        )
        scene = self._repository.create_scenes(
            project.id,
            [SceneDraft(scene_number=1, image_prompt=prompt)],
        )[0]
        job = self._repository.create_job(
            project.id,
            scene.id,
            JobType.IMAGE,
            retry_limit=0,
            config={
                "model": "Nano Banana 2.1",
                "variant_count": 1,
            },
        )
        self._repository.transition_job(
            job.id,
            JobStatus.RUNNING,
            message="Submitting the single approved image request.",
        )

        try:
            task = await self._adapter.create_image(ImageRequest(prompt))
        except (
            AuthenticationRequiredError,
            FlowSubmissionNotSentError,
            UnsupportedOperationError,
            ValueError,
        ) as error:
            self._mark_failed(job.id, error)
            raise
        except FlowStateUnknownError:
            self._mark_reconciliation(job.id, "image_submission")
            raise

        try:
            await self._adapter.wait_for_result(task, timeout=timeout)
            assets = await self._adapter.collect_images(task)
            if len(assets) != 1:
                raise FlowStateUnknownError(
                    "The x1 Flow request did not produce exactly one collectable image."
                )

            output_directory = project_root / "scenes" / "001"
            output_directory.mkdir(parents=True, exist_ok=True)
            destination = output_directory / f"{assets[0].filename}.jpg"
            downloaded_path = await self._adapter.download_asset(assets[0], destination)
            verified = self._media_verifier.verify_image(downloaded_path)
        except (
            FlowStateUnknownError,
            UnsupportedOperationError,
            OSError,
            MediaVerificationError,
        ):
            self._mark_reconciliation(
                job.id,
                "result_download_or_verification",
                task_reference=task.reference,
            )
            raise

        relative_path = downloaded_path.relative_to(project_root).as_posix()
        stored_asset = self._repository.create_asset(
            project.id,
            relative_path,
            AssetKind.IMAGE,
            scene_id=scene.id,
            job_id=job.id,
            original_filename=downloaded_path.name,
            mime_type=verified.mime_type,
            size_bytes=verified.size_bytes,
            sha256=verified.sha256,
            verification_status=FileVerificationStatus.VERIFIED,
        )
        self._repository.transition_job(
            job.id,
            JobStatus.SUCCEEDED,
            message="Flow image downloaded and verified.",
            technical_details={
                "adapter_task_reference": task.reference,
                "image_format": verified.format,
                "width": verified.width,
                "height": verified.height,
                "sha256": verified.sha256,
            },
            result_verified=True,
        )
        logger.info(
            "Single Flow image proof of concept verified: job_id=%s asset_id=%s.",
            job.id,
            stored_asset.id,
        )
        return ImagePocResult(
            project_id=project.id,
            scene_id=scene.id,
            job_id=job.id,
            asset=stored_asset,
            path=downloaded_path,
            width=verified.width,
            height=verified.height,
        )

    def _mark_failed(self, job_id: str, error: Exception) -> None:
        self._repository.transition_job(
            job_id,
            JobStatus.FAILED,
            message="Image request was rejected before submission.",
            error=type(error).__name__,
        )

    def _mark_reconciliation(
        self,
        job_id: str,
        stage: str,
        *,
        task_reference: str | None = None,
    ) -> None:
        details = {"stage": stage}
        if task_reference is not None:
            details["adapter_task_reference"] = task_reference
        self._repository.transition_job(
            job_id,
            JobStatus.RECONCILIATION_REQUIRED,
            message="Flow may have accepted the request; inspect the project before retrying.",
            reconciliation_details=details,
        )
