# Native Android App — Kaggle Cloud Manager (mobile control plane)

- **Author:** JamesWalton69
- **Status:** Draft
- **Date:** 2026-11-03

## Overview

A native Android companion for the deployed Kaggle Cloud Manager at
**https://renderxkaggle.onrender.com**. The web dashboard controls remote Kaggle
notebook executions; the app gives the owner the same control from a phone:
monitor state, launch runs, stop/cancel jobs, approve AI-agent actions, and chat
with the assistant.

The backend is already live and complete — **no backend changes are required.**
The app is a pure client over the existing REST API (session-cookie auth).

## The Problem

The manager's core value is "control my Kaggle executions from anywhere". Today
that requires a browser. A native app gives quick one-tap run/stop/approve
actions, push-ready presence (polling in background while foregrounded), and a
first-class mobile UX.

## Goals

1. Authenticate with the admin password against the live backend and persist the
   session cookie securely on-device.
2. Overview tab: engine state, job counts, active job, recent jobs.
3. Notebooks tab: list notebooks with last-run status; **Run** a notebook;
   **create** a minimal notebook (title + code).
4. Jobs tab: list jobs with status; open detail (logs, error); **Cancel/Stop**
   (queued/uncertain vs running/stopping) and **Requeue** where valid.
5. Approvals tab: list pending approval requests; Approve / Reject (this is the
   hard-gated AI path).
6. Chat tab: send a message to the assistant; show reply + pending-approval
   notices; load history on entry.
7. Handle Render free-tier sleep: generous timeouts + "server waking up" UX.

## Non-Goals

- Editing settings / Kaggle accounts on mobile (web dashboard remains the
  admin surface for secrets).
- Downloading job output files on-device (open the web dashboard for that).
- Push notifications (polling only while the app is foregrounded).
- Offline mode / caching beyond in-memory last view.
- iOS, tablets-specific layouts, multi-user/multi-tenant.

## API Contract (fixed, from `app/main.py`)

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/api/login` | `{password}` | 200 `{ok}` + `Set-Cookie: kc_session=…` (HttpOnly, SameSite=Lax, max-age 30d, **no Secure flag**) |
| GET | `/api/me` | — | `{authenticated: bool}` |
| POST | `/api/logout` | — | `{ok}` |
| GET | `/api/overview` | — | counts, total_jobs, success_rate (nullable), notebooks, active_job (nullable), engine_state, pending_approvals, recent_jobs[] |
| GET | `/api/notebooks` | — | rows: id, slug, title, description, accelerator (string), internet (int 0/1), schedule, enabled, last_job_status, last_job_id |
| POST | `/api/notebooks` | `{title, code, description?, accelerator?, internet?}` | `{id, slug}` |
| POST | `/api/notebooks/{id}/run` | `{comment?}` | `{job_id, status}` |
| GET | `/api/jobs` | `?status=&limit=` | job[] (public shape, limit 50 default) |
| GET | `/api/jobs/{id}` | — | job + `logs`[] `{ts, line}` |
| POST | `/api/jobs/{id}/cancel` | — | `{ok, message}` (queued/uncertain → cancel; running/stopping → stop; no remote ref yet → 409) |
| POST | `/api/jobs/{id}/requeue` | — | `{ok}` (terminal statuses only; otherwise 409) |
| GET | `/api/approvals` | `?status=pending` | rows: id, tool, params (object), reason, created_at |
| POST | `/api/approvals/{id}/decide` | `{approve: bool}` (real JSON boolean) | `{ok, agent}` — may itself return a new pending approval |
| POST | `/api/agent/message` | `{text}` | `{answer: string\|null, pending_approval: int\|null, turns?: int, message?: string}` — `pending_approval != null` means approval required |
| GET | `/api/agent/history` | — | `[{role, content, ts, meta}]` |

Auth: session cookie `kc_session`; no CSRF header; expiry is **client-side only**
(server keeps sessions until 30d cookie age or logout). Job statuses: `queued,
running, stopping, success, failed, cancelled, interrupted, uncertain`.

Job public shape: `id, notebook_id, notebook_title, kind, status,
kaggle_status, error, created_at, started_at, finished_at, meta`.

Accelerator keys (exactly these 10): `auto, cpu, gpu_t4, gpu_t4_x2,
gpu_p100, gpu_v100, gpu_l4, gpu_a100, gpu_h100, tpu_v3_8`. `internet`
defaults to true.

Action matrix (per job status):

| Status | Cancel/Stop | Requeue |
|---|---|---|
| queued | ✅ cancel | ❌ |
| uncertain | ✅ cancel | ❌ |
| running | ✅ stop | ❌ |
| stopping | ✅ stop (idempotent) | ❌ |
| success / failed / cancelled / interrupted | ❌ | ✅ |

## Critical User Journeys (CUJs)

### CUJ 1: Login and session restore
- **Given** the app is installed and the backend is published.
- **When** the app opens.
- **Then** it calls `GET /api/me`; authenticated → main UI, else login screen.
  A correct password sets the session cookie (persisted encrypted); a wrong one
  shows "invalid password".

### CUJ 2: Stop a runaway execution
- **Given** a job is `running` (or `stopping`).
- **When** the owner taps **Stop** on that job.
- **Then** the app calls `/api/jobs/{id}/cancel` (backend snapshots outputs and
  pushes the stop stub), confirms with the returned message, and the job list
  reflects `stopping` → `cancelled` on refresh.

### CUJ 3: Approve the AI assistant
- **Given** the agent has requested a sensitive action.
- **When** the owner opens Approvals and taps **Approve**.
- **Then** `/api/approvals/{id}/decide {approve:true}` runs, the request leaves
  the pending list, and the chat shows the agent's resumed reply.

### CUJ 4: Server sleep (Render free tier)
- **Given** the instance has slept (~15 min idle; ~1 min cold start).
- **When** any request is made.
- **Then** the app keeps waiting up to 60s, shows a "server is waking up…"
  indicator on timeout, and offers Retry — it never silently fails.

## Error Handling & Edge Cases

- Non-2xx → parse `detail` from JSON error body and surface it verbatim.
- Network failure → distinguish: DNS/unreachable; **connect timeout** (Render
  cold start → "server is waking up…" + Retry); **read timeout on chat** (LLM
  can take up to 120s → "assistant is still thinking…" — keep waiting);
  401 (session expired → clear stored cookie + force re-login); 409 (conflict →
  surface verbatim; e.g. Stop pressed before a run reached Kaggle, or requeue
  of an active job — keep the button enabled, retry is valid).
- Empty states everywhere (no notebooks / no jobs / no approvals / no chat).
- Auto-refresh (30s) only while a tab is visible; cancelled on dispose.

## Architecture

- **Single module** `android/` (Gradle project). Kotlin 2.0.20, AGP 8.5.2,
  Gradle 8.9 (wrapper `distributionUrl` pinned), compileSdk 34, minSdk 26,
  targetSdk 34, Kotlin Compose compiler plugin (`org.jetbrains.kotlin.plugin.compose`
  v2.0.20) — required with Kotlin 2.x.
- **UI:** Jetpack Compose (Material 3, BOM 2024.09.03), one `MainActivity`,
  bottom navigation (Overview / Notebooks / Jobs / Approvals / Chat). No
  ViewModel/DI libs — plain composables + `mutableStateOf` + `LaunchedEffect`.
  Edge-to-edge: Scaffold inner padding applied.
- **Network:** OkHttp 4.12 + `org.json` (no Retrofit/serialization libs).
  Suspend helpers run blocking calls on `Dispatchers.IO`. Session cookie
  (`kc_session`) is captured from login's `Set-Cookie` and stored in an
  **app-private file** (`MODE_PRIVATE`; `androidx.security-crypto` is deprecated
  and deliberately avoided) and sent as a `Cookie` header on every request.
  401 → clear + re-login.
- **Timeouts:** non-chat: connect 20s / read 45s; chat: connect 20s / read
  130s (LLM budget).
- **Base URL:** single constant in `Api.kt` (`https://renderxkaggle.onrender.com`).
- **Theming:** dark-first Material 3, mirrors the web dashboard palette.

## File Layout (the whole app)

```
android/
  settings.gradle.kts
  build.gradle.kts
  gradle.properties
  gradle/wrapper/gradle-wrapper.properties
  app/
    build.gradle.kts
    proguard-rules.pro
    src/main/AndroidManifest.xml
    src/main/java/com/kaggle/manager/
      MainActivity.kt
      Api.kt            (client, cookie jar, endpoints, models)
      Session.kt        (encrypted cookie persistence)
      ui/Login.kt
      ui/MainScreen.kt  (scaffold + bottom nav + tabs)
      ui/OverviewTab.kt
      ui/NotebooksTab.kt
      ui/JobsTab.kt
      ui/ApprovalsTab.kt
      ui/ChatTab.kt
      ui/Components.kt  (badges, cards, confirm dialog, refresh)
      ui/Theme.kt
      res/...           (strings, themes, icons, launcher)
```

## Open Questions

- None blocking. (Device testing requires the user's Android Studio/phone; the
  Gradle wrapper jar is intentionally omitted — Android Studio provisions it.)

## Verification

- Static: `node`-free; Kotlin syntax checked by opening in Android Studio.
- Live: first launch against the deployed URL; login; run a small notebook;
  stop it; approve a test approval (agent must be configured first).
