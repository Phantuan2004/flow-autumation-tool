from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class BrowserIntegrationMode(StrEnum):
    CHROME_CDP = "chrome-cdp"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class BrowserCapabilities:
    mode: BrowserIntegrationMode
    browser_available: bool
    cdp_connection_available: bool
    explanation: str

    @classmethod
    def chrome_cdp_available(cls) -> BrowserCapabilities:
        return cls(
            mode=BrowserIntegrationMode.CHROME_CDP,
            browser_available=True,
            cdp_connection_available=True,
            explanation=(
                "A separately launched Chrome instance with an app-specific "
                "profile can be connected through loopback CDP."
            ),
        )

    @classmethod
    def unavailable(cls, explanation: str) -> BrowserCapabilities:
        return cls(
            mode=BrowserIntegrationMode.UNAVAILABLE,
            browser_available=False,
            cdp_connection_available=False,
            explanation=explanation,
        )
