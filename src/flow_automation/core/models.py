from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Mapping
from uuid import uuid4


def new_id() -> str:
    return uuid4().hex


def utc_now() -> datetime:
    return datetime.now(UTC)


class ProjectStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class SceneStatus(StrEnum):
    READY = "READY"
    IMAGE_QUEUED = "IMAGE_QUEUED"
    IMAGE_RUNNING = "IMAGE_RUNNING"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    IMAGE_APPROVED = "IMAGE_APPROVED"
    VIDEO_QUEUED = "VIDEO_QUEUED"
    VIDEO_RUNNING = "VIDEO_RUNNING"
    VIDEO_READY = "VIDEO_READY"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class JobType(StrEnum):
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RETRY_WAIT = "RETRY_WAIT"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class AssetKind(StrEnum):
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    REFERENCE = "REFERENCE"


class FileVerificationStatus(StrEnum):
    UNCHECKED = "UNCHECKED"
    VERIFIED = "VERIFIED"
    MISSING = "MISSING"
    INVALID = "INVALID"


class ReferenceKind(StrEnum):
    CHARACTER = "CHARACTER"
    ENVIRONMENT = "ENVIRONMENT"
    STYLE = "STYLE"
    OTHER = "OTHER"


class JobEventType(StrEnum):
    CREATED = "CREATED"
    STATUS_CHANGED = "STATUS_CHANGED"
    RESULT_RECONCILED = "RESULT_RECONCILED"
    NOTE = "NOTE"


@dataclass(frozen=True, slots=True)
class Project:
    id: str
    name: str
    root_path: str
    description: str = ""
    status: ProjectStatus = ProjectStatus.ACTIVE
    default_config: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class SceneDraft:
    scene_number: int
    image_prompt: str
    video_prompt: str = ""
    reference_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Scene:
    id: str
    project_id: str
    scene_number: int
    image_prompt: str
    video_prompt: str = ""
    reference_ids: tuple[str, ...] = ()
    status: SceneStatus = SceneStatus.READY
    selected_image_asset_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class GenerationJob:
    id: str
    project_id: str
    scene_id: str
    job_type: JobType
    status: JobStatus = JobStatus.PENDING
    retry_count: int = 0
    retry_limit: int = 3
    created_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_error: str | None = None
    external_task_reference: str | None = None
    config: Mapping[str, Any] = field(default_factory=dict)
    reconciliation_details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Asset:
    id: str
    project_id: str
    relative_path: str
    kind: AssetKind
    scene_id: str | None = None
    job_id: str | None = None
    original_filename: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    verification_status: FileVerificationStatus = FileVerificationStatus.UNCHECKED
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class Reference:
    id: str
    project_id: str
    relative_path: str
    kind: ReferenceKind
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class JobEvent:
    id: str
    job_id: str
    event_type: JobEventType
    occurred_at: datetime
    message: str
    status_before: JobStatus | None = None
    status_after: JobStatus | None = None
    technical_details: Mapping[str, Any] = field(default_factory=dict)
