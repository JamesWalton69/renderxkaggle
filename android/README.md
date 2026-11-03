# Kaggle Cloud Manager — Android app

Native Android client for the deployed manager at
**https://renderxkaggle.onrender.com**. Kotlin + Jetpack Compose (Material 3),
OkHttp, no Retrofit/serialization libs.

## Features

- Login with the admin password; session cookie stored in an app-private file.
- **Overview** — engine state, counts, active job, recent jobs.
- **Notebooks** — list, run, and create notebooks (title, code, accelerator,
  internet toggle).
- **Jobs** — filter, detail with logs, Cancel/Stop (running/stopping → stub
  push) and Requeue (terminal statuses).
- Handles Render free-tier sleep (60s wake-up allowance).

## Build & run

1. Install **Android Studio** (current stable) — it bundles JDK 17+.
2. Open this folder (`android/`) as a project.
3. Wait for the first Gradle sync (downloads Gradle 8.11.1 + dependencies).
   **Gradle wrapper jar/scripts are intentionally not committed** — Android
   Studio generates/regenerates the wrapper on first sync; alternatively run
   `gradle wrapper --gradle-version 8.11.1` once from a machine with Gradle.
4. Run **app** on a device/emulator (min SDK 26).

## Configuration

- Backend URL: constant `Api.BASE_URL` in
  `app/src/main/java/com/kaggle/manager/Api.kt` (default
  `https://renderxkaggle.onrender.com`).
- The backend must be deployed and reachable; sign in with the same
  `ADMIN_PASSWORD`.
- There is no in-app AI assistant; the backend exposes no agent/approval
  endpoints. Drive automation through the MCP server instead (see the repo-root
  `README.md` → "Access via MCP").

## Notes

- Output file downloads are intentionally left to the web dashboard (v1
  non-goal).
- Security: the `kc_session` cookie is stored in `filesDir` (app-private); it
  is not stored in any backup-visible preference store. Session expiry is
  client-side (server keeps sessions for 30 days).
- Version pins: AGP 8.5.2, Kotlin 2.0.20 (+ Compose compiler plugin), Compose
  BOM 2024.09.00, OkHttp 4.12.0, minSdk 26 / targetSdk 34, Gradle 8.9.
