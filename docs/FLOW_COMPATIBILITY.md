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

An earlier authorized, single-image probe was performed in a manually opened
test project. In the current PoC, `FlowAdapter` was also run against the
manually authenticated project: it submitted one approved prompt once,
observed/collected one new loaded tile, and did not retry.

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

The probes selected image mode and x1 and observed one new loaded image tile
each. The Milestone 3 result rendered at 1376×768. No second Milestone 3
generation was submitted.

## Operation status

| Operation | Status | Evidence / limitation |
| --- | --- | --- |
| Open a project manually and inspect its editor | Verified in the live session | The editor controls listed above were visible. |
| Create one image via the live UI | Verified once | One x1 Nano Banana 2.1 image was submitted and rendered. This does not validate adapter selectors end-to-end. |
| Observe completion and a loaded image | Verified once in the live probe | A new tile contained a loaded image. |
| `FlowAdapter.create_image()` | Implemented and run live once | Checks authenticated workspace, Nano Banana 2.1/x1, image mode, empty prompt, and waits for the submit control to enable before one click. A known pre-submit rejection is distinguished from an ambiguous click outcome. |
| `FlowAdapter.wait_for_result()` | Implemented and run live once | Waits for a new loaded image in the observed Flow result tile/virtual-scroll UI. Timeout is an unknown outcome; the adapter does not resubmit. |
| `FlowAdapter.collect_images()` | Implemented and run live once | Associated exactly one new loaded image tile with the x1 task and retains its locator for download in the active adapter session. |
| Download one image through Flow UI | Verified once | Opening **Tải xuống** exposed a **1K / Kích thước gốc** choice. Selecting it completed a Chrome download to `runtime/assets/flow-smoke/download-final-20261017-100248/flow-image-original-1k.jpg`; Pillow verified JPEG, 66,108 bytes, 1376×768 pixels. |
| `FlowAdapter.download_asset()` | Implemented; run live once | Hovered the observed image tile, selected **Tùy chọn khác → Tải xuống → 1K / Kích thước gốc**, awaited the download event and saved a JPEG. A first attempt returned an unknown download outcome; after inspecting the same tile and confirming no file was written, a download-only continuation succeeded without resubmitting generation. |
| `MediaVerifier.verify_image()` | Implemented and run live once | Pillow verified JPEG bytes and dimensions (1376×768, 228,746 bytes); SHA-256 was persisted as asset metadata. |
| `FlowImagePocService` and SQLite repository | PoC state persisted; service logic tested with fakes | Bootstrap initializes SQLite and provides `FlowRepository`. The live job history records `PENDING → RUNNING → RECONCILIATION_REQUIRED → SUCCEEDED`; the asset is `VERIFIED` and its stored hash/size/MIME resolve to the on-disk JPEG. The first download attempt required manual reconciliation and a download-only continuation; this does not prove uninterrupted service execution or automatic restart recovery. |
| UI batch image processor, Qt worker, scheduler, and gallery | Implemented; fake-adapter tested, not live batch verified | UI queues persisted scene jobs, requires credit confirmation before starting/retrying, and runs browser-bound work with concurrency 1. Downloads are verified before job success; gallery only exposes verified image assets and rechecks file metadata/hash before approval. No multi-scene Flow batch was run for this integration. |
| Create one video from the existing image | Verified once by direct UI test | **Omni 1.1 Flash**, video mode, the existing red-apple image attached as a component, x1, 720p, 8 seconds, using the user-approved camera-orbit prompt. One new video tile appeared after one submit. No retry was made. |
| Download one video at original 720p | Verified once | Selected **Tải xuống → 720p / Kích thước gốc**. Chrome reported completion and saved `runtime/assets/flow-smoke/video-download-20261017-103139/Red_apple_rotating_20261017103523.mp4` (1,542,033 bytes). `ffprobe` verified H.264, 1280×720, 24 fps, exactly 8 seconds. |
| `FlowAdapter.create_video()` | Unsupported | A previous direct UI video test succeeded, but video automation is not implemented in this adapter/service. |
| Open a project through `FlowAdapter.open_project()` | Unsupported | Project navigation is not automated. |
| Batch generation | Not supported | No batch or concurrency behavior has been exercised. |

The adapter reports image generation, collection and 1K download observations,
but leaves the overall capability set unverified because video, UI integration
and batch support remain unresolved. It prevents a new image submission while
a previous task is unresolved, including an ambiguous submit outcome. An
ambiguous task must be reconciled manually before continuing.

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

In the Milestone 3 PoC, the first adapter download attempt stopped safely and
recorded `RECONCILIATION_REQUIRED`. The same observed image tile was inspected
and downloaded via the adapter without resubmitting the generation. The job
was marked successful only after Pillow verified the saved image.

## Manual checklist

| Check | Status |
| --- | --- |
| Launch isolated Chrome and attach over loopback CDP | Verified on Windows with a local-page probe |
| Manually sign in and reach the Flow dashboard | Verified by user report and live dashboard observation |
| Recheck connection in dashboard and project editor | Verified against observed controls |
| Restart the app and reuse the app-managed profile | Not yet tested |
| Expired session and reauthentication | Not tested against a real expired session |
| Flow loading delay and missing live locator | Not tested against the real Flow UI |
| Generate one x1 image and observe/collect its loaded tile via `FlowAdapter` | Verified once; one request and one tile |
| Download 1K original-size image via `FlowAdapter` and verify file | Verified once; JPEG, 1376×768 pixels, 228,746 bytes |
| Persist job/asset and reconcile the observed download interruption | Verified once in SQLite; no generation was resent; reconciliation was manual |
| Generate Omni 1.1 Flash video from the existing image, x1 at 720p for 8 seconds | Verified once; one result tile appeared |
| Download video at original 720p | Verified once; H.264 1280×720, 24 fps, 8 seconds, 1,542,033 bytes |
| Close browser during an active Flow job | Not tested |

## Safe use

Sign in manually only in the app-managed Chrome profile. Do not copy cookies,
tokens, or profile files from ordinary Chrome. Do not retry an image
submission after an ambiguous outcome until the project has been inspected.
The profile, logs, screenshots, and media remain under ignored `runtime/`.
