from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

import pytest
from playwright.async_api import Error as PlaywrightError, Page

from flow_automation.automation.browser_capabilities import (
    BrowserIntegrationMode,
)
from flow_automation.automation.browser_manager import (
    BrowserConfig,
    _ProfileLock,
)
from flow_automation.automation.browser_provider import BrowserProvider
from flow_automation.automation.errors import (
    AuthenticationRequiredError,
    BrowserAlreadyRunningError,
    BrowserLifecycleError,
    FlowStateUnknownError,
    UnsupportedOperationError,
)
from flow_automation.automation.flow_adapter import (
    FlowAdapter,
    FlowAsset,
    FlowTask,
    ImageRequest,
    VideoRequest,
)
from flow_automation.automation.flow_capabilities import FlowCapabilities
from flow_automation.automation.locators import (
    FLOW_IMAGE_SETTINGS_BUTTON_NAME,
    FLOW_NEW_PROJECT_BUTTON_NAME,
    FLOW_PROMPT_TEXTBOX_NAME,
    FLOW_START_CREATE_BUTTON_NAME,
)
from flow_automation.automation.session_manager import (
    SessionManager,
    SessionStatus,
    classify_session_signals,
)
from flow_automation.diagnostics.screenshots import capture_failure_screenshot


class FakeLocator:
    def __init__(
        self,
        *,
        visible: bool = False,
        href: str | None = None,
        text: str = "",
        checked: bool = False,
        enabled: bool = True,
        count: int = 1,
        members: list[FakeLocator] | None = None,
        attributes: dict[str, str] | None = None,
        image_state: dict[str, int | bool] | None = None,
        fail_click: bool = False,
    ) -> None:
        self.first = self
        self._visible = visible
        self._href = href
        self._text = text
        self._checked = checked
        self._enabled = enabled
        self._count = count
        self._members = members
        self._attributes = attributes or {}
        self._image_state = image_state
        self._fail_click = fail_click
        self._type_callback: Callable[[], None] | None = None
        self.click_count = 0

    async def is_visible(self) -> bool:
        return self._visible

    async def get_attribute(self, name: str) -> str | None:
        if name == "href":
            return self._href
        return self._attributes.get(name)

    async def inner_text(self) -> str:
        return self._text

    async def count(self) -> int:
        return len(self._members) if self._members is not None else self._count

    def nth(self, index: int) -> FakeLocator:
        if self._members is not None:
            return self._members[index]
        return self

    async def is_checked(self) -> bool:
        return self._checked

    async def is_enabled(self) -> bool:
        return self._enabled

    async def click(self) -> None:
        self.click_count += 1
        if self._fail_click:
            raise PlaywrightError("click failed")

    async def press_sequentially(self, text: str) -> None:
        self._text += text
        if self._type_callback is not None:
            self._type_callback()

    async def press(self, key: str) -> None:
        if key == "Backspace":
            self._text = ""

    def locator(self, selector: str) -> FakeLocator:
        del selector
        return FakeLocator(
            visible=True,
            count=1,
            image_state={"complete": True, "width": 1376},
        )

    async def evaluate(self, expression: str) -> dict[str, int | bool]:
        del expression
        return self._image_state or {"complete": True, "width": 1376}


class FailingLocator(FakeLocator):
    async def is_visible(self) -> bool:
        raise PlaywrightError("locator failed")


class FakeKeyboard:
    async def press(self, key: str) -> None:
        del key


class FakePage:
    def __init__(
        self,
        sign_in: FakeLocator,
        *,
        workspace: FakeLocator | None = None,
        project_editor: bool = False,
        closed: bool = False,
        url: str = "https://flow.google.com/",
    ) -> None:
        self._sign_in = sign_in
        self._workspace = workspace or FakeLocator()
        self._project_editor = (
            FakeLocator(visible=True) if project_editor else FakeLocator()
        )
        self._closed = closed
        self.url = url
        self.keyboard = FakeKeyboard()

    def is_closed(self) -> bool:
        return self._closed

    def get_by_role(
        self,
        role: str,
        *,
        name: str | re.Pattern[str] | None = None,
        exact: bool = False,
    ) -> FakeLocator:
        del exact
        matches_dashboard = (
            name.search(FLOW_NEW_PROJECT_BUTTON_NAME) is not None
            if isinstance(name, re.Pattern)
            else isinstance(name, str) and FLOW_NEW_PROJECT_BUTTON_NAME in name
        )
        if role == "link":
            return self._sign_in
        if role == "textbox" and name == FLOW_PROMPT_TEXTBOX_NAME:
            return self._project_editor
        if role == "button":
            if matches_dashboard:
                return self._workspace
            if name in {
                FLOW_START_CREATE_BUTTON_NAME,
                FLOW_IMAGE_SETTINGS_BUTTON_NAME,
            }:
                return self._project_editor
        return FakeLocator()

    def locator(self, selector: str) -> FakeLocator:
        del selector
        return FakeLocator(count=0)


class FakeImagePage(FakePage):
    def __init__(self) -> None:
        super().__init__(FakeLocator())
        self.prompt = FakeLocator(
            visible=True,
            text="",
            attributes={
                "aria-label": FLOW_PROMPT_TEXTBOX_NAME,
                "contenteditable": "true",
            },
        )
        self.settings = FakeLocator(
            visible=True,
            text="Nano Banana 2.1 16:9 x1",
        )
        self.start = FakeLocator(visible=True, enabled=False)
        self.start._fail_click = False
        self.image_mode = FakeLocator(
            visible=True,
            text="image Hình ảnh",
            checked=True,
        )
        self.single_variant = FakeLocator(
            visible=True,
            text="x1",
            checked=True,
        )
        self.radios = FakeLocator(
            members=[self.image_mode, self.single_variant],
        )
        self.image_tile_count = 0
        self.prompt._type_callback = lambda: setattr(self.start, "_enabled", True)

    def get_by_role(
        self,
        role: str,
        *,
        name: str | re.Pattern[str] | None = None,
        exact: bool = False,
    ) -> FakeLocator:
        if role == "radio":
            return self.radios
        if role == "textbox" and name == FLOW_PROMPT_TEXTBOX_NAME:
            return self.prompt
        if role == "button":
            if name == FLOW_IMAGE_SETTINGS_BUTTON_NAME:
                return self.settings
            if name == FLOW_START_CREATE_BUTTON_NAME:
                return self.start
        return super().get_by_role(role, name=name, exact=exact)

    def locator(self, selector: str) -> FakeLocator:
        if selector == "flow-image-tile":
            if self.image_tile_count == 0:
                return FakeLocator(count=0)
            tile = FakeLocator()
            return FakeLocator(members=[tile])
        return super().locator(selector)

    async def wait_for_function(
        self,
        expression: str,
        *,
        arg: int,
        timeout: int,
    ) -> None:
        del expression, timeout
        assert arg == 0
        self.image_tile_count = 1


class FakeScreenshotPage:
    def __init__(self) -> None:
        self.style = ""

    async def screenshot(
        self,
        *,
        path: str,
        full_page: bool,
        style: str,
    ) -> None:
        del full_page
        self.style = style
        Path(path).write_bytes(b"test screenshot")


class FakeBrowser:
    def __init__(self, page: FakePage | None) -> None:
        self._page = cast(Page, page) if page is not None else None
        self.screenshot_count = 0

    @property
    def page(self) -> Page:
        if self._page is None:
            raise BrowserLifecycleError("browser closed")
        return self._page

    async def capture_failure_screenshot(self) -> None:
        self.screenshot_count += 1


def as_page(page: FakePage) -> Page:
    return cast(Page, page)


def test_session_signal_classifier_handles_all_supported_states() -> None:
    assert classify_session_signals(
        authenticated_visible=True,
        sign_in_visible=False,
    ) is SessionStatus.AUTHENTICATED
    assert classify_session_signals(
        authenticated_visible=False,
        sign_in_visible=True,
    ) is SessionStatus.UNAUTHENTICATED
    assert classify_session_signals(
        authenticated_visible=False,
        sign_in_visible=False,
    ) is SessionStatus.UNKNOWN
    assert classify_session_signals(
        authenticated_visible=False,
        sign_in_visible=False,
        auth_required_visible=True,
    ) is SessionStatus.AUTH_REQUIRED
    assert classify_session_signals(
        authenticated_visible=False,
        sign_in_visible=False,
        browser_available=False,
    ) is SessionStatus.BROWSER_UNAVAILABLE


def test_conflicting_session_signals_fail_closed() -> None:
    assert classify_session_signals(
        authenticated_visible=True,
        sign_in_visible=True,
    ) is SessionStatus.UNAUTHENTICATED


def test_unverified_page_is_unknown_and_observed_sign_in_is_unauthenticated() -> None:
    async def run() -> tuple[SessionStatus, SessionStatus]:
        manager = SessionManager()
        unknown = await manager.check_session(as_page(FakePage(FakeLocator())))
        unauthenticated = await manager.check_session(
            as_page(
                FakePage(
                    FakeLocator(
                        visible=True,
                        href="https://accounts.google.com/ServiceLogin",
                        text="Sign in",
                    )
                )
            )
        )
        return unknown, unauthenticated

    assert asyncio.run(run()) == (
        SessionStatus.UNKNOWN,
        SessionStatus.UNAUTHENTICATED,
    )


def test_flow_dashboard_action_verifies_workspace_access() -> None:
    async def run() -> SessionStatus:
        page = FakePage(
            FakeLocator(),
            workspace=FakeLocator(visible=True),
        )
        return await SessionManager().check_session(as_page(page))

    assert asyncio.run(run()) is SessionStatus.AUTHENTICATED


def test_flow_project_editor_signals_verify_workspace_access() -> None:
    async def run() -> SessionStatus:
        page = FakePage(FakeLocator(), project_editor=True)
        return await SessionManager().check_session(as_page(page))

    assert asyncio.run(run()) is SessionStatus.AUTHENTICATED


def test_flow_workspace_detector_requires_visible_dashboard_action() -> None:
    async def run() -> SessionStatus:
        hidden_workspace = FakePage(
            FakeLocator(),
            workspace=FakeLocator(visible=False),
        )
        return await SessionManager().check_session(as_page(hidden_workspace))

    assert asyncio.run(run()) is SessionStatus.UNKNOWN


def test_sign_in_signal_overrides_dashboard_action() -> None:
    async def run() -> SessionStatus:
        page = FakePage(
            FakeLocator(
                visible=True,
                href="https://accounts.google.com/ServiceLogin",
                text="Sign in",
            ),
            workspace=FakeLocator(visible=True),
        )
        return await SessionManager().check_session(as_page(page))

    assert asyncio.run(run()) is SessionStatus.UNAUTHENTICATED


def test_closed_page_reports_browser_unavailable() -> None:
    async def run() -> SessionStatus:
        return await SessionManager().check_session(
            as_page(FakePage(FakeLocator(), closed=True))
        )

    assert asyncio.run(run()) is SessionStatus.BROWSER_UNAVAILABLE


def test_google_auth_page_requires_manual_authentication() -> None:
    async def run() -> SessionStatus:
        page = FakePage(
            FakeLocator(),
            url="https://accounts.google.com/signin/",
        )
        return await SessionManager().check_session(as_page(page))

    assert asyncio.run(run()) is SessionStatus.AUTH_REQUIRED


def test_capabilities_default_to_unverified_and_empty() -> None:
    capabilities = FlowCapabilities.unverified("workspace not inspected")

    assert not capabilities.verified
    assert not capabilities.image_generation
    assert not capabilities.image_collection
    assert not capabilities.video_generation
    assert not capabilities.asset_download
    assert capabilities.models == ()
    assert capabilities.aspect_ratios == ()
    assert capabilities.resolutions == ()
    assert capabilities.variant_counts == ()
    assert capabilities.max_concurrency is None
    assert capabilities.observations == ("workspace not inspected",)


def test_adapter_capabilities_do_not_claim_unobserved_features() -> None:
    async def run() -> FlowCapabilities:
        adapter = FlowAdapter(FakeBrowser(None), SessionManager())
        return await adapter.get_capabilities()

    capabilities = asyncio.run(run())

    assert not capabilities.verified
    assert capabilities.image_generation
    assert capabilities.image_collection
    assert not capabilities.video_generation
    assert not capabilities.asset_download
    assert not capabilities.verified
    assert capabilities.models == ("Nano Banana 2.1",)


def test_image_task_is_submitted_once_and_result_collection_is_idempotent() -> None:
    async def run() -> tuple[FakeImagePage, FlowTask, list[FlowAsset]]:
        page = FakeImagePage()
        adapter = FlowAdapter(FakeBrowser(page), SessionManager())
        task = await adapter.create_image(ImageRequest("A single test image"))

        with pytest.raises(FlowStateUnknownError, match="not been reconciled"):
            await adapter.create_image(ImageRequest("Do not submit this"))
        assert page.start.click_count == 1

        await adapter.wait_for_result(task, timeout=1)
        assets = await adapter.collect_images(task)
        assert await adapter.collect_images(task) == assets
        return page, task, assets

    page, task, assets = asyncio.run(run())

    assert page.start.click_count == 1
    assert len(assets) == 1
    assert assets[0].reference == f"{task.reference}:image:0"
    assert assets[0].filename.startswith("flow-image-")


def test_uncertain_image_submission_stays_pending_and_cannot_be_retried() -> None:
    async def run() -> tuple[FakeImagePage, FakeBrowser]:
        page = FakeImagePage()
        page.start._fail_click = True
        browser = FakeBrowser(page)
        adapter = FlowAdapter(browser, SessionManager())

        with pytest.raises(FlowStateUnknownError, match="could not be confirmed"):
            await adapter.create_image(ImageRequest("A single test image"))
        with pytest.raises(FlowStateUnknownError, match="not been reconciled"):
            await adapter.create_image(ImageRequest("Do not submit this"))
        return page, browser

    page, browser = asyncio.run(run())

    assert page.start.click_count == 1
    assert browser.screenshot_count == 1


def test_adapter_reports_session_state_without_claiming_access() -> None:
    async def run() -> None:
        adapter = FlowAdapter(
            FakeBrowser(FakePage(FakeLocator())),
            SessionManager(),
        )
        assert await adapter.check_session() is SessionStatus.UNKNOWN
        with pytest.raises(FlowStateUnknownError):
            await adapter.require_authenticated_session()

    asyncio.run(run())


def test_adapter_requires_manual_sign_in_when_login_signal_is_visible() -> None:
    async def run() -> None:
        page = FakePage(
            FakeLocator(
                visible=True,
                href="https://accounts.google.com/ServiceLogin",
                text="Sign in",
            )
        )
        adapter = FlowAdapter(
            FakeBrowser(page),
            SessionManager(),
        )
        assert await adapter.check_session() is SessionStatus.UNAUTHENTICATED
        with pytest.raises(AuthenticationRequiredError):
            await adapter.require_authenticated_session()

    asyncio.run(run())


def test_adapter_requires_manual_google_authentication() -> None:
    async def run() -> None:
        page = FakePage(
            FakeLocator(),
            url="https://accounts.google.com/signin/",
        )
        adapter = FlowAdapter(FakeBrowser(page), SessionManager())

        assert await adapter.check_session() is SessionStatus.AUTH_REQUIRED
        with pytest.raises(AuthenticationRequiredError):
            await adapter.require_authenticated_session()

    asyncio.run(run())


def test_adapter_returns_authenticated_only_from_explicit_detector() -> None:
    async def detect_authenticated_session(page: Page) -> bool:
        del page
        return True

    async def run() -> SessionStatus:
        adapter = FlowAdapter(
            FakeBrowser(FakePage(FakeLocator())),
            SessionManager(detect_authenticated_session),
        )
        return await adapter.check_session()

    assert asyncio.run(run()) is SessionStatus.AUTHENTICATED


def test_adapter_reports_closed_browser_as_unknown() -> None:
    async def run() -> None:
        adapter = FlowAdapter(FakeBrowser(None), SessionManager())
        assert await adapter.check_session() is SessionStatus.BROWSER_UNAVAILABLE
        with pytest.raises(FlowStateUnknownError):
            await adapter.require_authenticated_session()

    asyncio.run(run())


def test_session_inspection_error_is_structured_and_captures_diagnostics() -> None:
    async def run() -> None:
        browser = FakeBrowser(FakePage(FailingLocator()))
        adapter = FlowAdapter(browser, SessionManager())

        with pytest.raises(FlowStateUnknownError):
            await adapter.check_session()
        assert browser.screenshot_count == 1

    asyncio.run(run())


def test_unverified_operations_raise_structured_errors() -> None:
    async def run_operation(
        operation: Callable[[], Awaitable[object]],
    ) -> None:
        with pytest.raises(UnsupportedOperationError):
            await operation()

    async def run_unavailable_operation(
        operation: Callable[[], Awaitable[object]],
    ) -> None:
        with pytest.raises(FlowStateUnknownError):
            await operation()

    adapter = FlowAdapter(FakeBrowser(None), SessionManager())
    unsupported_operations = (
        lambda: adapter.open_project("sample"),
        lambda: adapter.create_video(
            VideoRequest("prompt", FlowAsset("asset", "image.png"))
        ),
        lambda: adapter.download_asset(
            FlowAsset("asset", "image.png"),
            Path("image.png"),
        ),
    )
    for operation in unsupported_operations:
        asyncio.run(run_operation(operation))
    unavailable_operations = (
        lambda: adapter.create_image(ImageRequest("prompt")),
        lambda: adapter.wait_for_result(FlowTask("task"), timeout=1),
        lambda: adapter.collect_images(FlowTask("task")),
    )
    for operation in unavailable_operations:
        asyncio.run(run_unavailable_operation(operation))


def test_browser_config_validates_url_and_timeout(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="HTTP\\(S\\)"):
        BrowserConfig(
            "not-a-url",
            tmp_path / "profile",
            tmp_path / "diagnostics",
            tmp_path / "logs",
        )
    with pytest.raises(ValueError, match="timeout"):
        BrowserConfig(
            "https://flow.google.com/",
            tmp_path / "profile",
            tmp_path / "diagnostics",
            tmp_path / "logs",
            default_timeout_ms=0,
        )


def test_browser_config_loads_project_url_and_runtime_paths() -> None:
    project_root = Path(__file__).resolve().parents[2]
    config = BrowserConfig.from_toml(
        project_root / "config" / "defaults.toml",
        project_root=project_root,
    )

    assert config.flow_url == "https://labs.google/fx/tools/flow"
    assert (
        config.profile_path
        == project_root / "runtime" / "browser-profile-manual-chrome"
    )
    assert config.diagnostics_path == project_root / "runtime" / "diagnostics"
    assert config.log_path == project_root / "runtime" / "logs"
    assert config.default_timeout_ms > 0
    assert config.channel == "chrome"


def test_browser_provider_reports_missing_chrome_without_starting(
    tmp_path: Path,
) -> None:
    config = BrowserConfig(
        "https://flow.google.com/",
        tmp_path / "profile",
        tmp_path / "diagnostics",
        tmp_path / "logs",
    )
    provider = BrowserProvider(config, executable_finder=lambda: None)

    capabilities = provider.get_capabilities()

    assert capabilities.mode is BrowserIntegrationMode.UNAVAILABLE
    assert not capabilities.browser_available
    assert not capabilities.cdp_connection_available
    with pytest.raises(BrowserLifecycleError, match="Chrome stable was not found"):
        asyncio.run(provider.start())
    assert not config.profile_path.exists()


def test_browser_provider_reports_isolated_cdp_mode(
    tmp_path: Path,
) -> None:
    config = BrowserConfig(
        "https://flow.google.com/",
        tmp_path / "profile",
        tmp_path / "diagnostics",
        tmp_path / "logs",
    )
    provider = BrowserProvider(
        config,
        executable_finder=lambda: Path("chrome.exe"),
    )

    capabilities = provider.get_capabilities()

    assert capabilities.mode is BrowserIntegrationMode.CHROME_CDP
    assert capabilities.browser_available
    assert capabilities.cdp_connection_available


def test_failure_screenshot_redacts_text_and_media(tmp_path: Path) -> None:
    page = FakeScreenshotPage()

    async def run() -> Path:
        return await capture_failure_screenshot(cast(Page, page), tmp_path)

    screenshot_path = asyncio.run(run())

    assert screenshot_path.parent == tmp_path
    assert screenshot_path.is_file()
    assert "color: transparent" in page.style
    assert "visibility: hidden" in page.style


def test_profile_lock_prevents_sharing_and_releases_without_deleting_profile(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "browser-profile"
    profile.mkdir()
    first_lock = _ProfileLock(profile)
    second_lock = _ProfileLock(profile)

    first_lock.acquire()
    try:
        with pytest.raises(BrowserAlreadyRunningError):
            second_lock.acquire()
    finally:
        first_lock.release()

    second_lock.acquire()
    second_lock.release()
    assert (profile / ".flow-automation.lock").exists()
    assert profile.exists()
