from __future__ import annotations

import asyncio
import logging
from concurrent.futures import Future
from typing import TYPE_CHECKING

from PyQt6.QtCore import QThread, pyqtSignal

from flow_automation.automation.browser_provider import BrowserProvider
from flow_automation.automation.errors import (
    BrowserAlreadyRunningError,
    BrowserLifecycleError,
)
from flow_automation.automation.flow_adapter import FlowAdapter
from flow_automation.automation.session_manager import SessionManager, SessionStatus

if TYPE_CHECKING:
    from flow_automation.automation.browser_manager import BrowserConfig

logger = logging.getLogger(__name__)


class BrowserWorker(QThread):
    """Owns the browser and Playwright event loop away from the UI thread."""

    status_changed = pyqtSignal(str)
    connection_checked = pyqtSignal(str)

    def __init__(self, config: BrowserConfig) -> None:
        super().__init__()
        self._config = config
        self._loop: asyncio.AbstractEventLoop | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._provider: BrowserProvider | None = None

    def run(self) -> None:
        try:
            asyncio.run(self._serve())
        except Exception:
            logger.exception("Browser worker stopped unexpectedly.")
            self.status_changed.emit(
                "Browser stopped unexpectedly. Check the application log."
            )

    def request_connection_check(self) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            self.connection_checked.emit(
                "Browser is not ready. Open it before checking the connection."
            )
            return
        future = asyncio.run_coroutine_threadsafe(self._check_connection(), loop)
        future.add_done_callback(self._report_connection_result)

    def request_stop(self) -> None:
        loop = self._loop
        shutdown_event = self._shutdown_event
        if loop is not None and loop.is_running() and shutdown_event is not None:
            loop.call_soon_threadsafe(shutdown_event.set)

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._shutdown_event = asyncio.Event()
        provider = BrowserProvider(self._config)
        self._provider = provider
        try:
            await provider.start()
        except (BrowserAlreadyRunningError, BrowserLifecycleError) as error:
            self.status_changed.emit(str(error))
            self._provider = None
            return

        self.status_changed.emit(
            "Chrome is open. Sign in manually, then select Check Connection."
        )
        browser_closed = asyncio.create_task(provider.wait_until_closed())
        shutdown_requested = asyncio.create_task(self._shutdown_event.wait())
        try:
            done, pending = await asyncio.wait(
                (browser_closed, shutdown_requested),
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()
        finally:
            await provider.close()
            self._provider = None
            self._shutdown_event = None
            self.status_changed.emit("Browser closed.")

    async def _check_connection(self) -> SessionStatus:
        provider = self._provider
        if provider is None:
            return SessionStatus.BROWSER_UNAVAILABLE
        adapter = FlowAdapter(provider, SessionManager())
        return await adapter.check_session()

    def _report_connection_result(self, future: Future[SessionStatus]) -> None:
        try:
            status = future.result()
        except Exception:
            logger.exception("Flow connection check failed.")
            message = "Connection check failed safely. See the application log."
        else:
            message = {
                SessionStatus.AUTHENTICATED: (
                    "Connected: Flow dashboard access verified. "
                    "Media capabilities are not verified."
                ),
                SessionStatus.UNAUTHENTICATED: "Not signed in to Flow. Sign in manually.",
                SessionStatus.AUTH_REQUIRED: (
                    "Google sign-in is required. Complete it manually in Chrome."
                ),
                SessionStatus.UNKNOWN: (
                    "Browser is connected, but Flow workspace access could not "
                    "be verified. No automation job was submitted."
                ),
                SessionStatus.BROWSER_UNAVAILABLE: (
                    "Browser is unavailable. Open Chrome and try again."
                ),
            }[status]
        self.connection_checked.emit(message)
