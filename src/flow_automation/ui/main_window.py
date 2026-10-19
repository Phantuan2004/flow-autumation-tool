from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QCloseEvent, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from flow_automation.automation.browser_manager import BrowserConfig
from flow_automation.ui.browser_worker import BrowserWorker


class MainWindow(QMainWindow):
    """Desktop controls for Flow image jobs and verified-image review."""

    def __init__(self, browser_config: BrowserConfig) -> None:
        super().__init__()
        self.setWindowTitle("Flow Automation Tool")
        self.resize(1024, 900)
        self._browser_worker: BrowserWorker | None = None
        self._queued_image_count = 0

        self._open_browser_button = QPushButton("Open Browser")
        self._check_connection_button = QPushButton("Check Connection")
        self._check_connection_button.setEnabled(False)
        self._project_picker = QComboBox()
        self._project_name = QLineEdit()
        self._project_name.setPlaceholderText("New project name")
        self._create_project_button = QPushButton("Create Project")
        self._scene_prompt = QPlainTextEdit()
        self._scene_prompt.setPlaceholderText("Prompt for a scene image")
        self._scene_prompt.setMaximumHeight(90)
        self._add_scene_button = QPushButton("Add Scene")
        self._queue_images_button = QPushButton("Queue Images")
        self._start_images_button = QPushButton("Start Image Batch")
        self._pause_button = QPushButton("Pause")
        self._resume_button = QPushButton("Resume")
        self._cancel_pending_button = QPushButton("Cancel Pending")
        self._retry_failed_button = QPushButton("Retry Failed")
        self._refresh_gallery_button = QPushButton("Refresh Gallery")
        self._approve_image_button = QPushButton("Approve Selected Image")
        self._gallery = QListWidget()
        self._gallery.setIconSize(QSize(180, 120))
        self._gallery.setViewMode(QListWidget.ViewMode.IconMode)
        self._gallery.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._status = QLabel(
            "Sign in manually in the isolated Chrome profile. "
            "Open a Flow project editor before queueing image jobs."
        )
        self._status.setWordWrap(True)
        self._status.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._workspace_controls = [
            self._project_picker,
            self._project_name,
            self._create_project_button,
            self._scene_prompt,
            self._add_scene_button,
            self._queue_images_button,
            self._start_images_button,
            self._pause_button,
            self._resume_button,
            self._cancel_pending_button,
            self._retry_failed_button,
            self._refresh_gallery_button,
            self._approve_image_button,
            self._gallery,
        ]
        for control in self._workspace_controls:
            control.setEnabled(False)

        browser_actions = QHBoxLayout()
        browser_actions.addWidget(self._open_browser_button)
        browser_actions.addWidget(self._check_connection_button)

        project_box = QGroupBox("Project and scene prompts")
        project_layout = QVBoxLayout()
        project_row = QHBoxLayout()
        project_row.addWidget(self._project_picker, 1)
        project_row.addWidget(self._project_name, 1)
        project_row.addWidget(self._create_project_button)
        project_layout.addLayout(project_row)
        project_layout.addWidget(self._scene_prompt)
        project_layout.addWidget(self._add_scene_button)
        project_box.setLayout(project_layout)

        jobs_box = QGroupBox("Image batch")
        jobs_layout = QHBoxLayout()
        for button in (
            self._queue_images_button,
            self._start_images_button,
            self._pause_button,
            self._resume_button,
            self._cancel_pending_button,
            self._retry_failed_button,
        ):
            jobs_layout.addWidget(button)
        jobs_box.setLayout(jobs_layout)

        gallery_box = QGroupBox("Verified image gallery")
        gallery_layout = QVBoxLayout()
        gallery_actions = QHBoxLayout()
        gallery_actions.addWidget(self._refresh_gallery_button)
        gallery_actions.addWidget(self._approve_image_button)
        gallery_layout.addLayout(gallery_actions)
        gallery_layout.addWidget(self._gallery)
        gallery_box.setLayout(gallery_layout)

        layout = QVBoxLayout()
        layout.addLayout(browser_actions)
        layout.addWidget(project_box)
        layout.addWidget(jobs_box)
        layout.addWidget(gallery_box, 1)
        layout.addWidget(self._status)
        central_widget = QWidget()
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)

        self._browser_config = browser_config
        self._open_browser_button.clicked.connect(self._open_browser)
        self._check_connection_button.clicked.connect(self._check_connection)
        self._create_project_button.clicked.connect(self._create_project)
        self._add_scene_button.clicked.connect(self._add_scene)
        self._project_picker.currentIndexChanged.connect(self._project_changed)
        self._queue_images_button.clicked.connect(self._queue_images)
        self._start_images_button.clicked.connect(self._start_images)
        self._pause_button.clicked.connect(self._pause)
        self._resume_button.clicked.connect(self._resume)
        self._cancel_pending_button.clicked.connect(self._cancel_pending)
        self._retry_failed_button.clicked.connect(self._retry_failed)
        self._refresh_gallery_button.clicked.connect(self._refresh_gallery)
        self._approve_image_button.clicked.connect(self._approve_selected_image)

    def _open_browser(self) -> None:
        if self._browser_worker is not None and self._browser_worker.isRunning():
            return
        worker = BrowserWorker(self._browser_config)
        worker.status_changed.connect(self._on_worker_status)
        worker.connection_checked.connect(self._status.setText)
        worker.projects_loaded.connect(self._populate_projects)
        worker.gallery_loaded.connect(self._populate_gallery)
        worker.operation_completed.connect(self._on_operation_completed)
        worker.scheduler_event.connect(self._on_scheduler_event)
        worker.finished.connect(self._browser_worker_finished)
        self._browser_worker = worker
        self._open_browser_button.setEnabled(False)
        self._check_connection_button.setEnabled(False)
        worker.start()

    def _on_worker_status(self, message: str) -> None:
        self._status.setText(message)
        if message.startswith("Chrome is open."):
            self._check_connection_button.setEnabled(True)
            worker = self._browser_worker
            if worker is not None:
                worker.request_projects()

    def _check_connection(self) -> None:
        worker = self._browser_worker
        if worker is None or not worker.isRunning():
            self._status.setText("Open the browser before checking the connection.")
            return
        worker.request_connection_check()

    def _create_project(self) -> None:
        name = self._project_name.text().strip()
        if not name:
            self._status.setText("Enter a project name first.")
            return
        worker = self._active_worker()
        if worker is not None:
            worker.request_create_project(name)

    def _add_scene(self) -> None:
        project_id = self._selected_project_id()
        prompt = self._scene_prompt.toPlainText().strip()
        if not project_id or not prompt:
            self._status.setText("Select a project and enter a scene prompt.")
            return
        worker = self._active_worker()
        if worker is not None:
            worker.request_add_scene(project_id, prompt)
            self._scene_prompt.clear()

    def _project_changed(self, _: int) -> None:
        self._queued_image_count = self._selected_queued_count()
        self._refresh_gallery()

    def _queue_images(self) -> None:
        project_id = self._selected_project_id()
        worker = self._active_worker()
        if not project_id or worker is None:
            self._status.setText("Select a project and open the browser first.")
            return
        worker.request_queue_images(project_id)

    def _start_images(self) -> None:
        project_id = self._selected_project_id()
        worker = self._active_worker()
        if not project_id or worker is None:
            self._status.setText("Select a project and open the browser first.")
            return
        project_name = self._project_picker.currentText().split(" (", 1)[0]
        answer = QMessageBox.question(
            self,
            "Start Flow image batch",
            (
                f"Start up to {self._queued_image_count} queued image job(s) "
                f"for {project_name}?\n\n"
                "Each generation may consume Flow credits. Ensure the Flow "
                "project editor is open and Nano Banana 2.1 with x1 output is "
                "selected. Stop if Flow requests verification."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer is QMessageBox.StandardButton.Yes:
            worker.request_start_images(project_id)

    def _pause(self) -> None:
        worker = self._active_worker()
        if worker is not None:
            worker.request_pause()

    def _resume(self) -> None:
        worker = self._active_worker()
        if worker is not None:
            worker.request_resume()

    def _cancel_pending(self) -> None:
        project_id = self._selected_project_id()
        worker = self._active_worker()
        if project_id and worker is not None:
            worker.request_cancel_pending(project_id)

    def _retry_failed(self) -> None:
        project_id = self._selected_project_id()
        worker = self._active_worker()
        if not project_id or worker is None:
            return
        answer = QMessageBox.question(
            self,
            "Retry failed image jobs",
            (
                "Retry eligible failed image jobs? Each retry may consume Flow "
                "credits. Confirm only if you also want any other already-queued "
                "image jobs in this project to run. Jobs with uncertain submission "
                "outcomes are excluded and require manual reconciliation."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer is QMessageBox.StandardButton.Yes:
            worker.request_retry_failed(project_id)

    def _refresh_gallery(self) -> None:
        project_id = self._selected_project_id()
        worker = self._active_worker()
        if project_id and worker is not None:
            worker.request_load_gallery(project_id)

    def _approve_selected_image(self) -> None:
        item = self._gallery.currentItem()
        worker = self._active_worker()
        if item is None or worker is None:
            self._status.setText("Select a verified image from the gallery.")
            return
        scene_id, asset_id = item.data(Qt.ItemDataRole.UserRole)
        worker.request_approve_image(scene_id, asset_id)

    def _populate_projects(self, projects: list[dict[str, Any]]) -> None:
        previous_id = self._selected_project_id()
        self._project_picker.blockSignals(True)
        self._project_picker.clear()
        for entry in projects:
            project = entry["project"]
            scene_count = entry["scene_count"]
            queued_count = entry["queued_count"]
            self._project_picker.addItem(
                f"{project.name} ({scene_count} scenes, {queued_count} queued)",
                (project.id, queued_count),
            )
        if previous_id:
            for index in range(self._project_picker.count()):
                if self._project_picker.itemData(index)[0] == previous_id:
                    self._project_picker.setCurrentIndex(index)
                    break
        self._project_picker.blockSignals(False)
        has_projects = self._project_picker.count() > 0
        self._set_workspace_enabled(True)
        self._add_scene_button.setEnabled(has_projects)
        self._queue_images_button.setEnabled(has_projects)
        self._start_images_button.setEnabled(has_projects)
        self._pause_button.setEnabled(has_projects)
        self._resume_button.setEnabled(has_projects)
        self._cancel_pending_button.setEnabled(has_projects)
        self._retry_failed_button.setEnabled(has_projects)
        self._refresh_gallery_button.setEnabled(has_projects)
        self._approve_image_button.setEnabled(has_projects)
        self._queued_image_count = self._selected_queued_count()
        if has_projects:
            self._refresh_gallery()

    def _populate_gallery(self, items: list[dict[str, Any]]) -> None:
        self._gallery.clear()
        for image in items:
            state = "APPROVED" if image["selected"] else "Needs review"
            item = QListWidgetItem(
                f"Scene {image['scene_number']:03d} - {state}"
            )
            item.setToolTip(image["prompt"])
            item.setData(
                Qt.ItemDataRole.UserRole,
                (image["scene_id"], image["asset_id"]),
            )
            path = image["path"]
            if path.is_file():
                pixmap = QPixmap(str(path))
                if not pixmap.isNull():
                    item.setIcon(pixmap.scaled(
                        180,
                        120,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    ))
            self._gallery.addItem(item)
        self._approve_image_button.setEnabled(bool(items))
        if not items:
            self._status.setText("No verified image variants are available yet.")

    def _on_operation_completed(self, operation: str, result: dict[str, Any]) -> None:
        if not result.get("ok", False):
            self._status.setText(f"{operation}: {result.get('message', 'Failed.')}")
            return
        if operation == "create_project":
            self._project_name.clear()
        if operation == "queue":
            self._queued_image_count = result.get(
                "queued_count",
                self._queued_image_count,
            )
            self._status.setText(result.get("message", "Queue updated."))
            worker = self._active_worker()
            if worker is not None:
                worker.request_projects()
            return
        if operation == "retry":
            self._status.setText(result.get("message", "Retry started."))
            worker = self._active_worker()
            if worker is not None:
                worker.request_projects()
            return
        if operation == "batch_finished":
            self._status.setText(result["message"])
            worker = self._active_worker()
            if worker is not None:
                worker.request_projects()
                worker.request_load_gallery(result["project_id"])
            return
        self._status.setText(result.get("message", "Done."))

    def _on_scheduler_event(self, event: Any) -> None:
        progress = (
            f"{event.completed}/{event.total}: "
            if event.total > 0
            else ""
        )
        self._status.setText(f"{progress}{event.message}")
        if event.status is not None and event.status.value == "SUCCEEDED":
            worker = self._active_worker()
            project_id = self._selected_project_id()
            if worker is not None and project_id:
                worker.request_load_gallery(project_id)

    def _selected_project_id(self) -> str | None:
        data = self._project_picker.currentData()
        return data[0] if data else None

    def _selected_queued_count(self) -> int:
        data = self._project_picker.currentData()
        return int(data[1]) if data else 0

    def _active_worker(self) -> BrowserWorker | None:
        worker = self._browser_worker
        if worker is None or not worker.isRunning():
            return None
        return worker

    def _set_workspace_enabled(self, enabled: bool) -> None:
        for control in self._workspace_controls:
            control.setEnabled(enabled)

    def _browser_worker_finished(self) -> None:
        self._open_browser_button.setEnabled(True)
        self._check_connection_button.setEnabled(False)
        self._set_workspace_enabled(False)
        self._project_picker.clear()
        self._gallery.clear()
        self._browser_worker = None

    def closeEvent(self, event: QCloseEvent) -> None:
        worker = self._browser_worker
        if worker is not None and worker.isRunning():
            worker.request_stop()
            if not worker.wait(10_000):
                self._status.setText(
                    "The browser is still shutting down. Close it and try again."
                )
                event.ignore()
                return
        super().closeEvent(event)
