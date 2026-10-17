from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from flow_automation.automation.browser_manager import (
    BrowserConfig,
    BrowserManager,
)
from flow_automation.automation.errors import BrowserAlreadyRunningError
from flow_automation.automation.session_manager import SessionManager, SessionStatus


class _FlowStubHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        content = (
            b'<a href="https://accounts.google.com/ServiceLogin">Sign in</a>'
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def test_persistent_browser_starts_locks_profile_and_reopens(
    tmp_path: Path,
) -> None:
    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).exists():
            pytest.skip("Install Chromium with `python -m playwright install chromium`.")

    server = ThreadingHTTPServer(("127.0.0.1", 0), _FlowStubHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    profile = tmp_path / "browser-profile"
    config = BrowserConfig(
        flow_url=f"http://127.0.0.1:{server.server_port}/",
        profile_path=profile,
        diagnostics_path=tmp_path / "diagnostics",
        log_path=tmp_path / "logs",
    )

    async def exercise_lifecycle() -> None:
        first_browser = BrowserManager(config, headless=True)
        second_browser = BrowserManager(config, headless=True)
        try:
            page = await first_browser.start()
            assert (
                await SessionManager().check_session(page)
                is SessionStatus.UNAUTHENTICATED
            )
            lock_probe = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "\n".join(
                        (
                            "import sys",
                            "from pathlib import Path",
                            "from flow_automation.automation.browser_manager import _ProfileLock",
                            "from flow_automation.automation.errors import BrowserAlreadyRunningError",
                            "lock = _ProfileLock(Path(sys.argv[1]))",
                            "try:",
                            "    lock.acquire()",
                            "except BrowserAlreadyRunningError:",
                            "    raise SystemExit(23)",
                            "else:",
                            "    lock.release()",
                        )
                    ),
                    str(profile),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
            assert lock_probe.returncode == 23, lock_probe.stderr
            with pytest.raises(BrowserAlreadyRunningError):
                await second_browser.start()

            page.set_default_timeout(50)
            with pytest.raises(PlaywrightTimeoutError):
                await page.wait_for_event("close")

            close_wait = asyncio.create_task(first_browser.wait_until_closed())
            await asyncio.sleep(0.1)
            assert not close_wait.done()
            await page.close()
            await asyncio.wait_for(close_wait, timeout=3)
            await first_browser.close()

            assert profile.exists()
            assert (profile / ".flow-automation.lock").exists()

            reopened_browser = BrowserManager(config, headless=True)
            try:
                await reopened_browser.start()
                assert reopened_browser.page.url == config.flow_url
            finally:
                await reopened_browser.close()
        finally:
            await first_browser.close()
            await second_browser.close()

    try:
        asyncio.run(exercise_lifecycle())
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
