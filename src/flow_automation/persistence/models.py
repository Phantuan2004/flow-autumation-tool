from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Table,
    Column,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from flow_automation.core.models import (
    AssetKind,
    FileVerificationStatus,
    JobEventType,
    JobStatus,
    JobType,
    ProjectStatus,
    ReferenceKind,
    SceneStatus,
    utc_now,
)


def _enum_values(enum_type: type) -> list[str]:
    return [item.value for item in enum_type]


class Base(DeclarativeBase):
    pass


scene_references = Table(
    "scene_references",
    Base.metadata,
    Column(
        "scene_id",
        String(32),
        ForeignKey("scenes.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "reference_id",
        String(32),
        ForeignKey("references.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class ProjectRow(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False, default="")
    root_path: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(
            ProjectStatus,
            values_callable=_enum_values,
            native_enum=False,
            create_constraint=True,
            name="project_status",
        ),
        nullable=False,
    )
    default_config: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    scenes: Mapped[list[SceneRow]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    jobs: Mapped[list[GenerationJobRow]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    assets: Mapped[list[AssetRow]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    references: Mapped[list[ReferenceRow]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class SceneRow(Base):
    __tablename__ = "scenes"
    __table_args__ = (
        UniqueConstraint("project_id", "scene_number", name="uq_scene_project_number"),
        CheckConstraint("scene_number > 0", name="ck_scene_number_positive"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scene_number: Mapped[int] = mapped_column(Integer, nullable=False)
    image_prompt: Mapped[str] = mapped_column(String, nullable=False)
    video_prompt: Mapped[str] = mapped_column(String, nullable=False, default="")
    status: Mapped[SceneStatus] = mapped_column(
        Enum(
            SceneStatus,
            values_callable=_enum_values,
            native_enum=False,
            create_constraint=True,
            name="scene_status",
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    project: Mapped[ProjectRow] = relationship(back_populates="scenes")
    references: Mapped[list[ReferenceRow]] = relationship(
        secondary=scene_references, back_populates="scenes"
    )
    image_selection: Mapped[SceneImageSelectionRow | None] = relationship(
        back_populates="scene",
        cascade="all, delete-orphan",
        uselist=False,
    )
    jobs: Mapped[list[GenerationJobRow]] = relationship(back_populates="scene")
    assets: Mapped[list[AssetRow]] = relationship(back_populates="scene")


class ReferenceRow(Base):
    __tablename__ = "references"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    relative_path: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[ReferenceKind] = mapped_column(
        Enum(
            ReferenceKind,
            values_callable=_enum_values,
            native_enum=False,
            create_constraint=True,
            name="reference_kind",
        ),
        nullable=False,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    project: Mapped[ProjectRow] = relationship(back_populates="references")
    scenes: Mapped[list[SceneRow]] = relationship(
        secondary=scene_references, back_populates="references"
    )


class GenerationJobRow(Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (
        CheckConstraint("retry_count >= 0", name="ck_job_retry_count_nonnegative"),
        CheckConstraint("retry_limit >= 0", name="ck_job_retry_limit_nonnegative"),
        CheckConstraint(
            "retry_count <= retry_limit",
            name="ck_job_retry_count_lte_limit",
        ),
        Index("ix_job_project_status_created", "project_id", "status", "created_at"),
        Index("ix_job_scene_type_status", "scene_id", "job_type", "status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    scene_id: Mapped[str] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False
    )
    job_type: Mapped[JobType] = mapped_column(
        Enum(
            JobType,
            values_callable=_enum_values,
            native_enum=False,
            create_constraint=True,
            name="job_type",
        ),
        nullable=False,
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(
            JobStatus,
            values_callable=_enum_values,
            native_enum=False,
            create_constraint=True,
            name="job_status",
        ),
        nullable=False,
        index=True,
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    external_task_reference: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    config_json: Mapped[dict[str, Any]] = mapped_column(
        "config", JSON, nullable=False, default=dict
    )
    reconciliation_json: Mapped[dict[str, Any]] = mapped_column(
        "reconciliation_details", JSON, nullable=False, default=dict
    )

    project: Mapped[ProjectRow] = relationship(back_populates="jobs")
    scene: Mapped[SceneRow] = relationship(back_populates="jobs")
    assets: Mapped[list[AssetRow]] = relationship(back_populates="job")
    events: Mapped[list[JobEventRow]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class AssetRow(Base):
    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0",
            name="ck_asset_size_nonnegative",
        ),
        UniqueConstraint("project_id", "relative_path", name="uq_asset_project_path"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scene_id: Mapped[str | None] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE"), nullable=True, index=True
    )
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[AssetKind] = mapped_column(
        Enum(
            AssetKind,
            values_callable=_enum_values,
            native_enum=False,
            create_constraint=True,
            name="asset_kind",
        ),
        nullable=False,
    )
    relative_path: Mapped[str] = mapped_column(String, nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verification_status: Mapped[FileVerificationStatus] = mapped_column(
        Enum(
            FileVerificationStatus,
            values_callable=_enum_values,
            native_enum=False,
            create_constraint=True,
            name="file_verification_status",
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    project: Mapped[ProjectRow] = relationship(back_populates="assets")
    scene: Mapped[SceneRow | None] = relationship(back_populates="assets")
    job: Mapped[GenerationJobRow | None] = relationship(back_populates="assets")
    selected_by: Mapped[list[SceneImageSelectionRow]] = relationship(
        back_populates="asset"
    )


class SceneImageSelectionRow(Base):
    __tablename__ = "scene_image_selections"

    scene_id: Mapped[str] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE"), primary_key=True
    )
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    selected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    scene: Mapped[SceneRow] = relationship(back_populates="image_selection")
    asset: Mapped[AssetRow] = relationship(back_populates="selected_by")


class JobEventRow(Base):
    __tablename__ = "job_events"
    __table_args__ = (Index("ix_job_event_job_time", "job_id", "occurred_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[JobEventType] = mapped_column(
        Enum(
            JobEventType,
            values_callable=_enum_values,
            native_enum=False,
            create_constraint=True,
            name="job_event_type",
        ),
        nullable=False,
    )
    status_before: Mapped[JobStatus | None] = mapped_column(
        Enum(
            JobStatus,
            values_callable=_enum_values,
            native_enum=False,
            create_constraint=True,
            name="job_event_before_status",
        ),
        nullable=True,
    )
    status_after: Mapped[JobStatus | None] = mapped_column(
        Enum(
            JobStatus,
            values_callable=_enum_values,
            native_enum=False,
            create_constraint=True,
            name="job_event_after_status",
        ),
        nullable=True,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    message: Mapped[str] = mapped_column(String, nullable=False)
    technical_details: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )

    job: Mapped[GenerationJobRow] = relationship(back_populates="events")
