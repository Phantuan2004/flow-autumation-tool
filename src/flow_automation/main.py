from __future__ import annotations

import argparse
from collections.abc import Sequence

from flow_automation import __version__
from flow_automation.bootstrap import create_application
from flow_automation.ui.main_window import MainWindow


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="flow-automation")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    args = parser.parse_args(argv)

    application = create_application()
    window = MainWindow()
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())

