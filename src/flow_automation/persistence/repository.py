from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import func, select
from flow_automation.core.models import (
    Asset,
    AssetKind,
    FileVerificationStatus,
    GenerationJob,
    JobEvent,
    JobEventType,
    JobStatus,
    JobType,
    Project,
    ProjectStatus,
    Reference,
    ReferenceKind,
    Scene,
    SceneDraft,
    SceneStatus,
    new_id,
    utc_now,
)
from flow_automation.core.state_machine import (
    InvalidStateTransition,
    transition_job,
    transition_scene,
)
from flow_automation.persistence.database import Database
from flow_automation.persistence.models import (
    AssetRow,
    GenerationJobRow,
    JobEventRow,
    ProjectRow,
    ReferenceRow,
    SceneImageSelectionRow,
    SceneRow,
)


class RepositoryNotFoundError(LookupError):
    """Raised when the requested project-domain row does not exist."""


_SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "cookie",
    "token",
    "secret",
    "credential",
    "authentication",
    "authorization",
    "auth_header",
    "storage_state",
    "session",
)
_SENSITIVE_TEXT = re.compile(
    r"(?:password|passwd|cookie|token|secret|authorization)\s*[:=]|"
    r"\bbearer\s+[A-Za-z0-9._~-]+",
    re.IGNORECASE,
)


def _safe_json_object(value: Mapping[str, Any] | None, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping.")

    def validate(item: Any, path: str) -> Any:
        if isinstance(item, Mapping):
            validated: dict[str, Any] = {}
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError(f"{field_name} keys must be strings.")
                normalized_key = key.casefold().replace("-", "_")
                if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
                    raise ValueError(
                        f"{field_name} must not contain sensitive field {path}{key!r}."
                    )
                validated[key] = validate(child, f"{path}{key}.")
            return validated
        if isinstance(item, (list, tuple)):
            return [validate(child, f"{path}[]") for child in item]
        return item

    validated = validate(value, "")
    try:
        encoded = json.dumps(validated, allow_nan=False)
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must contain only JSON-safe values.") from error
    if not isinstance(decoded, dict):
        raise ValueError(f"{field_name} must be a JSON object.")
    return decoded


def _relative_path(value: str) -> str:
    if not value or "\\" in value:
        raise ValueError("Asset paths must be non-empty project-relative POSIX paths.")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Asset paths must not be absolute or escape the project folder.")
    return path.as_posix()


def _safe_text(value: str, *, field_name: str) -> str:
    if _SENSITIVE_TEXT.search(value):
        raise ValueError(f"{field_name} must not contain authentication material.")
    return value


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _project(row: ProjectRow) -> Project:
    return Project(
        id=row.id,
        name=row.name,
        description=row.description,
        root_path=row.root_path,
        status=row.status,
        default_config=dict(row.default_config),
        created_at=_utc(row.created_at),  # type: ignore[arg-type]
        updated_at=_utc(row.updated_at),  # type: ignore[arg-type]
    )


def _scene(row: SceneRow) -> Scene:
    return Scene(
        id=row.id,
        project_id=row.project_id,
        scene_number=row.scene_number,
        image_prompt=row.image_prompt,
        video_prompt=row.video_prompt,
        reference_ids=tuple(reference.id for reference in row.references),
        status=row.status,
        selected_image_asset_id=(
            row.image_selection.asset_id if row.image_selection is not None else None
        ),
        created_at=_utc(row.created_at),  # type: ignore[arg-type]
        updated_at=_utc(row.updated_at),  # type: ignore[arg-type]
    )


def _job(row: GenerationJobRow) -> GenerationJob:
    return GenerationJob(
        id=row.id,
        project_id=row.project_id,
        scene_id=row.scene_id,
        job_type=row.job_type,
        status=row.status,
        retry_count=row.retry_count,
        retry_limit=row.retry_limit,
        created_at=_utc(row.created_at),  # type: ignore[arg-type]
        started_at=_utc(row.started_at),
        finished_at=_utc(row.finished_at),
        last_error=row.last_error,
        external_task_reference=row.external_task_reference,
        config=dict(row.config_json),
        reconciliation_details=dict(row.reconciliation_json),
    )


def _asset(row: AssetRow) -> Asset:
    return Asset(
        id=row.id,
        project_id=row.project_id,
        scene_id=row.scene_id,
        job_id=row.job_id,
        kind=row.kind,
        relative_path=row.relative_path,
        original_filename=row.original_filename,
        mime_type=row.mime_type,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        verification_status=row.verification_status,
        created_at=_utc(row.created_at),  # type: ignore[arg-type]
    )


def _reference(row: ReferenceRow) -> Reference:
    return Reference(
        id=row.id,
        project_id=row.project_id,
        relative_path=row.relative_path,
        kind=row.kind,
        metadata=dict(row.metadata_json),
        created_at=_utc(row.created_at),  # type: ignore[arg-type]
    )


def _event(row: JobEventRow) -> JobEvent:
    return JobEvent(
        id=row.id,
        job_id=row.job_id,
        event_type=row.event_type,
        occurred_at=_utc(row.occurred_at),  # type: ignore[arg-type]
        message=row.message,
        status_before=row.status_before,
        status_after=row.status_after,
        technical_details=dict(row.technical_details),
    )


class FlowRepository:
    """Transaction-scoped CRUD and workflow operations for domain data."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def create_project(
        self,
        name: str,
        root_path: str,
        *,
        description: str = "",
        default_config: Mapping[str, Any] | None = None,
        project_id: str | None = None,
    ) -> Project:
        clean_name = name.strip()
        clean_root = str(root_path).strip()
        if not clean_name:
            raise ValueError("Project name must not be empty.")
        if not clean_root:
            raise ValueError("Project root path must not be empty.")
        config = _safe_json_object(default_config, field_name="Project defaults")
        now = utc_now()
        with self._database.session_scope() as session:
            row = ProjectRow(
                id=project_id or new_id(),
                name=clean_name,
                description=description,
                root_path=clean_root,
                status=ProjectStatus.ACTIVE,
                default_config=config,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            result = _project(row)
        return result

    def get_project(self, project_id: str) -> Project:
        with self._database.session_scope() as session:
            row = self._require(session.get(ProjectRow, project_id), "Project", project_id)
            return _project(row)

    def find_project(self, project_id: str) -> Project | None:
        with self._database.session_scope() as session:
            row = session.get(ProjectRow, project_id)
            return _project(row) if row is not None else None

    def list_projects(self, *, include_archived: bool = True) -> list[Project]:
        with self._database.session_scope() as session:
            statement = select(ProjectRow).order_by(ProjectRow.created_at, ProjectRow.id)
            if not include_archived:
                statement = statement.where(
                    ProjectRow.status == ProjectStatus.ACTIVE
                )
            return [_project(row) for row in session.scalars(statement)]

    def update_project(
        self,
        project_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        root_path: str | None = None,
        status: ProjectStatus | None = None,
        default_config: Mapping[str, Any] | None = None,
    ) -> Project:
        with self._database.session_scope() as session:
            row = self._require(session.get(ProjectRow, project_id), "Project", project_id)
            if name is not None:
                if not name.strip():
                    raise ValueError("Project name must not be empty.")
                row.name = name.strip()
            if description is not None:
                row.description = description
            if root_path is not None:
                if not root_path.strip():
                    raise ValueError("Project root path must not be empty.")
                row.root_path = root_path.strip()
            if status is not None:
                row.status = status
            if default_config is not None:
                row.default_config = _safe_json_object(
                    default_config,
                    field_name="Project defaults",
                )
            row.updated_at = utc_now()
            session.flush()
            return _project(row)

    def create_reference(
        self,
        project_id: str,
        relative_path: str,
        kind: ReferenceKind,
        *,
        metadata: Mapping[str, Any] | None = None,
        reference_id: str | None = None,
    ) -> Reference:
        path = _relative_path(relative_path)
        safe_metadata = _safe_json_object(metadata, field_name="Reference metadata")
        with self._database.session_scope() as session:
            self._require(session.get(ProjectRow, project_id), "Project", project_id)
            row = ReferenceRow(
                id=reference_id or new_id(),
                project_id=project_id,
                relative_path=path,
                kind=kind,
                metadata_json=safe_metadata,
            )
            session.add(row)
            session.flush()
            return _reference(row)

    def get_reference(self, reference_id: str) -> Reference:
        with self._database.session_scope() as session:
            row = self._require(
                session.get(ReferenceRow, reference_id),
                "Reference",
                reference_id,
            )
            return _reference(row)

    def list_references(self, project_id: str) -> list[Reference]:
        with self._database.session_scope() as session:
            statement = (
                select(ReferenceRow)
                .where(ReferenceRow.project_id == project_id)
                .order_by(ReferenceRow.created_at, ReferenceRow.id)
            )
            return [_reference(row) for row in session.scalars(statement)]

    def create_scenes(
        self,
        project_id: str,
        scenes: Iterable[SceneDraft],
    ) -> list[Scene]:
        drafts = tuple(scenes)
        if not drafts:
            return []
        numbers = [draft.scene_number for draft in drafts]
        if any(number <= 0 for number in numbers):
            raise ValueError("Scene numbers must be positive.")
        if len(numbers) != len(set(numbers)):
            raise ValueError("Scene numbers must be unique within an import.")
        for draft in drafts:
            if not draft.image_prompt.strip():
                raise ValueError(
                    f"Scene {draft.scene_number} must have an image prompt."
                )
            if len(draft.reference_ids) != len(set(draft.reference_ids)):
                raise ValueError(
                    f"Scene {draft.scene_number} contains duplicate reference IDs."
                )

        with self._database.session_scope() as session:
            self._require(session.get(ProjectRow, project_id), "Project", project_id)
            all_reference_ids = {
                reference_id
                for draft in drafts
                for reference_id in draft.reference_ids
            }
            references = {
                row.id: row
                for row in session.scalars(
                    select(ReferenceRow).where(
                        ReferenceRow.id.in_(all_reference_ids)
                    )
                )
            } if all_reference_ids else {}
            for reference_id in all_reference_ids:
                reference = references.get(reference_id)
                if reference is None or reference.project_id != project_id:
                    raise ValueError(
                        "Scenes may only use references belonging to their project."
                    )
            rows = [
                SceneRow(
                    id=new_id(),
                    project_id=project_id,
                    scene_number=draft.scene_number,
                    image_prompt=draft.image_prompt,
                    video_prompt=draft.video_prompt,
                    status=SceneStatus.READY,
                    created_at=utc_now(),
                    updated_at=utc_now(),
                    references=[
                        references[reference_id]
                        for reference_id in draft.reference_ids
                    ],
                )
                for draft in drafts
            ]
            session.add_all(rows)
            session.flush()
            return [_scene(row) for row in rows]

    def get_scene(self, scene_id: str) -> Scene:
        with self._database.session_scope() as session:
            row = self._require(session.get(SceneRow, scene_id), "Scene", scene_id)
            return _scene(row)

    def list_scenes(self, project_id: str) -> list[Scene]:
        with self._database.session_scope() as session:
            statement = (
                select(SceneRow)
                .where(SceneRow.project_id == project_id)
                .order_by(SceneRow.scene_number)
            )
            return [_scene(row) for row in session.scalars(statement)]

    def update_scene_prompts(
        self,
        scene_id: str,
        *,
        image_prompt: str | None = None,
        video_prompt: str | None = None,
    ) -> Scene:
        with self._database.session_scope() as session:
            row = self._require(session.get(SceneRow, scene_id), "Scene", scene_id)
            if image_prompt is not None:
                if not image_prompt.strip():
                    raise ValueError("Scene image prompt must not be empty.")
                row.image_prompt = image_prompt
            if video_prompt is not None:
                row.video_prompt = video_prompt
            row.updated_at = utc_now()
            session.flush()
            return _scene(row)

    def transition_scene(
        self,
        scene_id: str,
        target: SceneStatus,
        *,
        result_verified: bool = False,
    ) -> Scene:
        with self._database.session_scope() as session:
            row = self._require(session.get(SceneRow, scene_id), "Scene", scene_id)
            selected = row.image_selection
            selected_image_verified = (
                selected is not None
                and selected.asset.kind is AssetKind.IMAGE
                and selected.asset.verification_status
                is FileVerificationStatus.VERIFIED
            )
            transition_scene(
                row.status,
                target,
                result_verified=result_verified,
                selected_image_verified=selected_image_verified,
            )
            row.status = target
            row.updated_at = utc_now()
            session.flush()
            return _scene(row)

    def create_job(
        self,
        project_id: str,
        scene_id: str,
        job_type: JobType,
        *,
        retry_limit: int = 3,
        config: Mapping[str, Any] | None = None,
        external_task_reference: str | None = None,
        job_id: str | None = None,
    ) -> GenerationJob:
        if retry_limit < 0:
            raise ValueError("Job retry limit must not be negative.")
        safe_config = _safe_json_object(config, field_name="Job configuration")
        now = utc_now()
        with self._database.session_scope() as session:
            self._require(session.get(ProjectRow, project_id), "Project", project_id)
            scene = self._require(session.get(SceneRow, scene_id), "Scene", scene_id)
            if scene.project_id != project_id:
                raise ValueError("The scene does not belong to the specified project.")
            active_statuses = (
                JobStatus.PENDING,
                JobStatus.RUNNING,
                JobStatus.RETRY_WAIT,
                JobStatus.INTERRUPTED,
                JobStatus.RECONCILIATION_REQUIRED,
            )
            active_exists = session.scalar(
                select(func.count())
                .select_from(GenerationJobRow)
                .where(
                    GenerationJobRow.scene_id == scene_id,
                    GenerationJobRow.job_type == job_type,
                    GenerationJobRow.status.in_(active_statuses),
                )
            )
            if active_exists:
                raise ValueError(
                    "An active job of this type already exists for the scene."
                )
            if job_type is JobType.IMAGE:
                target_scene_status = SceneStatus.IMAGE_QUEUED
                if scene.status not in {
                    SceneStatus.READY,
                    SceneStatus.NEEDS_REVIEW,
                    SceneStatus.IMAGE_APPROVED,
                    SceneStatus.FAILED,
                }:
                    raise InvalidStateTransition(
                        f"Cannot queue an image job for scene state {scene.status.value}."
                    )
                if scene.image_selection is not None:
                    session.delete(scene.image_selection)
                    session.flush()
            else:
                target_scene_status = SceneStatus.VIDEO_QUEUED
                if scene.status is not SceneStatus.IMAGE_APPROVED:
                    raise InvalidStateTransition(
                        "Video jobs require a scene with an approved image."
                    )
                if not self._has_verified_selection(scene):
                    raise InvalidStateTransition(
                        "Video jobs require a selected, verified image asset."
                    )
                if not scene.video_prompt.strip():
                    raise ValueError("A video prompt is required for a video job.")
            transition_scene(
                scene.status,
                target_scene_status,
                selected_image_verified=self._has_verified_selection(scene),
            )
            scene.status = target_scene_status
            scene.updated_at = now
            row = GenerationJobRow(
                id=job_id or new_id(),
                project_id=project_id,
                scene_id=scene_id,
                job_type=job_type,
                status=JobStatus.PENDING,
                retry_count=0,
                retry_limit=retry_limit,
                created_at=now,
                external_task_reference=external_task_reference,
                config_json=safe_config,
                reconciliation_json={},
            )
            session.add(row)
            session.flush()
            session.add(
                JobEventRow(
                    id=new_id(),
                    job_id=row.id,
                    event_type=JobEventType.CREATED,
                    status_before=None,
                    status_after=JobStatus.PENDING,
                    occurred_at=now,
                    message="Job queued.",
                    technical_details={},
                )
            )
            session.flush()
            return _job(row)

    def transition_job(
        self,
        job_id: str,
        target: JobStatus,
        *,
        message: str,
        event_type: JobEventType = JobEventType.STATUS_CHANGED,
        technical_details: Mapping[str, Any] | None = None,
        error: str | None = None,
        external_task_reference: str | None = None,
        reconciliation_details: Mapping[str, Any] | None = None,
        result_verified: bool = False,
        safe_to_retry: bool = False,
    ) -> GenerationJob:
        clean_message = _safe_text(message.strip(), field_name="Job event message")
        if not clean_message:
            raise ValueError("A safe event message is required.")
        if error is not None:
            error = _safe_text(error, field_name="Job error")
        if external_task_reference is not None:
            external_task_reference = _safe_text(
                external_task_reference,
                field_name="External task reference",
            )
        safe_details = _safe_json_object(
            technical_details,
            field_name="Job event details",
        )
        safe_reconciliation = (
            _safe_json_object(
                reconciliation_details,
                field_name="Job reconciliation details",
            )
            if reconciliation_details is not None
            else None
        )
        with self._database.session_scope() as session:
            row = self._require(
                session.get(GenerationJobRow, job_id),
                "Generation job",
                job_id,
            )
            before = row.status
            transition_job(
                before,
                target,
                retry_count=row.retry_count,
                retry_limit=row.retry_limit,
                result_verified=result_verified,
                safe_to_retry=safe_to_retry,
            )
            now = utc_now()
            if target is JobStatus.RETRY_WAIT:
                row.retry_count += 1
            if target is JobStatus.RUNNING and row.started_at is None:
                row.started_at = now
            if target in {
                JobStatus.SUCCEEDED,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
                JobStatus.INTERRUPTED,
                JobStatus.RECONCILIATION_REQUIRED,
            }:
                row.finished_at = now
            else:
                row.finished_at = None
            if target is JobStatus.FAILED and not error:
                raise ValueError("A failed job must record a non-empty safe error.")
            if error is not None:
                row.last_error = error
            if target is JobStatus.RUNNING:
                row.last_error = None
            if external_task_reference is not None:
                row.external_task_reference = external_task_reference
            if safe_reconciliation is not None:
                row.reconciliation_json = safe_reconciliation
            row.status = target
            scene = self._require(session.get(SceneRow, row.scene_id), "Scene", row.scene_id)
            self._synchronize_scene_for_job(
                scene,
                row.job_type,
                before,
                target,
                result_verified=result_verified,
            )
            session.add(
                JobEventRow(
                    id=new_id(),
                    job_id=row.id,
                    event_type=(
                        JobEventType.RESULT_RECONCILED
                        if before is JobStatus.RECONCILIATION_REQUIRED
                        else event_type
                    ),
                    status_before=before,
                    status_after=target,
                    occurred_at=now,
                    message=clean_message,
                    technical_details=safe_details,
                )
            )
            session.flush()
            return _job(row)

    def get_job(self, job_id: str) -> GenerationJob:
        with self._database.session_scope() as session:
            row = self._require(
                session.get(GenerationJobRow, job_id),
                "Generation job",
                job_id,
            )
            return _job(row)

    def list_jobs(
        self,
        *,
        project_id: str | None = None,
        scene_id: str | None = None,
        job_type: JobType | None = None,
        statuses: Iterable[JobStatus] | None = None,
    ) -> list[GenerationJob]:
        with self._database.session_scope() as session:
            statement = select(GenerationJobRow)
            if project_id is not None:
                statement = statement.where(
                    GenerationJobRow.project_id == project_id
                )
            if scene_id is not None:
                statement = statement.where(GenerationJobRow.scene_id == scene_id)
            if job_type is not None:
                statement = statement.where(GenerationJobRow.job_type == job_type)
            if statuses is not None:
                status_values = tuple(statuses)
                if not status_values:
                    return []
                statement = statement.where(
                    GenerationJobRow.status.in_(status_values)
                )
            statement = statement.order_by(
                GenerationJobRow.created_at,
                GenerationJobRow.id,
            )
            return [_job(row) for row in session.scalars(statement)]

    def list_resumable_jobs(self, project_id: str | None = None) -> list[GenerationJob]:
        return self.list_jobs(
            project_id=project_id,
            statuses=(JobStatus.PENDING, JobStatus.RETRY_WAIT),
        )

    def list_reconciliation_jobs(
        self,
        project_id: str | None = None,
    ) -> list[GenerationJob]:
        return self.list_jobs(
            project_id=project_id,
            statuses=(
                JobStatus.INTERRUPTED,
                JobStatus.RECONCILIATION_REQUIRED,
            ),
        )

    def list_job_events(self, job_id: str) -> list[JobEvent]:
        with self._database.session_scope() as session:
            self._require(
                session.get(GenerationJobRow, job_id),
                "Generation job",
                job_id,
            )
            statement = (
                select(JobEventRow)
                .where(JobEventRow.job_id == job_id)
                .order_by(JobEventRow.occurred_at, JobEventRow.id)
            )
            return [_event(row) for row in session.scalars(statement)]

    def record_job_note(
        self,
        job_id: str,
        message: str,
        *,
        technical_details: Mapping[str, Any] | None = None,
    ) -> JobEvent:
        clean_message = _safe_text(message.strip(), field_name="Job event message")
        if not clean_message:
            raise ValueError("A safe event message is required.")
        safe_details = _safe_json_object(
            technical_details,
            field_name="Job event details",
        )
        with self._database.session_scope() as session:
            row = self._require(
                session.get(GenerationJobRow, job_id),
                "Generation job",
                job_id,
            )
            now = utc_now()
            event = JobEventRow(
                id=new_id(),
                job_id=job_id,
                event_type=JobEventType.NOTE,
                status_before=row.status,
                status_after=row.status,
                occurred_at=now,
                message=clean_message,
                technical_details=safe_details,
            )
            session.add(event)
            session.flush()
            return _event(event)

    def create_asset(
        self,
        project_id: str,
        relative_path: str,
        kind: AssetKind,
        *,
        scene_id: str | None = None,
        job_id: str | None = None,
        original_filename: str | None = None,
        mime_type: str | None = None,
        size_bytes: int | None = None,
        sha256: str | None = None,
        verification_status: FileVerificationStatus = FileVerificationStatus.UNCHECKED,
        asset_id: str | None = None,
    ) -> Asset:
        path = _relative_path(relative_path)
        if size_bytes is not None and size_bytes < 0:
            raise ValueError("Asset size must not be negative.")
        if sha256 is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
            raise ValueError("Asset SHA-256 must contain exactly 64 hexadecimal characters.")
        with self._database.session_scope() as session:
            self._require(session.get(ProjectRow, project_id), "Project", project_id)
            if scene_id is not None:
                scene = self._require(session.get(SceneRow, scene_id), "Scene", scene_id)
                if scene.project_id != project_id:
                    raise ValueError("Asset scene must belong to the specified project.")
            if job_id is not None:
                job = self._require(
                    session.get(GenerationJobRow, job_id),
                    "Generation job",
                    job_id,
                )
                if job.project_id != project_id or (
                    scene_id is not None and job.scene_id != scene_id
                ):
                    raise ValueError("Asset job must belong to its project and scene.")
            row = AssetRow(
                id=asset_id or new_id(),
                project_id=project_id,
                scene_id=scene_id,
                job_id=job_id,
                kind=kind,
                relative_path=path,
                original_filename=original_filename,
                mime_type=mime_type,
                size_bytes=size_bytes,
                sha256=sha256,
                verification_status=verification_status,
                created_at=utc_now(),
            )
            session.add(row)
            session.flush()
            return _asset(row)

    def get_asset(self, asset_id: str) -> Asset:
        with self._database.session_scope() as session:
            row = self._require(session.get(AssetRow, asset_id), "Asset", asset_id)
            return _asset(row)

    def list_assets(
        self,
        *,
        project_id: str | None = None,
        scene_id: str | None = None,
        kind: AssetKind | None = None,
    ) -> list[Asset]:
        with self._database.session_scope() as session:
            statement = select(AssetRow)
            if project_id is not None:
                statement = statement.where(AssetRow.project_id == project_id)
            if scene_id is not None:
                statement = statement.where(AssetRow.scene_id == scene_id)
            if kind is not None:
                statement = statement.where(AssetRow.kind == kind)
            statement = statement.order_by(AssetRow.created_at, AssetRow.id)
            return [_asset(row) for row in session.scalars(statement)]

    def select_image(self, scene_id: str, asset_id: str) -> Scene:
        with self._database.session_scope() as session:
            scene = self._require(session.get(SceneRow, scene_id), "Scene", scene_id)
            asset = self._require(session.get(AssetRow, asset_id), "Asset", asset_id)
            if (
                asset.kind is not AssetKind.IMAGE
                or asset.verification_status is not FileVerificationStatus.VERIFIED
                or asset.project_id != scene.project_id
                or asset.scene_id != scene.id
            ):
                raise ValueError(
                    "Only a verified image asset belonging to this scene can be selected."
                )
            if scene.status not in {
                SceneStatus.NEEDS_REVIEW,
                SceneStatus.IMAGE_APPROVED,
            }:
                raise InvalidStateTransition(
                    "An image can only be selected while the scene needs review "
                    "or already has an approved image."
                )
            if scene.status is SceneStatus.NEEDS_REVIEW:
                transition_scene(
                    scene.status,
                    SceneStatus.IMAGE_APPROVED,
                    selected_image_verified=True,
                )
                scene.status = SceneStatus.IMAGE_APPROVED
            if scene.image_selection is None:
                scene.image_selection = SceneImageSelectionRow(
                    scene_id=scene.id,
                    asset_id=asset.id,
                    selected_at=utc_now(),
                )
            else:
                scene.image_selection.asset_id = asset.id
                scene.image_selection.selected_at = utc_now()
            scene.updated_at = utc_now()
            session.flush()
            return _scene(scene)

    def update_asset_verification(
        self,
        asset_id: str,
        verification_status: FileVerificationStatus,
    ) -> Asset:
        with self._database.session_scope() as session:
            row = self._require(session.get(AssetRow, asset_id), "Asset", asset_id)
            row.verification_status = verification_status
            session.flush()
            return _asset(row)

    def project_progress(self, project_id: str) -> dict[str, dict[str, int]]:
        with self._database.session_scope() as session:
            self._require(session.get(ProjectRow, project_id), "Project", project_id)
            scene_counts = {status.value: 0 for status in SceneStatus}
            for status, count in session.execute(
                select(SceneRow.status, func.count())
                .where(SceneRow.project_id == project_id)
                .group_by(SceneRow.status)
            ):
                scene_counts[status.value] = count
            job_counts = {status.value: 0 for status in JobStatus}
            for status, count in session.execute(
                select(GenerationJobRow.status, func.count())
                .where(GenerationJobRow.project_id == project_id)
                .group_by(GenerationJobRow.status)
            ):
                job_counts[status.value] = count
            return {"scenes": scene_counts, "jobs": job_counts}

    @staticmethod
    def _require(row: Any | None, entity_name: str, entity_id: str) -> Any:
        if row is None:
            raise RepositoryNotFoundError(f"{entity_name} {entity_id!r} was not found.")
        return row

    @staticmethod
    def _has_verified_selection(scene: SceneRow) -> bool:
        selection = scene.image_selection
        return (
            selection is not None
            and selection.asset.kind is AssetKind.IMAGE
            and selection.asset.verification_status
            is FileVerificationStatus.VERIFIED
            and selection.asset.scene_id == scene.id
            and selection.asset.project_id == scene.project_id
        )

    @staticmethod
    def _synchronize_scene_for_job(
        scene: SceneRow,
        job_type: JobType,
        before: JobStatus,
        after: JobStatus,
        *,
        result_verified: bool,
    ) -> None:
        if before is after:
            return
        queued_status = (
            SceneStatus.IMAGE_QUEUED
            if job_type is JobType.IMAGE
            else SceneStatus.VIDEO_QUEUED
        )
        running_status = (
            SceneStatus.IMAGE_RUNNING
            if job_type is JobType.IMAGE
            else SceneStatus.VIDEO_RUNNING
        )
        if after is JobStatus.RUNNING and scene.status is queued_status:
            transition_scene(scene.status, running_status)
            scene.status = running_status
        elif after is JobStatus.RUNNING:
            raise InvalidStateTransition(
                "A job can run only while its scene is queued for that job type."
            )
        elif after is JobStatus.RETRY_WAIT:
            if scene.status is running_status:
                retry_source = (
                    SceneStatus.READY
                    if job_type is JobType.IMAGE
                    else SceneStatus.IMAGE_APPROVED
                )
                transition_scene(scene.status, retry_source)
                scene.status = retry_source
                transition_scene(scene.status, queued_status)
                scene.status = queued_status
            elif scene.status is SceneStatus.FAILED:
                transition_scene(scene.status, queued_status)
                scene.status = queued_status
            else:
                raise InvalidStateTransition(
                    "A retry can be scheduled only from a running or failed scene."
                )
        elif after is JobStatus.PENDING and scene.status is SceneStatus.FAILED:
            transition_scene(scene.status, queued_status)
            scene.status = queued_status
        elif after is JobStatus.PENDING and before in {
            JobStatus.INTERRUPTED,
            JobStatus.RECONCILIATION_REQUIRED,
        }:
            raise InvalidStateTransition(
                "The scene must be reconciled before the job can be requeued."
            )
        elif after is JobStatus.SUCCEEDED:
            target = (
                SceneStatus.NEEDS_REVIEW
                if job_type is JobType.IMAGE
                else SceneStatus.VIDEO_READY
            )
            transition_scene(
                scene.status,
                target,
                result_verified=result_verified,
            )
            scene.status = target
        elif after in {
            JobStatus.FAILED,
            JobStatus.INTERRUPTED,
            JobStatus.RECONCILIATION_REQUIRED,
        }:
            if scene.status is not SceneStatus.FAILED:
                if scene.status not in {queued_status, running_status}:
                    raise InvalidStateTransition(
                        "The scene state does not match the job being stopped."
                    )
                transition_scene(scene.status, SceneStatus.FAILED)
                scene.status = SceneStatus.FAILED
        elif after is JobStatus.CANCELLED:
            target = (
                SceneStatus.READY
                if job_type is JobType.IMAGE
                else SceneStatus.IMAGE_APPROVED
            )
            if scene.status is not target:
                allowed_source_statuses = (
                    {SceneStatus.IMAGE_QUEUED, SceneStatus.IMAGE_RUNNING}
                    if job_type is JobType.IMAGE
                    else {SceneStatus.VIDEO_QUEUED, SceneStatus.VIDEO_RUNNING}
                )
                if scene.status not in allowed_source_statuses:
                    raise InvalidStateTransition(
                        "The scene state does not match the job being cancelled."
                    )
                transition_scene(
                    scene.status,
                    target,
                    selected_image_verified=FlowRepository._has_verified_selection(
                        scene
                    ),
                )
                scene.status = target
        scene.updated_at = utc_now()
