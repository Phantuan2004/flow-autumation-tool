from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from flow_automation.core.models import (
    AssetKind,
    FileVerificationStatus,
    JobEventType,
    JobStatus,
    JobType,
    ProjectStatus,
    ReferenceKind,
    SceneDraft,
    SceneStatus,
)
from flow_automation.core.state_machine import InvalidStateTransition
from flow_automation.persistence.database import (
    Database,
    DatabaseVersionError,
)
from flow_automation.persistence.repository import (
    FlowRepository,
    RepositoryNotFoundError,
)


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    return tmp_path / "isolated.sqlite3"


def new_repository(path: Path) -> tuple[Database, FlowRepository]:
    database = Database.from_path(path)
    database.initialize()
    return database, FlowRepository(database)


def test_projects_scenes_and_references_round_trip_with_utc(
    database_path: Path,
) -> None:
    database, repository = new_repository(database_path)
    project = repository.create_project(
        "  Sample project  ",
        "projects/sample",
        description="test data",
        default_config={"image_model": "Nano Banana"},
    )
    reference = repository.create_reference(
        project.id,
        "references/character.png",
        ReferenceKind.CHARACTER,
        metadata={"role": "lead"},
    )
    scenes = repository.create_scenes(
        project.id,
        [
            SceneDraft(1, "Image prompt one", "Video prompt one", (reference.id,)),
            SceneDraft(2, "Image prompt two"),
        ],
    )

    assert project.name == "Sample project"
    assert project.status is ProjectStatus.ACTIVE
    assert project.created_at.tzinfo is not None
    assert scenes[0].reference_ids == (reference.id,)
    assert scenes[0].status is SceneStatus.READY
    assert [scene.scene_number for scene in repository.list_scenes(project.id)] == [
        1,
        2,
    ]
    assert repository.get_reference(reference.id).metadata == {"role": "lead"}
    updated_project = repository.update_project(
        project.id,
        name="Updated project",
        description="updated description",
        default_config={"image_model": "Observed model"},
    )
    assert updated_project.name == "Updated project"
    assert updated_project.description == "updated description"
    assert updated_project.default_config == {"image_model": "Observed model"}
    assert repository.find_project(project.id) == updated_project
    database.close()


def test_scene_import_is_atomic_and_rejects_invalid_references(
    database_path: Path,
) -> None:
    database, repository = new_repository(database_path)
    project = repository.create_project("Atomic", "projects/atomic")
    existing_scene = repository.create_scenes(
        project.id,
        [SceneDraft(1, "existing scene")],
    )[0]

    with pytest.raises(IntegrityError):
        repository.create_scenes(
            project.id,
            [SceneDraft(1, "duplicate number"), SceneDraft(2, "second scene")],
        )
    assert repository.list_scenes(project.id) == [existing_scene]

    with pytest.raises(ValueError, match="belonging to their project"):
        repository.create_scenes(
            project.id,
            [SceneDraft(2, "prompt", reference_ids=("not-a-reference",))],
        )
    assert repository.list_scenes(project.id) == [existing_scene]
    database.close()


def test_foreign_keys_are_enabled_and_enforced(database_path: Path) -> None:
    database, _repository = new_repository(database_path)

    with pytest.raises(IntegrityError):
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO scenes "
                    "(id, project_id, scene_number, image_prompt, video_prompt, "
                    "status, created_at, updated_at) "
                    "VALUES ('missing-project-scene', 'missing-project', 1, "
                    "'prompt', '', 'READY', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
    database.close()


def test_image_selection_is_unique_and_video_requires_approved_image(
    database_path: Path,
) -> None:
    database, repository = new_repository(database_path)
    project = repository.create_project("Media", "projects/media")
    (scene,) = repository.create_scenes(
        project.id,
        [SceneDraft(1, "still image", "camera orbit")],
    )
    image_job = repository.create_job(project.id, scene.id, JobType.IMAGE)
    repository.transition_job(image_job.id, JobStatus.RUNNING, message="Started.")
    repository.transition_job(
        image_job.id,
        JobStatus.SUCCEEDED,
        message="Image result verified.",
        result_verified=True,
    )
    images = [
        repository.create_asset(
            project.id,
            f"scenes/1/image-{index}.jpg",
            AssetKind.IMAGE,
            scene_id=scene.id,
            job_id=image_job.id,
            mime_type="image/jpeg",
            size_bytes=1000 + index,
            verification_status=FileVerificationStatus.VERIFIED,
        )
        for index in (1, 2)
    ]

    with pytest.raises(InvalidStateTransition, match="approved image"):
        repository.create_job(project.id, scene.id, JobType.VIDEO)

    selected_scene = repository.select_image(scene.id, images[0].id)
    assert selected_scene.status is SceneStatus.IMAGE_APPROVED
    selected_scene = repository.select_image(scene.id, images[1].id)
    assert selected_scene.selected_image_asset_id == images[1].id
    assert len(repository.list_assets(project_id=project.id, kind=AssetKind.IMAGE)) == 2

    video_job = repository.create_job(project.id, scene.id, JobType.VIDEO)
    assert video_job.status is JobStatus.PENDING
    assert repository.get_scene(scene.id).status is SceneStatus.VIDEO_QUEUED
    database.close()


def test_jobs_events_failures_and_retry_queries_are_transactional(
    database_path: Path,
) -> None:
    database, repository = new_repository(database_path)
    project = repository.create_project("Jobs", "projects/jobs")
    (scene,) = repository.create_scenes(project.id, [SceneDraft(1, "prompt")])
    job = repository.create_job(
        project.id,
        scene.id,
        JobType.IMAGE,
        retry_limit=1,
        config={"aspect_ratio": "16:9"},
    )
    assert len(repository.list_job_events(job.id)) == 1
    running = repository.transition_job(
        job.id,
        JobStatus.RUNNING,
        message="Started.",
        external_task_reference="flow-task-1",
    )
    failed = repository.transition_job(
        job.id,
        JobStatus.FAILED,
        message="The UI reported a generation failure.",
        error="Generation failed.",
        technical_details={"stage": "result_wait"},
    )
    assert failed.retry_count == 0
    assert failed.last_error == "Generation failed."
    assert failed.started_at is not None
    assert failed.finished_at is not None
    assert repository.get_scene(scene.id).status is SceneStatus.FAILED

    retry = repository.transition_job(
        job.id,
        JobStatus.RETRY_WAIT,
        message="Retry scheduled after a confirmed failure.",
        event_type=JobEventType.STATUS_CHANGED,
    )
    assert retry.retry_count == 1
    assert [item.id for item in repository.list_resumable_jobs(project.id)] == [
        job.id
    ]
    repository.transition_job(job.id, JobStatus.PENDING, message="Retry is ready.")
    assert repository.list_resumable_jobs(project.id)[0].status is JobStatus.PENDING
    assert running.external_task_reference == "flow-task-1"
    repository.transition_job(
        job.id,
        JobStatus.RUNNING,
        message="Second attempt started.",
    )
    assert repository.get_scene(scene.id).status is SceneStatus.IMAGE_RUNNING

    event_count = len(repository.list_job_events(job.id))
    with pytest.raises(InvalidStateTransition, match="retry limit"):
        repository.transition_job(
            job.id,
            JobStatus.RETRY_WAIT,
            message="Retry limit reached.",
        )
    assert len(repository.list_job_events(job.id)) == event_count
    assert repository.get_job(job.id).status is JobStatus.RUNNING
    database.close()


def test_failure_without_error_rolls_back_and_video_cancel_restores_approval(
    database_path: Path,
) -> None:
    database, repository = new_repository(database_path)
    project = repository.create_project("Cancel video", "projects/cancel-video")
    (scene,) = repository.create_scenes(
        project.id,
        [SceneDraft(1, "still", "video")],
    )
    image_job = repository.create_job(project.id, scene.id, JobType.IMAGE)
    repository.transition_job(image_job.id, JobStatus.RUNNING, message="Started.")
    repository.transition_job(
        image_job.id,
        JobStatus.SUCCEEDED,
        message="Verified image result.",
        result_verified=True,
    )
    image = repository.create_asset(
        project.id,
        "scenes/1/image.jpg",
        AssetKind.IMAGE,
        scene_id=scene.id,
        job_id=image_job.id,
        verification_status=FileVerificationStatus.VERIFIED,
    )
    repository.select_image(scene.id, image.id)
    video_job = repository.create_job(project.id, scene.id, JobType.VIDEO)
    repository.transition_job(video_job.id, JobStatus.RUNNING, message="Started.")

    with pytest.raises(ValueError, match="non-empty safe error"):
        repository.transition_job(
            video_job.id,
            JobStatus.FAILED,
            message="Failure was not described.",
        )
    assert repository.get_job(video_job.id).status is JobStatus.RUNNING
    assert repository.get_scene(scene.id).status is SceneStatus.VIDEO_RUNNING

    repository.transition_job(
        video_job.id,
        JobStatus.CANCELLED,
        message="Cancelled by the user.",
    )
    assert repository.get_scene(scene.id).status is SceneStatus.IMAGE_APPROVED
    assert repository.get_scene(scene.id).selected_image_asset_id == image.id
    database.close()


def test_ambiguous_jobs_require_reconciliation_and_secrets_are_rejected(
    database_path: Path,
) -> None:
    database, repository = new_repository(database_path)
    project = repository.create_project("Recovery", "projects/recovery")
    (scene,) = repository.create_scenes(project.id, [SceneDraft(1, "prompt")])
    job = repository.create_job(project.id, scene.id, JobType.IMAGE)
    repository.transition_job(job.id, JobStatus.RUNNING, message="Started.")
    interrupted = repository.transition_job(
        job.id,
        JobStatus.INTERRUPTED,
        message="Application stopped while job was running.",
    )
    assert interrupted.id in {
        row.id for row in repository.list_reconciliation_jobs(project.id)
    }
    with pytest.raises(InvalidStateTransition, match="reconciled"):
        repository.transition_job(
            job.id,
            JobStatus.PENDING,
            message="Unsafe retry.",
        )
    with pytest.raises(ValueError, match="sensitive"):
        repository.transition_job(
            job.id,
            JobStatus.RECONCILIATION_REQUIRED,
            message="Reconciliation required.",
            technical_details={"access_token": "must not persist"},
        )
    with pytest.raises(ValueError, match="sensitive"):
        repository.transition_job(
            job.id,
            JobStatus.RECONCILIATION_REQUIRED,
            message="Reconciliation required.",
            reconciliation_details={"authentication_state": "must not persist"},
        )
    assert repository.get_job(job.id).status is JobStatus.INTERRUPTED
    database.close()


def test_database_reopens_persistent_data_and_rejects_future_schema(
    database_path: Path,
) -> None:
    first_database, first_repository = new_repository(database_path)
    project = first_repository.create_project("Persistent", "projects/persistent")
    (scene,) = first_repository.create_scenes(
        project.id,
        [SceneDraft(7, "persistent prompt")],
    )
    first_database.close()

    reopened_database, reopened_repository = new_repository(database_path)
    assert reopened_repository.get_project(project.id) == project
    assert reopened_repository.get_scene(scene.id).image_prompt == "persistent prompt"
    assert reopened_repository.project_progress(project.id)["scenes"][
        SceneStatus.READY.value
    ] == 1
    reopened_database.close()

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA user_version=99")
    future_database = Database.from_path(database_path)
    with pytest.raises(DatabaseVersionError, match="newer than supported"):
        future_database.initialize()
    future_database.close()


def test_project_json_fields_reject_authentication_material(
    database_path: Path,
) -> None:
    database, repository = new_repository(database_path)
    with pytest.raises(ValueError, match="sensitive"):
        repository.create_project(
            "Unsafe",
            "projects/unsafe",
            default_config={"nested": {"cookie_value": "never store"}},
        )
    with pytest.raises(RepositoryNotFoundError):
        repository.get_project("missing")
    assert repository.list_projects() == []
    database.close()


def test_progress_counts_multiple_jobs_and_scene_states(database_path: Path) -> None:
    database, repository = new_repository(database_path)
    project = repository.create_project("Progress", "projects/progress")
    scenes = repository.create_scenes(
        project.id,
        [SceneDraft(1, "one"), SceneDraft(2, "two")],
    )
    first_job = repository.create_job(project.id, scenes[0].id, JobType.IMAGE)
    repository.transition_job(
        first_job.id,
        JobStatus.CANCELLED,
        message="Cancelled before start.",
    )
    second_job = repository.create_job(project.id, scenes[0].id, JobType.IMAGE)
    progress = repository.project_progress(project.id)

    assert progress["scenes"][SceneStatus.READY.value] == 1
    assert progress["scenes"][SceneStatus.IMAGE_QUEUED.value] == 1
    assert progress["jobs"][JobStatus.CANCELLED.value] == 1
    assert progress["jobs"][JobStatus.PENDING.value] == 1
    assert len(repository.list_jobs(scene_id=scenes[0].id)) == 2
    assert repository.get_job(second_job.id).status is JobStatus.PENDING
    database.close()
