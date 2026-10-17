from __future__ import annotations

import logging
from pathlib import Path


def configure_logging(log_directory: Path, level_name: str) -> None:
    level = logging.getLevelNamesMapping().get(level_name.upper())
    if not isinstance(level, int):
        raise ValueError(f"Unsupported log level: {level_name!r}")

    if logging.getLogger().handlers:
        return

    log_directory.mkdir(parents=True, exist_ok=True)
    log_file = logging.FileHandler(
        log_directory / "flow-automation.log",
        encoding="utf-8",
    )
    console = logging.StreamHandler()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=(log_file, console),
    )
