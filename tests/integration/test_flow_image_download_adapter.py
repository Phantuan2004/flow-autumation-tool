from __future__ import annotations

import asyncio
import base64
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from playwright.async_api import Page, async_playwright

from flow_automation.automation.browser_provider import find_chrome_executable
from flow_automation.automation.flow_adapter import (
    FlowAdapter,
    FlowAsset,
    FlowTask,
    _ImageTaskState,
)
from flow_automation.automation.locators import (
    FLOW_DOWNLOAD_MENU_ITEM_NAME,
    FLOW_IMAGE_MORE_OPTIONS_BUTTON_NAME,
)
from flow_automation.automation.session_manager import SessionManager


class _PageProvider:
    def __init__(self, page: Page) -> None:
        self.page = page

    async def capture_failure_screenshot(self) -> None:
        return None


def test_image_adapter_downloads_observed_original_size_menu_item(
    tmp_path: Path,
) -> None:
    if find_chrome_executable() is None:
        pytest.skip("Install Google Chrome stable to run the UI download integration test.")

    async def run() -> Path:
        image_data = BytesIO()
        Image.new("RGB", (1376, 768), color="navy").save(
            image_data,
            format="JPEG",
        )
        encoded_image = base64.b64encode(image_data.getvalue()).decode("ascii")
        html = f"""
            <flow-image-tile>
              <img class="image" width="1376" height="768"
                   src="data:image/jpeg;base64,{encoded_image}">
              <button aria-label="{FLOW_IMAGE_MORE_OPTIONS_BUTTON_NAME}"
                      onclick="document.querySelector('[role=menuitem]').hidden=false">
                more
              </button>
              <div role="menuitem" aria-label="{FLOW_DOWNLOAD_MENU_ITEM_NAME}"
                   hidden
                   onclick="document.querySelector('#original-size').hidden=false">
                download
              </div>
              <div id="original-size" role="menuitem" hidden
                   onclick="downloadImage()">1K Kích thước gốc</div>
            </flow-image-tile>
            <script>
              function downloadImage() {{
                const source = document.querySelector('img').src;
                const anchor = document.createElement('a');
                anchor.href = source;
                anchor.download = 'flow-image.jpg';
                anchor.click();
              }}
            </script>
        """
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                channel="chrome",
                headless=True,
            )
            try:
                context = await browser.new_context(accept_downloads=True)
                page = await context.new_page()
                await page.set_content(html)
                image = page.locator("flow-image-tile img")
                await page.wait_for_function(
                    """() => {
                        const image = document.querySelector('flow-image-tile img');
                        return image && image.complete && image.naturalWidth === 1376;
                    }"""
                )
                adapter = FlowAdapter(_PageProvider(page), SessionManager())
                task = FlowTask("integration-download-task")
                asset = FlowAsset(
                    f"{task.reference}:image:0",
                    "flow-image-integration",
                )
                adapter._image_tasks[task.reference] = _ImageTaskState(
                    baseline_tile_count=0,
                    baseline_loaded_image_count=0,
                    completed=True,
                    assets=(asset,),
                    image_locators=(image,),
                )
                destination = tmp_path / "flow-image.jpg"
                return await adapter.download_asset(asset, destination)
            finally:
                await browser.close()

    downloaded_path = asyncio.run(run())

    assert downloaded_path.is_file()
    with Image.open(downloaded_path) as image:
        assert image.format == "JPEG"
        assert image.size == (1376, 768)
