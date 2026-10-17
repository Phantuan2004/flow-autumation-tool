from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from flow_automation import __version__
from flow_automation.automation.browser_manager import BrowserConfig
from flow_automation.automation.browser_provider import BrowserProvider
from flow_automation.automation.errors import (
    BrowserAlreadyRunningError,
    BrowserLifecycleError,
)
from flow_automation.bootstrap import create_application
from flow_automation.diagnostics.logging_config import configure_logging
from flow_automation.ui.main_window import MainWindow


def _load_browser_config() -> BrowserConfig:
    project_root = Path(__file__).resolve().parents[2]
    return BrowserConfig.from_toml(
        project_root / "config" / "defaults.toml",
        project_root=project_root,
    )


async def _open_flow_for_manual_sign_in() -> int:
    config = _load_browser_config()
    configure_logging(config.log_path, config.log_level)
    browser = BrowserProvider(config)
    try:
        await browser.start()
        print(
            "Flow is open in isolated Chrome. Sign in manually; close Chrome when finished.",
            flush=True,
        )
        await browser.wait_until_closed()
    except (BrowserAlreadyRunningError, BrowserLifecycleError) as error:
        print(f"Could not open the isolated Chrome browser: {error}", file=sys.stderr)
        return 1
    finally:
        await browser.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="flow-automation")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--open-flow",
        action="store_true",
        help="Open Flow in the app's isolated Chrome profile for manual sign-in.",
    )
    args = parser.parse_args(argv)
    if args.open_flow:
        return asyncio.run(_open_flow_for_manual_sign_in())

    config = _load_browser_config()
    configure_logging(config.log_path, config.log_level)
    application = create_application()
    window = MainWindow(config)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
