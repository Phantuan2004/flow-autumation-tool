# Flow UI compatibility and verification

This document separates live observations from adapter code that has only been
unit-tested. Flow UI labels and behavior may change; the adapter fails closed
when the observed controls do not match.

## Browser and session

| Operation | Status | Evidence |
| --- | --- | --- |
| Launch installed Chrome stable with an app-specific profile and attach over loopback CDP | Verified on this Windows machine | A local-page browser lifecycle probe succeeded. The app does not use the ordinary Chrome profile. |
| Manually sign in using `open-flow` and reach the Flow dashboard | Verified by user report and observed live | The dashboard displayed the **Dự án mới** action; Google sign-in was not displayed. |
| Detect dashboard access | Verified | The visible semantic **Dự án mới** button is recognized, absent conflicting sign-in signals. |
| Detect project-editor access | Verified | The live editor exposed the prompt textbox, image settings button, and start button; the detector returned `AUTHENTICATED`. |
| Reuse the app-managed profile after an application restart | Not integration-tested in this environment | The profile is retained under ignored `runtime/`; a restart reuse test is still outstanding. |
| macOS or Linux browser discovery/lifecycle | Not tested | No cross-platform support claim is made. |

`AUTHENTICATED` means the observed Flow workspace is accessible. It does not
assert that the account can use every model or media operation.

## Live Flow UI observations

One authorized, single-image probe was performed in a manually opened test
project. It was a direct Playwright UI probe, not a live end-to-end run of the
`FlowAdapter` implementation.

| UI/control | Observation |
| --- | --- |
| Prompt textbox | Visible, labelled “Bạn muốn tạo những gì?” |
| Submit control | Visible, labelled “Bắt đầu tạo” |
| Settings control | Displayed Nano Banana 2.1 and current aspect-ratio/variant selection |
| Model | Nano Banana 2.1 |
| Mode | Image |
| Aspect ratios | 16:9, 4:3, 1:1, 3:4, 9:16 |
| Variant counts | x1, x2, x3, x4 |
| Resolution | The image download menu exposed **1K / Kích thước gốc**. Selecting it saved a JPEG whose measured dimensions are 1376×768 pixels. The 1K label is a Flow UI option; the actual pixel dimensions are reported separately. |
| Concurrency | Not tested or inferred. |

The probe selected image mode and x1, submitted one prompt once, and observed
one new loaded image tile. The result image rendered at 1376×768. No second
generation was submitted.

## Operation status

| Operation | Status | Evidence / limitation |
| --- | --- | --- |
| Open a project manually and inspect its editor | Verified in the live session | The editor controls listed above were visible. |
| Create one image via the live UI | Verified once | One x1 Nano Banana 2.1 image was submitted and rendered. This does not validate adapter selectors end-to-end. |
| Observe completion and a loaded image | Verified once in the live probe | A new tile contained a loaded image. |
| `FlowAdapter.create_image()` | Implemented; unit-tested only; not exposed in the UI | Checks authenticated workspace, observed settings, image mode, x1, empty prompt, and a unique enabled submit control. No live adapter submission has been made. |
| `FlowAdapter.wait_for_result()` | Implemented; unit-tested only | Waits for a new image tile with a fully loaded image. Timeout is an unknown outcome; the adapter does not resubmit. |
| `FlowAdapter.collect_images()` | Implemented; unit-tested only | Returns one local task reference for one new loaded image tile; it does not download image bytes. |
| Download one image through Flow UI | Verified once | Opening **Tải xuống** exposed a **1K / Kích thước gốc** choice. Selecting it completed a Chrome download to `runtime/assets/flow-smoke/download-final-20261017-100248/flow-image-original-1k.jpg`; Pillow verified JPEG, 66,108 bytes, 1376×768 pixels. |
| `FlowAdapter.download_asset()` | Unsupported | The live UI route is now observed, but the adapter method does not yet implement the menu selection or file-save lifecycle. |
| Create one video from the existing image | Verified once by direct UI test | **Omni 1.1 Flash**, video mode, the existing red-apple image attached as a component, x1, 720p, 8 seconds, using the user-approved camera-orbit prompt. One new video tile appeared after one submit. No retry was made. |
| Download one video at original 720p | Verified once | Selected **Tải xuống → 720p / Kích thước gốc**. Chrome reported completion and saved `runtime/assets/flow-smoke/video-download-20261017-103139/Red_apple_rotating_20261017103523.mp4` (1,542,033 bytes). `ffprobe` verified H.264, 1280×720, 24 fps, exactly 8 seconds. |
| `FlowAdapter.create_video()` and `FlowAdapter.download_asset()` | Unsupported | The live UI path is verified once, but neither operation is wired into the adapter or app UI/job worker. |
| Open a project through `FlowAdapter.open_project()` | Unsupported | Project navigation is not automated. |
| Batch generation | Not supported | No batch or concurrency behavior has been exercised. |

The adapter reports partial image observations but leaves the overall
capability set unverified while adapter/UI integration and batch support remain
unresolved. It prevents a new image submission while a previous task is
unresolved, including an ambiguous submit outcome. An ambiguous task must be
reconciled manually before continuing.

## Conditions and failures tested

Automated unit tests use mocks and cover session classification, observed
dashboard/editor signals, unsupported operations, image-submit-once behavior,
result collection, and blocking a retry after an uncertain submit. These tests
do not access a real Flow account.

An earlier image-download attempt stopped at the **Tải xuống** menu entry,
which opened a size submenu rather than starting a download. Selecting
**1K / Kích thước gốc** then produced a completed Chrome download event and a
verified local JPEG. The video download likewise required explicitly choosing
**720p / Kích thước gốc** from its submenu. Both final files were verified on
disk; opening either parent menu alone does not start a download.

## Manual checklist

| Check | Status |
| --- | --- |
| Launch isolated Chrome and attach over loopback CDP | Verified on Windows with a local-page probe |
| Manually sign in and reach the Flow dashboard | Verified by user report and live dashboard observation |
| Recheck connection in dashboard and project editor | Verified against observed controls |
| Restart the app and reuse the app-managed profile | Not yet tested |
| Expired session and reauthentication | Not tested against a real expired session |
| Flow loading delay and missing live locator | Not tested against the real Flow UI |
| Generate one x1 image and observe its loaded tile | Verified once by direct UI probe |
| Download 1K original-size image | Verified once; completed file measured 1376×768 pixels and 66,108 bytes |
| Generate Omni 1.1 Flash video from the existing image, x1 at 720p for 8 seconds | Verified once; one result tile appeared |
| Download video at original 720p | Verified once; H.264 1280×720, 24 fps, 8 seconds, 1,542,033 bytes |
| Close browser during an active Flow job | Not tested |

## Safe use

Sign in manually only in the app-managed Chrome profile. Do not copy cookies,
tokens, or profile files from ordinary Chrome. Do not retry an image
submission after an ambiguous outcome until the project has been inspected.
The profile, logs, screenshots, and media remain under ignored `runtime/`.
