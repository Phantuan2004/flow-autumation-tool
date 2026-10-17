from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from playwright.async_api import Page

_REDACT_PAGE_STYLE = """
*, *::before, *::after {
  color: transparent !important;
  -webkit-text-fill-color: transparent !important;
  text-shadow: none !important;
  background-image: none !important;
  caret-color: transparent !important;
}
img, picture, video, canvas, svg, input, textarea, select, iframe, object, embed,
[contenteditable="true"] {
  visibility: hidden !important;
}
"""


async def capture_failure_screenshot(page: Page, destination: Path) -> Path:
    """Save a layout-only screenshot with visible text and media redacted."""
    destination.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = destination / f"flow-error-{timestamp}-{uuid4().hex[:8]}.png"
    await page.screenshot(
        path=str(path),
        full_page=True,
        style=_REDACT_PAGE_STYLE,
    )
    return path
