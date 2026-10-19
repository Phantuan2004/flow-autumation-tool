from __future__ import annotations

import asyncio
from pathlib import Path

from PIL import Image
import pytest

from flow_automation.application.image_gallery import ImageGalleryService
from flow_automation.automation.flow_adapter import FlowAsset, FlowTask, ImageRequest
from flow_automation.core.models import (
    AssetKind,
    FileVerificationStatus,
    GenerationJob,
    JobStatus,
    JobType,
    SceneDraft,
)
from flow_automation.jobs.image_processor import FlowImageJobProcessor
from flow_automation.jobs.job_runner import JobExecutionContext, JobExecutionResult
from flow_automation.jobs.scheduler import BatchScheduler
from flow_automation.persistence.database import Database
from flow_automation.persistence.repository import FlowRepository
from flow_automation.storage.media_verifier import (
    MediaVerificationError,
    MediaVerifier,
)


class FakeImageAdapter:
    def __init__(self, *, invalid_output: bool = False) -> None:
        self.invalid_output = invalid_output
        self.request: ImageRequest | None = None
        self.submission_count = 0

    async def create_image(self, request: ImageRequest) -> FlowTask:
        self.request = request
        self.submission_count += 1
        return FlowTask("fake-flow-task")

    async def wait_for_result(self, task: FlowTask, timeout: float) -> FlowTask:
        assert task.reference == "fake-flow-task"
        assert timeout > 0
        return task

    async def collect_images(self, task: FlowTask) -> list[FlowAsset]:
        assert task.reference == "fake-flow-task"
        return [FlowAsset("fake-flow-image", "generated-image")]

    async def download_asset(self, asset: FlowAsset, destination: Path) -> Path:
        assert asset.reference == "fake-flow-image"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if self.invalid_output:
            destination.write_bytes(b"not an image")
        else:
            Image.new("RGB", (1376, 768), color="white").save(
                destination,
                format="JPEG",
            )
        return destination


def make_repository(path: Path) -> tuple[Database, FlowRepository]:
    database = Database.from_path(path)
    database.initialize()
    return database, FlowRepository(database)


def test_image_job_verifies_and_persists_result_before_gallery_approval(
    tmp_path: Path,
) -> None:
    async def run() -> tuple[str, str, str]:
        database, repository = make_repository(tmp_path / "image-batch.sqlite3")
        project_root = tmp_path / "project-assets"
        project_root.mkdir()
        project = repository.create_project("Batch", str(project_root))
        (scene,) = repository.create_scenes(
            project.id,
            [SceneDraft(1, "  A wide landscape  ")],
        )
        job = repository.create_job(project.id, scene.id, JobType.IMAGE)
        adapter = FakeImageAdapter()
        processor = FlowImageJobProcessor(
            repository,
            adapter,
            result_timeout=10,
        )
        scheduler = BatchScheduler(repository, processor, browser_bound=True)

        results = await scheduler.start(project.id)

        completed_job = repository.get_job(job.id)
        assets = repository.list_assets(project_id=project.id, scene_id=scene.id)
        assert adapter.request == ImageRequest("A wide landscape")
        assert adapter.submission_count == 1
        assert completed_job.status is JobStatus.SUCCEEDED
        assert completed_job.external_task_reference == "fake-flow-task"
        assert len(assets) == 1
        assert assets[0].verification_status is FileVerificationStatus.VERIFIED
        assert assets[0].mime_type == "image/jpeg"
        image_path = project_root / assets[0].relative_path
        verified = MediaVerifier().verify_image(image_path)
        assert (verified.width, verified.height) == (1376, 768)
        assert results[0].status is JobStatus.SUCCEEDED

        second_job = repository.create_job(project.id, scene.id, JobType.IMAGE)
        second_results = await scheduler.start(project.id)
        second_assets = repository.list_assets(
            project_id=project.id,
            scene_id=scene.id,
        )
        assert repository.get_job(second_job.id).status is JobStatus.SUCCEEDED
        assert second_results[0].status is JobStatus.SUCCEEDED
        assert len(second_assets) == 2
        assert second_assets[0].relative_path != second_assets[1].relative_path

        gallery = ImageGalleryService(repository)
        items = gallery.list_project_images(project.id)
        assert len(items) == 2
        approved_scene = gallery.approve_image(scene.id, second_assets[1].id)
        assert approved_scene.selected_image_asset_id == second_assets[1].id
        assert [
            item.asset.id for item in gallery.list_project_images(project.id)
            if item.selected
        ] == [second_assets[1].id]
        database.close()
        return completed_job.id, assets[0].id, second_assets[1].id

    job_id, generated_asset_id, selected_asset_id = asyncio.run(run())
    assert job_id
    assert generated_asset_id != selected_asset_id


def test_download_verification_failure_requires_reconciliation(
    tmp_path: Path,
) -> None:
    async def run() -> tuple[JobStatus, int, int]:
        database, repository = make_repository(tmp_path / "image-invalid.sqlite3")
        project_root = tmp_path / "invalid-project"
        project_root.mkdir()
        project = repository.create_project("Invalid image", str(project_root))
        (scene,) = repository.create_scenes(
            project.id,
            [SceneDraft(1, "A prompt")],
        )
        job = repository.create_job(project.id, scene.id, JobType.IMAGE)
        adapter = FakeImageAdapter(invalid_output=True)
        processor = FlowImageJobProcessor(
            repository,
            adapter,
            result_timeout=10,
        )
        scheduler = BatchScheduler(repository, processor, browser_bound=True)

        results = await scheduler.start(project.id)

        current_job = repository.get_job(job.id)
        assets = repository.list_assets(project_id=project.id)
        event_count = len(repository.list_job_events(job.id))
        database.close()
        assert len(results) == 1
        return current_job.status, len(assets), event_count

    status, asset_count, event_count = asyncio.run(run())
    assert status is JobStatus.RECONCILIATION_REQUIRED
    assert asset_count == 0
    assert event_count >= 5


def test_gallery_rejects_tampered_file_and_hides_unverified_asset(
    tmp_path: Path,
) -> None:
    database, repository = make_repository(tmp_path / "gallery-tamper.sqlite3")
    project_root = tmp_path / "gallery-project"
    project_root.mkdir()
    project = repository.create_project("Gallery", str(project_root))
    (scene,) = repository.create_scenes(project.id, [SceneDraft(1, "A prompt")])
    job = repository.create_job(project.id, scene.id, JobType.IMAGE)
    repository.transition_job(job.id, JobStatus.RUNNING, message="Started.")
    repository.transition_job(
        job.id,
        JobStatus.SUCCEEDED,
        message="Verified image job.",
        result_verified=True,
    )

    image_path = project_root / "scenes" / "001" / "image.jpg"
    image_path.parent.mkdir(parents=True)
    Image.new("RGB", (320, 240), color="white").save(image_path, format="JPEG")
    verified = MediaVerifier().verify_image(image_path)
    asset = repository.create_asset(
        project.id,
        image_path.relative_to(project_root).as_posix(),
        AssetKind.IMAGE,
        scene_id=scene.id,
        job_id=job.id,
        mime_type=verified.mime_type,
        size_bytes=verified.size_bytes,
        sha256=verified.sha256,
        verification_status=FileVerificationStatus.VERIFIED,
    )
    unchecked = repository.create_asset(
        project.id,
        "scenes/001/unverified.jpg",
        AssetKind.IMAGE,
        scene_id=scene.id,
        job_id=job.id,
    )
    image_path.write_bytes(b"changed after verification")

    gallery = ImageGalleryService(repository)
    assert [item.asset.id for item in gallery.list_project_images(project.id)] == [
        asset.id
    ]
    with pytest.raises(MediaVerificationError):
        gallery.approve_image(scene.id, asset.id)

    assert (
        repository.get_asset(asset.id).verification_status
        is FileVerificationStatus.INVALID
    )
    assert (
        repository.get_asset(unchecked.id).verification_status
        is FileVerificationStatus.UNCHECKED
    )
    database.close()


def test_image_batch_does_not_dispatch_pending_video_jobs(
    tmp_path: Path,
) -> None:
    async def run() -> tuple[JobStatus, JobStatus, list[str]]:
        database, repository = make_repository(tmp_path / "image-filter.sqlite3")
        project = repository.create_project("Typed jobs", str(tmp_path / "typed-jobs"))
        scenes = repository.create_scenes(
            project.id,
            [
                SceneDraft(1, "Image prompt"),
                SceneDraft(2, "Video source", "Camera motion"),
            ],
        )
        image_job = repository.create_job(project.id, scenes[0].id, JobType.IMAGE)
        source_image_job = repository.create_job(
            project.id,
            scenes[1].id,
            JobType.IMAGE,
        )
        repository.transition_job(
            source_image_job.id,
            JobStatus.RUNNING,
            message="Started.",
        )
        repository.transition_job(
            source_image_job.id,
            JobStatus.SUCCEEDED,
            message="Verified.",
            result_verified=True,
        )
        source_asset = repository.create_asset(
            project.id,
            "scenes/002/source.jpg",
            AssetKind.IMAGE,
            scene_id=scenes[1].id,
            job_id=source_image_job.id,
            mime_type="image/jpeg",
            size_bytes=1,
            sha256="0" * 64,
            verification_status=FileVerificationStatus.VERIFIED,
        )
        repository.select_image(scenes[1].id, source_asset.id)
        video_job = repository.create_job(project.id, scenes[1].id, JobType.VIDEO)

        class RecordingProcessor:
            def __init__(self) -> None:
                self.calls: list[str] = []

            async def execute(
                self,
                job: GenerationJob,
                context: JobExecutionContext,
            ) -> JobExecutionResult:
                del context
                self.calls.append(job.id)
                return JobExecutionResult(result_verified=True)

        processor = RecordingProcessor()
        scheduler = BatchScheduler(repository, processor, browser_bound=False)
        await scheduler.start(project.id, job_type=JobType.IMAGE)
        result = (
            repository.get_job(image_job.id).status,
            repository.get_job(video_job.id).status,
            processor.calls,
        )
        database.close()
        return result

    image_status, video_status, processed_ids = asyncio.run(run())
    assert image_status is JobStatus.SUCCEEDED
    assert video_status is JobStatus.PENDING
    assert len(processed_ids) == 1
