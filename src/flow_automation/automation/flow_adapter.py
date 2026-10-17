from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from playwright.async_api import (
    Error as PlaywrightError,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

from flow_automation.automation.errors import (
    AuthenticationRequiredError,
    FlowStateUnknownError,
    FlowSubmissionNotSentError,
    UnsupportedOperationError,
)
from flow_automation.automation.flow_capabilities import FlowCapabilities
from flow_automation.automation.locators import (
    FLOW_DOWNLOAD_MENU_ITEM_NAME,
    FLOW_IMAGE_MORE_OPTIONS_BUTTON_NAME,
    FLOW_IMAGE_MODE_RADIO_NAME,
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
    baseline_loaded_image_count: int = 0
    completed: bool = False
    assets: tuple[FlowAsset, ...] = ()
    image_locators: tuple[Locator, ...] = ()


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
        logger.info("Returning Flow UI capabilities observed in the live image PoC.")
        return FlowCapabilities(
            image_generation=True,
            image_collection=True,
            asset_download=True,
            models=(FLOW_IMAGE_MODEL_NAME,),
            aspect_ratios=("16:9", "4:3", "1:1", "3:4", "9:16"),
            resolutions=("1K",),
            variant_counts=(1, 2, 3, 4),
            observations=(
                "One Nano Banana 2.1 x1 image was generated, observed, collected, "
                "and downloaded through the authenticated Flow project UI.",
                "The saved JPEG was verified as 1376x768 pixels.",
                "Video generation remains unsupported by this adapter.",
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
        try:
            await self.require_authenticated_session()
        except FlowStateUnknownError as error:
            raise FlowSubmissionNotSentError(
                "Flow session could not be verified; no image request was sent."
            ) from error
        if any(not state.completed for state in self._image_tasks.values()):
            raise FlowSubmissionNotSentError(
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
            raise FlowSubmissionNotSentError(
                "Flow image settings timed out before submission."
            ) from None
        except PlaywrightError:
            await self._browser.capture_failure_screenshot()
            raise FlowSubmissionNotSentError(
                "Flow image settings could not be inspected safely."
            ) from None

        try:
            await settings_button.click()
            image_mode = page.get_by_role(
                "radio",
                name=FLOW_IMAGE_MODE_RADIO_NAME,
                exact=True,
            )
            single_variant = page.get_by_role(
                "radio",
                name="x1",
                exact=True,
            )
            await image_mode.wait_for(state="visible")
            await single_variant.wait_for(state="visible")
            image_mode_ready = (
                await image_mode.count() == 1
                and await image_mode.is_visible()
                and await image_mode.is_checked()
            )
            single_variant_ready = (
                await single_variant.count() == 1
                and await single_variant.is_visible()
                and await single_variant.is_checked()
            )
            await page.keyboard.press("Escape")
            if not image_mode_ready or not single_variant_ready:
                raise UnsupportedOperationError(
                    "Flow is not configured for image mode with exactly one output."
                )

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
                raise FlowSubmissionNotSentError(
                    "The Flow prompt field is not empty; refusing to overwrite it."
                )

            baseline_tile_count = await page.locator(
                FLOW_IMAGE_TILE_SELECTOR
            ).count()
            baseline_loaded_image_count = await self._loaded_project_image_count(page)
            await prompt_box.click()
            await prompt_box.press_sequentially(request.prompt)
            entered_prompt = " ".join((await prompt_box.inner_text()).split())
            requested_prompt = " ".join(request.prompt.split())
            if entered_prompt != requested_prompt:
                await prompt_box.press("Control+A")
                await prompt_box.press("Backspace")
                raise FlowSubmissionNotSentError(
                    "The prompt field did not match the requested text; no job was sent."
                )
            try:
                await page.wait_for_function(
                    """name => {
                        const button = [...document.querySelectorAll('button')]
                            .find(item => item.getAttribute('aria-label') === name);
                        return button && !button.disabled &&
                            button.getAttribute('aria-disabled') !== 'true';
                    }""",
                    arg=FLOW_START_CREATE_BUTTON_NAME,
                    timeout=15_000,
                )
            except PlaywrightTimeoutError:
                await prompt_box.press("Control+A")
                await prompt_box.press("Backspace")
                raise FlowSubmissionNotSentError(
                    "Flow did not enable the submit control; no job was sent."
                ) from None

            task = FlowTask(reference=uuid4().hex)
            self._image_tasks[task.reference] = _ImageTaskState(
                baseline_tile_count=baseline_tile_count,
                baseline_loaded_image_count=baseline_loaded_image_count,
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
            raise FlowSubmissionNotSentError(
                "Flow image controls timed out before submission."
            ) from None
        except PlaywrightError:
            await self._browser.capture_failure_screenshot()
            raise FlowSubmissionNotSentError(
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
                    if (
                        tiles.length > baseline.tileCount &&
                        tiles.slice(baseline.tileCount).some(tile =>
                        [...tile.querySelectorAll('img')].some(image =>
                            image.complete && image.naturalWidth > 100
                        )
                        )
                    ) return true;
                    const viewport = document.querySelector('cdk-virtual-scroll-viewport');
                    if (!viewport) return false;
                    const images = [...viewport.querySelectorAll('img')].filter(image =>
                        image.complete && image.naturalWidth > 100
                    );
                    return images.length > baseline.imageCount;
                }""",
                arg={
                    "tileCount": state.baseline_tile_count,
                    "imageCount": state.baseline_loaded_image_count,
                },
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
            if new_tile_count == 1:
                tile = tiles.nth(state.baseline_tile_count)
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
                state.image_locators = (image,)
            elif new_tile_count == 0:
                image_locators = await self._new_loaded_project_images(
                    page,
                    state.baseline_loaded_image_count,
                )
                if len(image_locators) != 1:
                    raise FlowStateUnknownError(
                        "Expected exactly one new loaded project image for the x1 "
                        "request; result association is ambiguous."
                    )
                state.image_locators = tuple(image_locators)
            else:
                raise FlowStateUnknownError(
                    "Expected exactly one new image tile for the x1 request; "
                    "result association is ambiguous."
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
        task_reference, separator, asset_index = asset.reference.partition(":image:")
        state = self._image_tasks.get(task_reference)
        if (
            not separator
            or state is None
            or not state.completed
            or asset not in state.assets
            or not asset_index.isdecimal()
            or int(asset_index) >= len(state.image_locators)
        ):
            raise FlowStateUnknownError(
                "Image download requires the matching collected result from this "
                "adapter session."
            )
        if destination.exists():
            raise FileExistsError(
                "Refusing to overwrite an existing downloaded media file."
            )

        page = self._browser.page
        image = state.image_locators[int(asset_index)]
        tile = image.locator("xpath=ancestor::flow-image-tile[1]")
        try:
            await tile.hover()
            more_options = tile.get_by_role(
                "button",
                name=FLOW_IMAGE_MORE_OPTIONS_BUTTON_NAME,
                exact=True,
            )
            if await more_options.count() != 1:
                raise UnsupportedOperationError(
                    "The observed image options control is unavailable."
                )
            await more_options.click()
            download_menu_item = page.get_by_role(
                "menuitem",
                name=FLOW_DOWNLOAD_MENU_ITEM_NAME,
            )
            await download_menu_item.wait_for(state="visible")
            if await download_menu_item.count() != 1:
                raise UnsupportedOperationError(
                    "The observed image download menu item is unavailable."
                )
            await download_menu_item.click()
            original_size = page.get_by_role(
                "menuitem",
                name=re.compile(r"^1K(?:\s|$)"),
            )
            await original_size.wait_for(state="visible")
            if await original_size.count() != 1:
                raise UnsupportedOperationError(
                    "Flow did not expose one original 1K image download option."
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            async with page.expect_download(timeout=30_000) as download_info:
                await original_size.click()
            download = await download_info.value
            await download.save_as(destination)
        except (PlaywrightError, OSError):
            await self._browser.capture_failure_screenshot()
            raise FlowStateUnknownError(
                "The Flow image download did not complete safely."
            ) from None
        finally:
            if not page.is_closed():
                await page.keyboard.press("Escape")

        logger.info("Downloaded one original-size Flow image through the UI.")
        return destination

    @staticmethod
    async def _loaded_project_image_count(page: Page) -> int:
        images = page.locator("cdk-virtual-scroll-viewport img")
        loaded_count = 0
        for index in range(await images.count()):
            state = await images.nth(index).evaluate(
                "element => ({complete: element.complete, width: element.naturalWidth})"
            )
            if state["complete"] and state["width"] > 100:
                loaded_count += 1
        return loaded_count

    @staticmethod
    async def _new_loaded_project_images(
        page: Page,
        baseline_count: int,
    ) -> list[Locator]:
        images = page.locator("cdk-virtual-scroll-viewport img")
        loaded_images: list[Locator] = []
        for index in range(await images.count()):
            image = images.nth(index)
            state = await image.evaluate(
                "element => ({complete: element.complete, width: element.naturalWidth})"
            )
            if state["complete"] and state["width"] > 100:
                loaded_images.append(image)
        return loaded_images[baseline_count:]
