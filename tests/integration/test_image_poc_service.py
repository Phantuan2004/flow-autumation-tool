from __future__ import annotations

from pathlib import Path

from PIL import Image

from flow_automation.automation.flow_adapter import FlowAsset, FlowTask
from flow_automation.application.image_poc import FlowImagePocService
from flow_automation.bootstrap import create_flow_repository
from flow_automation.core.models import (
    AssetKind,
    FileVerificationStatus,
    JobStatus,
    JobType,
    SceneStatus,
)
from flow_automation.persistence.database import Database
from flow_automation.persistence.repository import FlowRepository


class FakeImageAdapter:
    def __init__(self, *, invalid_download: bool = False) -> None:
        self.invalid_download = invalid_download

    async def create_image(self, request: object) -> FlowTask:
        del request
        return FlowTask("test-adapter-task")

    async def wait_for_result(self, task: FlowTask, timeout: float) -> FlowTask:
        assert task.reference == "test-adapter-task"
        assert timeout > 0
        return task

    async def collect_images(self, task: FlowTask) -> list[FlowAsset]:
        assert task.reference == "test-adapter-task"
        return [FlowAsset("test-adapter-task:image:0", "flow-image-test")]

    async def download_asset(self, asset: FlowAsset, destination: Path) -> Path:
        assert asset.reference == "test-adapter-task:image:0"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if self.invalid_download:
            destination.write_bytes(b"not an image")
        else:
            Image.new("RGB", (1376, 768), color="white").save(
                destination,
                format="JPEG",
            )
        return destination


def create_repository(path: Path) -> tuple[Database, FlowRepository]:
    return create_flow_repository(path)


def test_image_poc_persists_success_only_after_media_verification(
    tmp_path: Path,
) -> None:
    database, repository = create_repository(tmp_path / "poc.sqlite3")
    service = FlowImagePocService(
        repository,
        FakeImageAdapter(),
        tmp_path / "assets",
    )

    import asyncio

    result = asyncio.run(service.run_once("A test landscape"))

    job = repository.get_job(result.job_id)
    project = repository.get_project(result.project_id)
    scene = repository.get_scene(result.scene_id)
    assets = repository.list_assets(project_id=result.project_id)
    assert job.status is JobStatus.SUCCEEDED
    assert scene.status is SceneStatus.NEEDS_REVIEW
    assert job.job_type is JobType.IMAGE
    assert result.asset.kind is AssetKind.IMAGE
    assert result.asset.verification_status is FileVerificationStatus.VERIFIED
    assert (result.width, result.height) == (1376, 768)
    assert Path(project.root_path, result.asset.relative_path) == result.path
    assert result.path.is_file()
    assert len(assets) == 1
    database.close()


def test_image_poc_does_not_mark_unverified_output_successful(
    tmp_path: Path,
) -> None:
    import asyncio

    import pytest

    from flow_automation.storage.media_verifier import MediaVerificationError

    database, repository = create_repository(tmp_path / "poc-invalid.sqlite3")
    service = FlowImagePocService(
        repository,
        FakeImageAdapter(invalid_download=True),
        tmp_path / "assets",
    )

    with pytest.raises(MediaVerificationError):
        asyncio.run(service.run_once("A test landscape"))

    jobs = repository.list_jobs(job_type=JobType.IMAGE)
    assert len(jobs) == 1
    assert jobs[0].status is JobStatus.RECONCILIATION_REQUIRED
    assert repository.list_assets(project_id=jobs[0].project_id) == []
    database.close()
