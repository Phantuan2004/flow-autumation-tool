from __future__ import annotations

import sys
from collections.abc import Sequence

from PyQt6.QtWidgets import QApplication


def create_application(argv: Sequence[str] | None = None) -> QApplication:
    """Create or reuse the Qt application for this process."""
    existing_application = QApplication.instance()
    if existing_application is not None:
        return existing_application

    application_args = list(argv) if argv is not None else sys.argv
    return QApplication(application_args)

