from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from flow_automation.automation.browser_manager import BrowserConfig
from flow_automation.automation.browser_provider import (
    BrowserProvider,
    find_chrome_executable,
)
from flow_automation.automation.errors import BrowserAlreadyRunningError


class _LocalPageHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        content = b"<html><title>Local CDP fixture</title><body>Fixture</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def test_isolated_chrome_cdp_lifecycle_and_profile_lock(tmp_path: Path) -> None:
    executable = find_chrome_executable()
    if executable is None:
        pytest.skip("Install Google Chrome stable to run the CDP lifecycle test.")

    server = ThreadingHTTPServer(("127.0.0.1", 0), _LocalPageHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    config = BrowserConfig(
        flow_url=f"http://127.0.0.1:{server.server_port}/",
        profile_path=tmp_path / "isolated-profile",
        diagnostics_path=tmp_path / "diagnostics",
        log_path=tmp_path / "logs",
        channel="chrome",
    )

    async def exercise_provider() -> None:
        provider = BrowserProvider(config, startup_timeout_seconds=15)
        await provider.start()
        process = provider._process
        assert process is not None
        assert provider.page.url == config.flow_url
        assert await provider.page.title() == "Local CDP fixture"

        with pytest.raises(BrowserAlreadyRunningError):
            await BrowserProvider(config).start()

        close_wait = asyncio.create_task(provider.wait_until_closed())
        await provider.page.close()
        await asyncio.wait_for(close_wait, timeout=5)
        await provider.close()

        assert process.poll() is not None
        assert config.profile_path.exists()

    try:
        asyncio.run(exercise_provider())
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
