from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from flow_automation.automation.browser_manager import BrowserConfig
from flow_automation.ui.browser_worker import BrowserWorker


class MainWindow(QMainWindow):
    """Manual Chrome sign-in and conservative Flow connection check."""

    def __init__(self, browser_config: BrowserConfig) -> None:
        super().__init__()
        self.setWindowTitle("Flow Automation Tool")
        self.resize(960, 640)
        self._browser_worker: BrowserWorker | None = None

        self._open_browser_button = QPushButton("Open Browser")
        self._check_connection_button = QPushButton("Check Connection")
        self._check_connection_button.setEnabled(False)
        self._status = QLabel(
            "Sign in manually in the app's isolated Chrome profile. "
            "The app never requests credentials."
        )
        self._status.setWordWrap(True)
        self._status.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        actions = QHBoxLayout()
        actions.addWidget(self._open_browser_button)
        actions.addWidget(self._check_connection_button)
        layout = QVBoxLayout()
        layout.addLayout(actions)
        layout.addWidget(self._status)
        layout.addStretch(1)

        central_widget = QWidget()
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)

        self._browser_config = browser_config
        self._open_browser_button.clicked.connect(self._open_browser)
        self._check_connection_button.clicked.connect(self._check_connection)

    def _open_browser(self) -> None:
        if self._browser_worker is not None and self._browser_worker.isRunning():
            return
        worker = BrowserWorker(self._browser_config)
        worker.status_changed.connect(self._status.setText)
        worker.connection_checked.connect(self._status.setText)
        worker.finished.connect(self._browser_worker_finished)
        self._browser_worker = worker
        self._open_browser_button.setEnabled(False)
        self._check_connection_button.setEnabled(True)
        worker.start()

    def _check_connection(self) -> None:
        worker = self._browser_worker
        if worker is None or not worker.isRunning():
            self._status.setText("Open the browser before checking the connection.")
            return
        worker.request_connection_check()

    def _browser_worker_finished(self) -> None:
        self._open_browser_button.setEnabled(True)
        self._check_connection_button.setEnabled(False)
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
