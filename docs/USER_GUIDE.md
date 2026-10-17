# User guide

## Open the isolated browser

1. Start the application with `flow-automation`.
2. Select **Open Browser**.
3. Chrome opens with a dedicated profile owned by this application.
4. Navigate and sign in manually. Complete any account verification yourself.
5. Return to the application and select **Check Connection**.

The application does not request credentials. It does not reuse regular Chrome
profiles. A successful login in regular Chrome does not share that session with
the app.

The profile is stored under `runtime/browser-profile-manual-chrome` and
persists across application restarts. Do not delete, copy, or share it while
the application is running. Only one app process can own the profile at a
time. Close the Chrome window to end the managed browser session.

## Interpret the connection result

`Connected` is shown when the app sees the verified dashboard action
**Dự án mới** or the verified project-editor controls and no sign-in prompt.
This confirms Flow workspace access only; it does not establish permission to
use every image/video feature.

The adapter code can submit one image request only when the observed model,
image mode, x1 variant, prompt field, and submit button match the verified UI.
The adapter blocks a second request while a prior task is unresolved. The
adapter's submit/wait/collect chain has unit-test coverage but has not itself
been run against the live account or connected to a user-facing image action in
the UI. A separate live UI probe created one image and observed its loaded
result. No batch submission is supported.

One live test selected the image's **Tải xuống → 1K / Kích thước gốc**
option and saved a JPEG under
`runtime/assets/flow-smoke/download-final-20261017-100248/`. Its measured
dimensions are 1376×768 pixels. This confirms that UI route once, but
`FlowAdapter.download_asset()` is not yet implemented or exposed in the UI.

One live video test also succeeded using the existing apple image as a
component: Omni 1.1 Flash, one variant, 720p, 8 seconds. The file was saved
using **Tải xuống → 720p / Kích thước gốc** under
`runtime/assets/flow-smoke/video-download-20261017-103139/`; local inspection
confirmed H.264, 1280×720, 24 fps, 8 seconds. This was a single live test,
not a general guarantee. Video generation/download remain unsupported through
the adapter and app UI.

## Google sign-in warning

If Google displays “This browser or app may not be secure”, stop the sign-in
attempt. Do not try to bypass the warning. See
[Google Login diagnostics](./GOOGLE_LOGIN_DIAGNOSTICS.md) and
[Troubleshooting](./TROUBLESHOOTING.md).

## Data and privacy

The isolated profile, diagnostics, logs, and runtime files are stored beneath
`runtime/`, which is excluded from Git. Avoid sharing the profile or
unredacted screenshots; they can contain sensitive account information.
