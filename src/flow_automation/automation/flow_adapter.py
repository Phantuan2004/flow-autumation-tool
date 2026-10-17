from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from playwright.async_api import (
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

from flow_automation.automation.errors import (
    AuthenticationRequiredError,
    FlowStateUnknownError,
    UnsupportedOperationError,
)
from flow_automation.automation.flow_capabilities import FlowCapabilities
from flow_automation.automation.locators import (
    FLOW_IMAGE_MODEL_NAME,
    FLOW_IMAGE_SETTINGS_BUTTON_NAME,
    FLOW_IMAGE_TILE_SELECTOR,
    FLOW_PROMPT_TEXTBOX_NAME,
    FLOW_START_CREATE_BUTTON_NAME,
)
from flow_automation.automation.session_manager import (
    SessionManager,
    SessionStatus,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ImageRequest:
    prompt: str


@dataclass(frozen=True, slots=True)
class VideoRequest:
    prompt: str
    image: FlowAsset


@dataclass(frozen=True, slots=True)
class FlowTask:
    reference: str


@dataclass(frozen=True, slots=True)
class FlowAsset:
    reference: str
    filename: str


@dataclass(slots=True)
class _ImageTaskState:
    baseline_tile_count: int
    completed: bool = False
    assets: tuple[FlowAsset, ...] = ()


class BrowserPageProvider(Protocol):
    @property
    def page(self) -> Page:
        """Return the managed page or raise when Chromium is not running."""

        ...

    async def capture_failure_screenshot(self) -> None:
        """Capture a redacted diagnostic image when a browser error occurs."""

        ...


class FlowAdapter:
    """Typed UI-only Flow contract; unverified mutations fail explicitly."""

    def __init__(
        self,
        browser: BrowserPageProvider,
        session_manager: SessionManager,
    ) -> None:
        self._browser = browser
        self._session_manager = session_manager
        self._image_tasks: dict[str, _ImageTaskState] = {}

    async def check_session(self) -> SessionStatus:
        try:
            page = self._browser.page
        except RuntimeError:
            logger.info("Flow browser is unavailable.")
            return SessionStatus.BROWSER_UNAVAILABLE
        try:
            status = await self._session_manager.check_session(page)
        except FlowStateUnknownError:
            await self._browser.capture_failure_screenshot()
            raise
        if status is SessionStatus.UNAUTHENTICATED:
            logger.warning("Manual Flow sign-in is required.")
        if status is SessionStatus.AUTH_REQUIRED:
            logger.warning("Google authentication is required in the browser.")
        if status is SessionStatus.UNKNOWN:
            logger.warning("Flow session state is unknown; operations must stop.")
        return status

    async def require_authenticated_session(self) -> None:
        status = await self.check_session()
        if status is SessionStatus.UNAUTHENTICATED:
            raise AuthenticationRequiredError(
                "Sign in manually in the app's isolated Chrome window."
            )
        if status is SessionStatus.AUTH_REQUIRED:
            raise AuthenticationRequiredError(
                "Complete Google sign-in manually in the app's isolated Chrome window."
            )
        if status is SessionStatus.UNKNOWN:
            raise FlowStateUnknownError(
                "The UI does not expose a verified authenticated-session signal."
            )
        if status is SessionStatus.BROWSER_UNAVAILABLE:
            raise FlowStateUnknownError(
                "The Chrome browser is unavailable; open it before continuing."
            )

    async def get_capabilities(self) -> FlowCapabilities:
        logger.info("Returning partially verified Flow UI capabilities.")
        return FlowCapabilities(
            image_generation=True,
            image_collection=True,
            models=(FLOW_IMAGE_MODEL_NAME,),
            aspect_ratios=("16:9", "4:3", "1:1", "3:4", "9:16"),
            variant_counts=(1, 2, 3, 4),
            observations=(
                "One Nano Banana 2.1 image was generated and displayed in the "
                "authenticated Flow project UI.",
                "One image was downloaded through the UI's 1K original-size "
                "option; the saved JPEG measured 1376x768 pixels.",
                "Download is not yet implemented by this adapter; video remains "
                "unverified.",
            ),
        )

    async def open_project(self, project_name: str) -> None:
        del project_name
        logger.warning("Flow project navigation is not verified; no UI action was taken.")
        raise UnsupportedOperationError(
            "Project navigation has not been verified against an authenticated Flow workspace."
        )

    async def create_image(self, request: ImageRequest) -> FlowTask:
        if not request.prompt.strip():
            raise ValueError("Image prompt must not be empty.")
        await self.require_authenticated_session()
        if any(not state.completed for state in self._image_tasks.values()):
            raise FlowStateUnknownError(
                "An earlier image task has not been reconciled; no new task was sent."
            )

        page = self._browser.page
        try:
            settings_button = page.get_by_role(
                "button",
                name=FLOW_IMAGE_SETTINGS_BUTTON_NAME,
                exact=True,
            )
            if (
                await settings_button.count() != 1
                or not await settings_button.is_visible()
            ):
                raise UnsupportedOperationError(
                    "The verified Flow image settings control is unavailable."
                )

            settings_label = " ".join((await settings_button.inner_text()).split())
            if (
                FLOW_IMAGE_MODEL_NAME not in settings_label
                or "x1" not in settings_label
            ):
                raise UnsupportedOperationError(
                    "Select the verified Nano Banana image model and x1 output before "
                    "submitting a single-image test."
                )
        except PlaywrightTimeoutError:
            await self._browser.capture_failure_screenshot()
            raise FlowStateUnknownError(
                "Flow image settings timed out before submission."
            ) from None
        except PlaywrightError:
            await self._browser.capture_failure_screenshot()
            raise FlowStateUnknownError(
                "Flow image settings could not be inspected safely."
            ) from None

        try:
            await settings_button.click()
            radios = page.get_by_role("radio")
            image_modes = []
            single_variant_options = []
            for index in range(await radios.count()):
                radio = radios.nth(index)
                if not await radio.is_visible():
                    continue
                label = " ".join((await radio.inner_text()).split())
                if label.casefold().startswith("image "):
                    image_modes.append(radio)
                if label == "x1":
                    single_variant_options.append(radio)
            if (
                len(image_modes) != 1
                or not await image_modes[0].is_checked()
                or len(single_variant_options) != 1
                or not await single_variant_options[0].is_checked()
            ):
                await page.keyboard.press("Escape")
                raise UnsupportedOperationError(
                    "Flow is not configured for image mode with exactly one output."
                )
            await page.keyboard.press("Escape")

            prompt_box = page.get_by_role(
                "textbox",
                name=FLOW_PROMPT_TEXTBOX_NAME,
                exact=True,
            )
            start_button = page.get_by_role(
                "button",
                name=FLOW_START_CREATE_BUTTON_NAME,
                exact=True,
            )
            if (
                await prompt_box.count() != 1
                or not await prompt_box.is_visible()
                or await start_button.count() != 1
                or not await start_button.is_visible()
            ):
                raise UnsupportedOperationError(
                    "The verified Flow image prompt controls are unavailable."
                )
            if (await prompt_box.inner_text()).strip():
                raise FlowStateUnknownError(
                    "The Flow prompt field is not empty; refusing to overwrite it."
                )

            baseline_tile_count = await page.locator(
                FLOW_IMAGE_TILE_SELECTOR
            ).count()
            await prompt_box.click()
            await prompt_box.press_sequentially(request.prompt)
            entered_prompt = " ".join((await prompt_box.inner_text()).split())
            requested_prompt = " ".join(request.prompt.split())
            if entered_prompt != requested_prompt:
                await prompt_box.press("Control+A")
                await prompt_box.press("Backspace")
                raise FlowStateUnknownError(
                    "The prompt field did not match the requested text; no job was sent."
                )
            if not await start_button.is_enabled():
                await prompt_box.press("Control+A")
                await prompt_box.press("Backspace")
                raise FlowStateUnknownError(
                    "Flow did not enable the submit control; no job was sent."
                )

            task = FlowTask(reference=uuid4().hex)
            self._image_tasks[task.reference] = _ImageTaskState(
                baseline_tile_count=baseline_tile_count,
            )
            try:
                await start_button.click()
            except PlaywrightTimeoutError:
                await self._browser.capture_failure_screenshot()
                logger.error(
                    "Image submission state is unknown; the task will not be retried."
                )
                raise FlowStateUnknownError(
                    "Flow may have received the image request. Inspect the project "
                    "before attempting another submission."
                ) from None
            except PlaywrightError:
                await self._browser.capture_failure_screenshot()
                logger.error(
                    "Image submission state is unknown; the task will not be retried."
                )
                raise FlowStateUnknownError(
                    "Flow submission could not be confirmed. Inspect the project "
                    "before attempting another submission."
                ) from None
        except PlaywrightTimeoutError:
            await self._browser.capture_failure_screenshot()
            raise FlowStateUnknownError(
                "Flow image controls timed out before submission."
            ) from None
        except PlaywrightError:
            await self._browser.capture_failure_screenshot()
            raise FlowStateUnknownError(
                "Flow image controls could not be inspected safely."
            ) from None

        logger.info("One Flow image request was submitted without automatic retry.")
        return task

    async def wait_for_result(
        self,
        task: FlowTask,
        timeout: float,
    ) -> FlowTask:
        if timeout <= 0:
            raise ValueError("Result wait timeout must be greater than zero.")
        state = self._image_tasks.get(task.reference)
        if state is None:
            raise FlowStateUnknownError(
                "This image task is not tracked by the current adapter session."
            )
        if state.completed:
            return task
        await self.require_authenticated_session()
        page = self._browser.page
        try:
            await page.wait_for_function(
                """baseline => {
                    const tiles = [...document.querySelectorAll('flow-image-tile')];
                    if (tiles.length <= baseline) return false;
                    return tiles.slice(baseline).some(tile =>
                        [...tile.querySelectorAll('img')].some(image =>
                            image.complete && image.naturalWidth > 100
                        )
                    );
                }""",
                arg=state.baseline_tile_count,
                timeout=int(timeout * 1000),
            )
        except PlaywrightTimeoutError:
            await self._browser.capture_failure_screenshot()
            raise FlowStateUnknownError(
                "Flow did not expose a completed image before the timeout. "
                "Inspect the project before retrying."
            ) from None
        except PlaywrightError:
            await self._browser.capture_failure_screenshot()
            raise FlowStateUnknownError(
                "The Flow result state could not be inspected safely."
            ) from None
        state.completed = True
        logger.info("A completed Flow image result was observed.")
        return task

    async def collect_images(self, task: FlowTask) -> list[FlowAsset]:
        state = self._image_tasks.get(task.reference)
        if state is None or not state.completed:
            raise FlowStateUnknownError(
                "Image result collection requires a completed task from this adapter."
            )
        if state.assets:
            return list(state.assets)
        page = self._browser.page
        try:
            tiles = page.locator(FLOW_IMAGE_TILE_SELECTOR)
            tile_count = await tiles.count()
            new_tile_count = tile_count - state.baseline_tile_count
            if new_tile_count != 1:
                raise FlowStateUnknownError(
                    "Expected exactly one new image tile for the x1 request; "
                    "result association is ambiguous."
                )
            tile_index = state.baseline_tile_count
            tile = tiles.nth(tile_index)
            image = tile.locator("img").first
            if await image.count() != 1:
                raise FlowStateUnknownError(
                    "The new Flow image tile did not expose one collectable image."
                )
            image_state = await image.evaluate(
                "element => ({complete: element.complete, width: element.naturalWidth})"
            )
            if not image_state["complete"] or image_state["width"] <= 100:
                raise FlowStateUnknownError(
                    "The Flow image tile is not fully loaded; collection stopped safely."
                )
        except PlaywrightError:
            await self._browser.capture_failure_screenshot()
            raise FlowStateUnknownError(
                "The collected image could not be inspected safely."
            ) from None
        asset = FlowAsset(
            reference=f"{task.reference}:image:0",
            filename=f"flow-image-{task.reference[:8]}",
        )
        state.assets = (asset,)
        logger.info("Collected one loaded image result from the Flow project UI.")
        return [asset]

    async def create_video(self, request: VideoRequest) -> FlowTask:
        del request
        logger.warning("Flow video creation is not verified; no task was submitted.")
        raise UnsupportedOperationError(
            "Video creation has not been verified against the Flow UI."
        )

    async def download_asset(
        self,
        asset: FlowAsset,
        destination: Path,
    ) -> Path:
        del asset, destination
        logger.warning(
            "A one-off Flow image download was verified, but the adapter's "
            "download operation is not implemented."
        )
        raise UnsupportedOperationError(
            "Flow image download is not yet implemented by the adapter. A live "
            "one-off download succeeded via the 1K original-size UI option."
        )
