# OpenCloset Mobile

OpenCloset Mobile is the first Android harness for the phone node in the OpenCloset ecosystem.

This project is intentionally built around existing tooling instead of bundling a second local-model stack into the app:

- OpenCloset Mobile owns network sync, local cache, approvals, polling, notifications, and APK delivery.
- PocketPal remains the local-model substrate for on-device distillation and phone-side chat.
- The current integration point with PocketPal is an external-app handoff plus a clipboard-ready PhoneCloset prompt.

## Current Scope

- Cache shared workspaces, projects, deliveries, and recent captures on the phone.
- Poll the OpenCloset backend in the background with WorkManager.
- Notify the user when new device-targeted deliveries appear.
- Download APK deliveries through the backend delivery route.
- Mark deliveries downloaded, installed, or failed.
- Draft a feature idea on the phone, hand the distillation prompt off to PocketPal, and submit the approved brief back into OpenCloset.

## Backend Contract

The app expects these endpoints from the OpenCloset backend:

- `GET /api/mobile/bootstrap?device_id=<id>`
- `POST /api/workspaces/<workspace_id>/captures`
- `GET /api/workspaces/<workspace_id>/deliveries/<delivery_id>/download`
- `PATCH /api/workspaces/<workspace_id>/deliveries/<delivery_id>`

## PocketPal Integration

This harness does not assume PocketPal exposes a stable local automation API. The current model-handoff path is:

1. Write a raw ramble in OpenCloset Mobile.
2. Copy a structured PhoneCloset prompt to the clipboard.
3. Launch PocketPal from OpenCloset Mobile.
4. Paste the prompt into PocketPal, review the distilled result, and bring the approved brief back into OpenCloset Mobile.
5. Submit the approved brief to the broader OpenCloset network.

If PocketPal later exposes an automatable local API, that connector can replace the clipboard-launch path without changing the rest of the harness.

## Build

This is a standard Android Studio / Gradle project.

From `opencloset/mobile`:

```powershell
./gradlew.bat :app:assembleDebug
```

An Android SDK and Java 17 are required. This repository now vendors a Gradle wrapper.

## Desktop Build And Queue

The desktop-side path for the Fold 5 is `build-and-queue-apk.ps1` in this folder.

It is intended to:

- build `:app:assembleDebug` or `:app:assembleRelease`
- locate the produced APK
- upload it to the existing OpenCloset project-delivery queue
- target a specific device such as `fold5`

Example:

```powershell
./build-and-queue-apk.ps1 -WorkspaceId <workspace> -ProjectId <project> -DeviceId fold5
```

Optional routing:

```powershell
./build-and-queue-apk.ps1 -WorkspaceId <workspace> -ProjectId <project> -DeviceId fold5 -SessionId <session>
```