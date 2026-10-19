from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from flow_automation.application.job_control import JobControlService
from flow_automation.jobs.job_runner import JobProcessor
from flow_automation.jobs.retry_policy import RetryPolicy
from flow_automation.jobs.scheduler import BatchScheduler
from flow_automation.persistence import Database, FlowRepository


def create_application(argv: Sequence[str] | None = None) -> QApplication:
    """Create or reuse the Qt application for this process."""
    existing_application = QApplication.instance()
    if existing_application is not None:
        return existing_application

    application_args = list(argv) if argv is not None else sys.argv
    return QApplication(application_args)


def create_flow_repository(database_path: Path) -> tuple[Database, FlowRepository]:
    """Initialize the application database and return its repository interface."""
    database = Database.from_path(database_path)
    database.initialize()
    return database, FlowRepository(database)


def create_job_control_service(
    repository: FlowRepository,
    processor: JobProcessor,
    *,
    concurrency: int = 1,
    browser_bound: bool = True,
    retry_policy: RetryPolicy | None = None,
) -> JobControlService:
    """Create the application command/event interface for background job control."""
    scheduler = BatchScheduler(
        repository,
        processor,
        concurrency=concurrency,
        browser_bound=browser_bound,
        retry_policy=retry_policy,
    )
    return JobControlService(scheduler)
