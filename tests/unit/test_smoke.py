from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import QTimer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from flow_automation import __version__
from flow_automation.automation.browser_manager import BrowserConfig
from flow_automation.bootstrap import create_application
from flow_automation.main import main
from flow_automation.ui.main_window import MainWindow


def test_application_entry_point_reports_version() -> None:
    project_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-m", "flow_automation.main", "--version"],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == f"flow-automation {__version__}"
    assert callable(main)


def test_qt_application_and_main_window_can_be_created() -> None:
    project_root = Path(__file__).resolve().parents[2]
    application = create_application(["flow-automation-test"])
    browser_config = BrowserConfig.from_toml(
        project_root / "config" / "defaults.toml",
        project_root=project_root,
    )
    window = MainWindow(browser_config)

    assert application is not None
    assert window.windowTitle() == "Flow Automation Tool"
    QTimer.singleShot(0, application.quit)
    assert main([]) == 0
