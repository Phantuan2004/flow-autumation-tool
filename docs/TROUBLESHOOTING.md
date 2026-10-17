# Troubleshooting

## Google reports that the browser is not secure

The app uses an app-specific Chrome profile and a loopback CDP connection.
CDP does not guarantee Google will accept sign-in. Stop retrying and do not
disable protections, alter browser signals, reuse a personal profile, or copy
session data. A regular Chrome login is separate and cannot be assumed to be
available to the app. See [Google Login diagnostics](./GOOGLE_LOGIN_DIAGNOSTICS.md).

## Chrome is not found

Install Google Chrome stable using the normal organization-approved installer,
then restart the application. The app does not assume Chrome is installed on
every supported operating system. Browser discovery on Windows was tested;
macOS and Linux have not been verified.

## Chrome profile is already in use

Close the Chrome window started by Flow Automation Tool and any second
application instance using the app profile. Do not open that profile directly
in another browser process. The application never deletes the profile to
recover a lock.

## Connection shows UNKNOWN

The UI may be open and signed in while the application cannot find either
verified signal: the visible **Dự án mới** dashboard action or the project
editor's prompt, start, and image-settings controls. Do not treat `UNKNOWN` as
connected or submit media jobs. Confirm the page is fully loaded and visibly
in the Flow dashboard or an open project, then check connection again.

## Image download via the app is not yet available

One live UI download succeeded after opening the image menu and selecting the
**1K / Kích thước gốc** option. The saved JPEG measured 1376×768 pixels.
`FlowAdapter.download_asset()` is still unsupported, so this one-off result
does not mean the application can download assets through its normal workflow.
Do not repeat a download when its outcome is uncertain; check the saved file
in `runtime/assets/flow-smoke/` first.

For video, the download menu also has **720p / Kích thước gốc**. One test
download completed and `ffprobe` confirmed H.264 at 1280×720 for 8 seconds.
This live result does not mean video download is integrated into the app.

## Browser closed or did not start

Reopen the application-managed browser and retry once. If Chrome is installed
but fails to start, preserve the sanitized application log and report the
operating system and app version. Do not include credentials, cookies, tokens,
browser profile files, or unredacted screenshots.
