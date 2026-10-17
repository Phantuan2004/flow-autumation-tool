# Google Login diagnostics

## Security boundary

The application opens Google Chrome stable with a separate, app-owned profile
under `runtime/browser-profile-manual-chrome`. It connects to that process over
Chrome DevTools Protocol (CDP), bound to `127.0.0.1`. It does not connect to the
user's default Chrome profile or transfer authentication data from it.

The user enters credentials and completes any account checks directly in
Chrome. The application never asks for, stores, or submits passwords, OTPs,
CAPTCHAs, cookies, or tokens. It does not alter browser fingerprints, bypass
Google protections, or call private authentication endpoints.

## If Google says the browser is not secure

This warning is controlled by Google. CDP provides a supported browser-control
connection; it does not promise that Google will accept every browser profile
or sign-in attempt. The user has since confirmed that `open-flow` currently
allows manual sign-in and reaches the Flow dashboard. If the warning reappears,
use the safe steps below.

If the warning appears:

1. Stop and do not retry repeatedly.
2. Do not disable security checks, modify automation signals, copy cookies, or
   use the personal Chrome profile.
3. If organizational policy allows, use regular Chrome manually for Flow work.
   A successful sign-in there does **not** mean the app can control that Chrome
   session.
4. Only resume app integration if Google permits sign-in in the isolated
   app-managed Chrome profile. Otherwise the app must remain disconnected.

The application does not claim the login problem is fixed by changing from
Playwright launch to CDP.

## What the connection status means

- `UNAUTHENTICATED`: a visible Flow sign-in link was observed.
- `AUTH_REQUIRED`: the current browser page is on Google's account sign-in
  host; complete authentication manually.
- `AUTHENTICATED`: the visible **Dự án mới** dashboard action or verified
  project-editor controls positively confirms Flow workspace access and no
  sign-in prompt is present. This does not verify every media permission.
- `UNKNOWN`: the app cannot safely infer access from the current page. The
  project editor remains `UNKNOWN` until its own stable UI signal is verified.
- `BROWSER_UNAVAILABLE`: the app-managed browser or page is closed.

Browser launch or absence of a sign-in link is not proof of authenticated Flow
access. The user has confirmed manual sign-in with `open-flow`; in a live
project, the editor controls were also observed. Authentication and ability to
automate each media operation are separate capabilities.
