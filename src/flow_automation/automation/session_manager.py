from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from enum import StrEnum
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError, Page

from flow_automation.automation.errors import FlowStateUnknownError
from flow_automation.automation.locators import (
    FLOW_NEW_PROJECT_BUTTON_NAME,
    FLOW_IMAGE_SETTINGS_BUTTON_NAME,
    FLOW_PROMPT_TEXTBOX_NAME,
    FLOW_START_CREATE_BUTTON_NAME,
    FLOW_SIGN_IN_LINK_NAME,
    GOOGLE_SIGN_IN_URL_PREFIX,
)

logger = logging.getLogger(__name__)


class SessionStatus(StrEnum):
    AUTHENTICATED = "AUTHENTICATED"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    UNKNOWN = "UNKNOWN"
    BROWSER_UNAVAILABLE = "BROWSER_UNAVAILABLE"


AuthenticatedSignal = Callable[[Page], Awaitable[bool]]


def classify_session_signals(
    *,
    authenticated_visible: bool,
    sign_in_visible: bool,
    auth_required_visible: bool = False,
    browser_available: bool = True,
) -> SessionStatus:
    if not browser_available:
        return SessionStatus.BROWSER_UNAVAILABLE
    if auth_required_visible:
        return SessionStatus.AUTH_REQUIRED
    if authenticated_visible and not sign_in_visible:
        return SessionStatus.AUTHENTICATED
    if sign_in_visible:
        return SessionStatus.UNAUTHENTICATED
    return SessionStatus.UNKNOWN


class SessionManager:
    """Classifies only verified UI signals; absence of a login prompt is UNKNOWN."""

    def __init__(
        self,
        authenticated_signal: AuthenticatedSignal | None = None,
    ) -> None:
        self._authenticated_signal = authenticated_signal

    async def check_session(self, page: Page) -> SessionStatus:
        if page.is_closed():
            logger.info("Flow browser is unavailable because the page is closed.")
            return SessionStatus.BROWSER_UNAVAILABLE

        try:
            page_host = (urlsplit(page.url).hostname or "").casefold()
            sign_in_links = page.get_by_role(
                "link",
                name=FLOW_SIGN_IN_LINK_NAME,
                exact=True,
            )
            sign_in_visible = False
            for index in range(await sign_in_links.count()):
                candidate = sign_in_links.nth(index)
                if not await candidate.is_visible():
                    continue
                href = await candidate.get_attribute("href")
                if href is not None and href.startswith(GOOGLE_SIGN_IN_URL_PREFIX):
                    sign_in_visible = True
                    break
            authenticated_visible = False
            if self._authenticated_signal is not None:
                authenticated_visible = await self._authenticated_signal(page)
            else:
                authenticated_visible = await self._flow_workspace_is_available(page)
        except PlaywrightError:
            logger.warning("Flow session signals could not be inspected.")
            raise FlowStateUnknownError(
                "Flow session state could not be inspected safely."
            ) from None

        status = classify_session_signals(
            authenticated_visible=authenticated_visible,
            sign_in_visible=sign_in_visible,
            auth_required_visible=page_host == "accounts.google.com",
        )
        logger.info("Flow session state classified as %s.", status.value)
        return status

    @staticmethod
    async def _flow_workspace_is_available(page: Page) -> bool:
        """Check for the observed dashboard or project-editor UI."""
        dashboard_action = page.get_by_role(
            "button",
            name=re.compile(
                re.escape(FLOW_NEW_PROJECT_BUTTON_NAME),
                re.IGNORECASE,
            ),
        )
        for index in range(await dashboard_action.count()):
            if await dashboard_action.nth(index).is_visible():
                return True

        prompt = page.get_by_role(
            "textbox",
            name=FLOW_PROMPT_TEXTBOX_NAME,
            exact=True,
        )
        start_button = page.get_by_role(
            "button",
            name=FLOW_START_CREATE_BUTTON_NAME,
            exact=True,
        )
        image_settings = page.get_by_role(
            "button",
            name=FLOW_IMAGE_SETTINGS_BUTTON_NAME,
            exact=True,
        )
        return (
            await prompt.count() == 1
            and await prompt.is_visible()
            and await start_button.count() == 1
            and await start_button.is_visible()
            and await image_settings.count() == 1
            and await image_settings.is_visible()
        )
