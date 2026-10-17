from __future__ import annotations

import asyncio
import errno
import logging
import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import BinaryIO
from urllib.parse import urlsplit

from playwright.async_api import (
    BrowserContext,
    Error as PlaywrightError,
    Page,
    Playwright,
    async_playwright,
)

from flow_automation.automation.errors import (
    BrowserAlreadyRunningError,
    BrowserLifecycleError,
)
from flow_automation.diagnostics.screenshots import capture_failure_screenshot

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BrowserConfig:
    flow_url: str
    profile_path: Path
    diagnostics_path: Path
    log_path: Path
    log_level: str = "INFO"
    default_timeout_ms: int = 15_000
    channel: str | None = None

    def __post_init__(self) -> None:
        parsed_url = urlsplit(self.flow_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("Flow URL must be an absolute HTTP(S) URL")
        if self.default_timeout_ms <= 0:
            raise ValueError("Browser timeout must be greater than zero")

    @classmethod
    def from_toml(
        cls,
        config_path: Path,
        *,
        project_root: Path,
    ) -> BrowserConfig:
        with config_path.open("rb") as config_file:
            config = tomllib.load(config_file)

        flow = config["flow"]
        paths = config["paths"]
        browser = config["browser"]
        return cls(
            flow_url=flow["url"],
            profile_path=project_root / paths["browser_profile"],
            diagnostics_path=project_root / paths["diagnostics"],
            log_path=project_root / paths["logs"],
            log_level=config["application"]["log_level"],
            default_timeout_ms=browser["default_timeout_ms"],
            channel=browser["channel"],
        )


class BrowserProfileLock:
    """An OS-level lock held for the lifetime of one persistent context."""

    def __init__(self, profile_path: Path) -> None:
        self._lock_path = profile_path / ".flow-automation.lock"
        self._file: BinaryIO | None = None

    def acquire(self) -> None:
        lock_file = self._lock_path.open("a+b")
        try:
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            lock_file.close()
            if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK} or getattr(
                error,
                "winerror",
                None,
            ) in {33, 36}:
                raise BrowserAlreadyRunningError(
                    "The Chromium profile is already in use by another process."
                ) from error
            raise BrowserLifecycleError(
                "Could not initialize the Chromium profile lock."
            ) from error
        self._file = lock_file

    def release(self) -> None:
        lock_file = self._file
        if lock_file is None:
            return
        self._file = None
        try:
            if sys.platform == "win32":
                import msvcrt

                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()


# Kept for compatibility with the existing browser-manager tests and callers.
_ProfileLock = BrowserProfileLock


class BrowserManager:
    """Owns one visible Chromium persistent context for a profile."""

    def __init__(self, config: BrowserConfig, *, headless: bool = False) -> None:
        self.config = config
        self._headless = headless
        self._profile_lock = BrowserProfileLock(config.profile_path)
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._page_closed: asyncio.Event | None = None

    @property
    def page(self) -> Page:
        if self._page is None or self._page.is_closed():
            raise BrowserLifecycleError("The managed Flow browser page is not open.")
        return self._page

    async def start(self) -> Page:
        if self._context is not None:
            if self._page is None or self._page.is_closed():
                raise BrowserLifecycleError(
                    "The managed browser was closed; close it before starting again."
                )
            return self._page

        self.config.profile_path.mkdir(parents=True, exist_ok=True)
        try:
            self._profile_lock.acquire()
        except BrowserAlreadyRunningError:
            logger.warning("Chromium profile is already in use; no browser was launched.")
            raise
        try:
            self._playwright = await async_playwright().start()
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.config.profile_path),
                channel=self.config.channel,
                headless=self._headless,
                accept_downloads=True,
                timeout=self.config.default_timeout_ms,
            )
            self._context.set_default_timeout(self.config.default_timeout_ms)
            self._page = (
                self._context.pages[0]
                if self._context.pages
                else await self._context.new_page()
            )
            page_closed = asyncio.Event()
            self._page_closed = page_closed
            self._page.on("close", lambda _: page_closed.set())
            try:
                await self._page.goto(
                    self.config.flow_url,
                    wait_until="domcontentloaded",
                    timeout=self.config.default_timeout_ms,
                )
            except PlaywrightError:
                logger.warning("Flow page navigation failed.")
                await self._capture_safely(self._page)
                raise BrowserLifecycleError(
                    "Could not navigate to the configured Flow URL."
                ) from None
            logger.info("Chromium persistent context started and Flow page opened.")
            return self._page
        except PlaywrightError:
            await self._release_resources()
            logger.error("Chromium could not start the persistent browser context.")
            raise BrowserLifecycleError(
                "Could not start the Chromium persistent context."
            ) from None
        except BaseException:
            await self._release_resources()
            raise

    async def close(self) -> None:
        await self._release_resources()
        logger.info("Chromium persistent context closed.")

    async def wait_until_closed(self) -> None:
        """Wait for manual browser close without inheriting the locator timeout."""
        if self._page_closed is None:
            raise BrowserLifecycleError("The managed Flow browser page is not open.")
        await self._page_closed.wait()

    async def capture_failure_screenshot(self) -> None:
        page = self._page
        if page is None or page.is_closed():
            logger.warning("No open page is available for a diagnostic screenshot.")
            return
        await self._capture_safely(page)

    async def _release_resources(self) -> None:
        context = self._context
        playwright = self._playwright
        self._context = None
        self._playwright = None
        self._page = None
        self._page_closed = None
        try:
            if context is not None:
                await context.close()
        finally:
            try:
                if playwright is not None:
                    await playwright.stop()
            finally:
                self._profile_lock.release()

    async def _capture_safely(self, page: Page) -> None:
        try:
            path = await capture_failure_screenshot(
                page,
                self.config.diagnostics_path,
            )
        except (OSError, PlaywrightError):
            logger.warning("Sanitized diagnostic screenshot could not be captured.")
        else:
            logger.info("Sanitized diagnostic screenshot saved to %s.", path.name)

    async def __aenter__(self) -> BrowserManager:
        await self.start()
        return self

    async def __aexit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        del _exception_type, _exception, _traceback
        await self.close()
