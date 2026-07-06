# Android Startup Logging Plan

## Current State

- The Android app still crashes on startup on the Samsung Fold after several startup-hardening fixes.
- Already landed in code:
  - notification posting is permission-guarded in `sync/MobileSyncWorker.kt`
  - the UI no longer auto-requests notification permission on first composition
  - `WorkManager` scheduling in `OpenClosetMobileApp.kt` is wrapped in `runCatching`
  - `MobileRepository` construction in `MainActivity.kt` is lazy instead of eager in the initial `MainViewModel` path
- Latest startup-hardened build version:
  - `versionCode = 5`
  - `versionName = "0.1.4-startup-hardening"`

## Goal

Add Android-side logging that survives startup failure well enough to answer one question quickly: where does the process die before the first usable frame?

## Recommended Implementation

### 1. Add a single mobile logging utility

Create a new file such as `app/src/main/java/com/openclaw/openclosetmobile/logging/MobileLog.kt`.

Requirements:

- Log to Android `Logcat`
- Also append to a local file under `filesDir/logs/opencloset-mobile.log`
- Use a small rolling buffer policy, for example:
  - truncate when file exceeds 512 KB
  - keep only the newest content
- Include:
  - timestamp
  - process id / thread name if easy
  - level
  - tag
  - event name
  - message
  - throwable stack if present

Suggested tags:

- `OC_APP`
- `OC_STARTUP`
- `OC_SYNC`
- `OC_NET`
- `OC_UI`

### 2. Install global crash capture in `OpenClosetMobileApp`

In [OpenClosetMobileApp.kt](opencloset/mobile/app/src/main/java/com/openclaw/openclosetmobile/OpenClosetMobileApp.kt):

- register `Thread.setDefaultUncaughtExceptionHandler(...)`
- log app boot start before any other startup work
- log device/build info once:
  - manufacturer
  - model
  - Android SDK version
  - app version name/code
- wrap startup milestones with explicit log lines:
  - before scheduling `WorkManager`
  - after scheduling `WorkManager`
  - if `WorkManager` throws, log the throwable

### 3. Instrument the first activity path

In [MainActivity.kt](opencloset/mobile/app/src/main/java/com/openclaw/openclosetmobile/MainActivity.kt):

- log `onCreate` entry
- log after `enableEdgeToEdge()`
- log before `setContent`
- log first entry to `OpenClosetMobileRoot`
- log current selected tab default once

In `MainViewModel`:

- log constructor / init entry
- log before accessing `SettingsStore`
- log before parsing cached bootstrap
- log if `MobileRepository` construction fails
- log every `refresh()` start/failure with throwable

### 4. Instrument repository creation and API boundaries

In [MobileRepository.kt](opencloset/mobile/app/src/main/java/com/openclaw/openclosetmobile/data/MobileRepository.kt):

- log repository initialization start/end
- log Retrofit base URL after normalization
- log parse failures in `parseCachedBootstrap(...)`
- log request start/failure for:
  - `refreshBootstrap`
  - `listSessionMessages`
  - `getBehaviorState`

Do not log full user content bodies unless necessary. Prefer ids, counts, and exception text.

### 5. Add a user-visible log export path

In the Settings tab, add a compact debug block:

- `Copy recent logs`
- `Share logs`
- `Clear logs`

Minimum viable version:

- read the last 100-200 lines from `opencloset-mobile.log`
- copy to clipboard or share via Android `ACTION_SEND`

This removes dependence on USB or wireless ADB for the next crash.

### 6. Add one explicit startup breadcrumb sequence

Use short event ids so a crash report immediately shows the last successful step, for example:

- `startup.app.onCreate.begin`
- `startup.workmanager.begin`
- `startup.workmanager.ok`
- `startup.activity.onCreate.begin`
- `startup.activity.setContent.begin`
- `startup.compose.root.begin`
- `startup.viewmodel.init.begin`
- `startup.settings.collect.begin`
- `startup.bootstrap.parse.begin`

If the last emitted line is known, the crash zone becomes obvious.

## Suggested File Order For Next Coder

1. Add `MobileLog.kt`
2. Instrument `OpenClosetMobileApp.kt`
3. Instrument `MainActivity.kt`
4. Instrument `MobileRepository.kt`
5. Add Settings-tab export controls
6. Rebuild APK and retest on phone

## Validation Plan

After logging lands:

1. Build and install the APK
2. Launch once on the Fold
3. If it crashes, retrieve:
   - exported in-app log file if available, or
   - wireless ADB `logcat`, or
   - Samsung crash dialog details
4. Patch the exact failing step instead of continuing blind startup hardening

## APK Download Link

The last known LAN download URL was:

- `http://10.0.0.70:8765/app-debug.apk`

It returned HTTP `200` during the latest check on 2026-05-07.

If that URL stops serving, restart it from the APK directory with:

```powershell
Set-Location d:/openclaw/opencloset/mobile/app/build/outputs/apk/debug
py -m http.server 8765 --bind 0.0.0.0
```

Then download again from:

- `http://10.0.0.70:8765/app-debug.apk`

Stop the server with `Ctrl+C` in that terminal.