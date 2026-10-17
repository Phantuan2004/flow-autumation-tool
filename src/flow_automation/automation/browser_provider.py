from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from types import TracebackType

from playwright.async_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Page,
    Playwright,
    async_playwright,
)

from flow_automation.automation.browser_capabilities import (
    BrowserCapabilities,
)
from flow_automation.automation.browser_manager import (
    BrowserConfig,
    BrowserProfileLock,
)
from flow_automation.automation.errors import (
    BrowserLifecycleError,
)
from flow_automation.diagnostics.screenshots import capture_failure_screenshot

logger = logging.getLogger(__name__)
_CDP_HOST = "127.0.0.1"
_CDP_READY_PATH = "/json/version"


def find_chrome_executable() -> Path | None:
    """Find an installed stable Chrome without selecting a personal profile."""
    candidates: list[Path] = []

    if sys.platform == "win32":
        try:
            import winreg

            app_path_key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                for access in (
                    winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
                    winreg.KEY_READ | winreg.KEY_WOW64_32KEY,
                ):
                    try:
                        with winreg.OpenKey(hive, app_path_key, 0, access) as key:
                            executable, _ = winreg.QueryValueEx(key, None)
                    except OSError:
                        continue
                    candidates.append(Path(executable.strip('"')))
        except ImportError:
            logger.info("Windows Chrome registry discovery is unavailable.")

        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base_path = os.environ.get(variable)
            if base_path:
                candidates.append(
                    Path(base_path)
                    / "Google"
                    / "Chrome"
                    / "Application"
                    / "chrome.exe"
                )
    elif sys.platform == "darwin":
        candidates.extend(
            (
                Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                Path.home()
                / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            )
        )
    else:
        for executable_name in ("google-chrome-stable", "google-chrome", "chrome"):
            executable = shutil.which(executable_name)
            if executable:
                candidates.append(Path(executable))

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


class BrowserProvider:
    """Launches an isolated Chrome profile and attaches through loopback CDP."""

    def __init__(
        self,
        config: BrowserConfig,
        *,
        startup_timeout_seconds: float = 20.0,
        executable_finder: Callable[[], Path | None] = find_chrome_executable,
    ) -> None:
        if startup_timeout_seconds <= 0:
            raise ValueError("Browser startup timeout must be greater than zero.")
        self.config = config
        self._startup_timeout_seconds = startup_timeout_seconds
        self._executable_finder = executable_finder
        self._profile_lock = BrowserProfileLock(config.profile_path)
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._page_closed: asyncio.Event | None = None

    @property
    def page(self) -> Page:
        if self._page is None or self._page.is_closed():
            raise BrowserLifecycleError("The managed Flow browser page is not open.")
        return self._page

    def get_capabilities(self) -> BrowserCapabilities:
        if self.config.channel not in {None, "chrome"}:
            return BrowserCapabilities.unavailable(
                "Only the installed Google Chrome stable channel is supported."
            )
        executable = self._executable_finder()
        if executable is None:
            return BrowserCapabilities.unavailable(
                "Google Chrome stable was not found on this machine."
            )
        return BrowserCapabilities.chrome_cdp_available()

    async def start(self) -> Page:
        if self._browser is not None:
            if self._page is None or self._page.is_closed():
                raise BrowserLifecycleError(
                    "The managed browser was closed; close it before starting again."
                )
            return self._page

        if self.config.channel not in {None, "chrome"}:
            raise BrowserLifecycleError(
                "Only the installed Google Chrome stable channel is supported."
            )

        executable = self._executable_finder()
        if executable is None:
            raise BrowserLifecycleError(
                "Google Chrome stable was not found. Install Chrome or select a "
                "supported browser before continuing."
            )

        self.config.profile_path.mkdir(parents=True, exist_ok=True)
        self._profile_lock.acquire()
        try:
            self._process, port = await asyncio.to_thread(
                self._launch_chrome, executable
            )
            endpoint = await self._wait_for_cdp(port, self._process)
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.connect_over_cdp(
                endpoint,
                timeout=self.config.default_timeout_ms,
            )
            self._context = (
                self._browser.contexts[0]
                if self._browser.contexts
                else await self._browser.new_context(accept_downloads=True)
            )
            self._context.set_default_timeout(self.config.default_timeout_ms)
            self._page = (
                self._context.pages[0]
                if self._context.pages
                else await self._context.new_page()
            )
            self._page.set_default_timeout(self.config.default_timeout_ms)
            self._page_closed = asyncio.Event()
            self._page.on(
                "close",
                lambda _: self._page_closed and self._page_closed.set(),
            )
            logger.info(
                "Connected to isolated Chrome through loopback CDP; "
                "manual sign-in is required."
            )
            return self._page
        except (OSError, PlaywrightError, TimeoutError, BrowserLifecycleError):
            await self._release_resources()
            logger.error("Could not launch or connect to isolated Chrome.")
            raise BrowserLifecycleError(
                "Could not launch or connect to Chrome. Check that Chrome is "
                "installed and that the app profile is not in use."
            ) from None
        except BaseException:
            await self._release_resources()
            raise

    async def wait_until_closed(self) -> None:
        if self._page_closed is None:
            raise BrowserLifecycleError("The managed Flow browser page is not open.")
        await self._page_closed.wait()

    async def capture_failure_screenshot(self) -> None:
        page = self._page
        if page is None or page.is_closed():
            logger.warning("No open page is available for a diagnostic screenshot.")
            return
        try:
            path = await capture_failure_screenshot(
                page,
                self.config.diagnostics_path,
            )
        except (OSError, PlaywrightError):
            logger.warning("Sanitized diagnostic screenshot could not be captured.")
        else:
            logger.info("Sanitized diagnostic screenshot saved to %s.", path.name)

    async def close(self) -> None:
        await self._release_resources()

    async def _wait_for_cdp(
        self,
        port: int,
        process: subprocess.Popen[bytes],
    ) -> str:
        endpoint = f"http://{_CDP_HOST}:{port}"
        ready_url = endpoint + _CDP_READY_PATH
        deadline = asyncio.get_running_loop().time() + self._startup_timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            if process.poll() is not None:
                raise BrowserLifecycleError("Chrome exited before CDP became ready.")
            try:
                response = await asyncio.to_thread(self._read_cdp_version, ready_url)
            except (OSError, urllib.error.URLError, json.JSONDecodeError):
                await asyncio.sleep(0.1)
                continue
            browser_name = response.get("Browser")
            if not isinstance(browser_name, str) or "Chrome" not in browser_name:
                raise BrowserLifecycleError(
                    "The local debugging endpoint is not a Chrome browser."
                )
            return endpoint
        raise BrowserLifecycleError("Chrome did not expose CDP before the timeout.")

    @staticmethod
    def _read_cdp_version(url: str) -> dict[str, object]:
        with urllib.request.urlopen(url, timeout=0.5) as response:
            data = json.loads(response.read())
        if not isinstance(data, dict):
            raise json.JSONDecodeError("Invalid CDP version response", "", 0)
        return data

    def _launch_chrome(self, executable: Path) -> tuple[subprocess.Popen[bytes], int]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind((_CDP_HOST, 0))
            port = listener.getsockname()[1]

        arguments = [
            str(executable),
            f"--user-data-dir={self.config.profile_path.resolve()}",
            f"--remote-debugging-address={_CDP_HOST}",
            f"--remote-debugging-port={port}",
            "--no-first-run",
            "--no-default-browser-check",
            self.config.flow_url,
        ]
        try:
            process = subprocess.Popen(
                arguments,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                ),
            )
        except OSError as error:
            raise BrowserLifecycleError("Chrome could not be started.") from error
        return process, port

    async def _release_resources(self) -> None:
        browser = self._browser
        playwright = self._playwright
        process = self._process
        self._browser = None
        self._context = None
        self._playwright = None
        self._page = None
        self._page_closed = None
        self._process = None
        try:
            if browser is not None:
                await browser.close()
        except PlaywrightError:
            logger.warning("CDP connection closed with a browser error.")
        try:
            if playwright is not None:
                await playwright.stop()
        finally:
            try:
                if process is not None and process.poll() is None:
                    await asyncio.to_thread(process.terminate)
                    try:
                        await asyncio.to_thread(process.wait, 5)
                    except subprocess.TimeoutExpired:
                        await asyncio.to_thread(process.kill)
                        await asyncio.to_thread(process.wait, 5)
            finally:
                self._profile_lock.release()

    async def __aenter__(self) -> BrowserProvider:
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
