# Kaggle Cloud Manager

Manage **Kaggle notebook executions remotely** from a polished web dashboard —
with one or more authorized Kaggle identities (choose per notebook), a persistent
job queue (one active execution at a time), and an **MCP server** that exposes
the same control plane to any AI agent (OpenCode, Claude Desktop, Cursor, …).

Built for **Render's free tier**: one web service + a Neon Postgres database,
no on-instance storage, no build step.

---

## Architecture

```
                    ┌──────────────────────────────────────────────────┐
                    │               Render (free web service)          │
                    │                                                  │
  Browser ────────►│  FastAPI + uvicorn (single worker)                │
  dashboard (SPA)  │   ├── REST API (/api/*)  ← auth via session cookie │
                    │   ├── Static dashboard (/  — index.html + JS)     │
                    │   ├── Job Worker (app/worker.py)  async loop      │
                    │   │    ├─ serial queue picker                     │
                    │   │    ├─ Kaggle polling / status transitions     │
                    │   │    └─ cron scheduler (app/scheduler.py)       │
                    │   └── Postgres (Neon) — durable storage       │
                    │        settings, sessions                         │
                    └───────────┬──────────────────────────────────────┘
                                │
                     ┌──────────▼─────┐        ┌──────────────────────┐
                     │ Kaggle Kernels │        │ MCP server (mcp/) —  │
                     │ API v1 (REST) │        │ primary agent access: │
                     │ (push/status/ │        │ notebooks, jobs,      │
                     │  output/log)  │        │ accounts → tools      │
                     └────────────────┘        └──────────────────────┘
```

**Key decisions**

| Concern | Choice | Why |
|---|---|---|
| Backend | Python 3.12, FastAPI, uvicorn (1 worker) | Small memory footprint on free tier; async-friendly |
| Database | Postgres via Neon (`psycopg2`) | Durable across redeploys on Render's free tier |
| Queue | DB rows + in-process worker | Persistent across restarts; one active execution enforced by the DB |
| Kaggle | Direct REST client over official Kernels API v1 (`app/kaggle_client.py`) | No heavy SDK; full control over retries and errors |
| MCP | MCP server (`mcp/`) exposing the manager as tools | Primary agent access — same API the dashboard uses, no browser needed |
| Frontend | Vanilla JS SPA served by FastAPI | No build step, no node toolchain on Render |
| Auth | Single admin password, scrypt hash, signed session cookie | Enough for a private single-owner control plane |

---

## Access via MCP (primary agent path)

The dashboard has no built-in AI assistant. Instead, the repo ships an **MCP
server** (`mcp/`) that turns the manager's REST API into tools — `list_notebooks`,
`create_notebook`, `run_notebook`, `list_jobs`, `get_job`,
`stop_or_cancel_job`, `requeue_job`, `list_accounts`, `manager_status`. Any
MCP-capable agent (OpenCode, Claude Desktop, Cursor, …) can drive the whole
manager through these tools. See `mcp/README.md` for setup and the full tool
list. The server holds `ADMIN_PASSWORD` internally; agents never see the
password or session cookie.

---

## Job lifecycle & restart safety

```
queued → running → success
            │        └ failed (Kaggle error)
            ├─ server restart → interrupted  (requeue to retry)
            ├─ repeated polling failures → uncertain (auto-retries after 60s)
            └─ user stop → stopping → cancelled (outputs preserved)
```

- Everything is stored in Postgres (Neon), so an execution survives restarts and redeploys.
- On boot the worker reconciles: any job left in `running`/`stopping` becomes
  `interrupted` with a log entry. You can requeue it from the dashboard or via
  the MCP `requeue_job` tool.
- **Stopping a running job:** the Kaggle API has no abort endpoint, so stop
  works by first snapshotting the current outputs, then pushing an immediate-exit
  "stub" version of the notebook. The job flips to `stopping`, ends as
  `cancelled` once Kaggle reaches a terminal state, and the pre-stop outputs stay
  downloadable.
- Queued/uncertain jobs can be cancelled instantly (no stub push needed).

---

## Notebooks

- Notebooks live as local `.ipynb` definitions (title, description, code,
  accelerator, internet access, cron schedule). Editing them never touches Kaggle.
- Each notebook also selects which registered Kaggle account runs it (default =
  env fallback).
- **Run** enqueues a job. When it reaches the front of the queue the worker:
  1. creates the notebook on Kaggle if missing (private draft),
  2. pushes a new version (this queues a remote execution — equivalent to
     *Save & Run*), with the selected accelerator (cpu / gpu_t4 / gpu_a100 /
     tpu_v3_8 / …) and internet toggle applied to the push metadata,
  3. polls `status` until `complete`/`error`,
  4. downloads and extracts the output zip to `data/outputs/job-<id>/`.
- Logs from every phase are recorded per job and viewable in the dashboard.

### Reference alignment

The Kaggle client (`app/kaggle_client.py`) follows the push mechanics of
[barryallen16/kaggle-automation](https://github.com/barryallen16/kaggle-automation):

- kernels are keyed by their **slugified title**; the push metadata `id` is the
  string ref `user/slug` (no numeric-id discovery),
- sources are normalized to an executable `.ipynb` before upload,
- 409-conflict push retries with backoff (5s / 10s / 20s),
- auth supports Basic (username:key), the `X-Kaggle-Authorization` bearer
  header, and a `KAGGLE_API_TOKEN` bearer fallback (env var),
- stopping a running kernel = pushing an immediate-exit stub (outputs
  snapshotted first).

---

## Repository layout

```
app/
  main.py            FastAPI app: routes, auth, static mount, bootstrap
  config.py          paths, constants, env defaults
  db.py              Postgres layer (schema, helpers)
  security.py        scrypt passwords, session tokens, AES-GCM secret encryption
  scheduler.py       minimal 5-field cron matcher
  kaggle_client.py   Kaggle Kernels API v1 client (httpx)
  worker.py          background worker: queue, polling, reconcile, cron firing
static/
  index.html         dashboard shell
  css/app.css        design system
  js/api.js, ui.js, app.js, views/*.js
tests/
  test_scheduler.py  test_security.py  test_config_accounts.py  test_worker.py
android/
  native Android app (Kotlin + Jetpack Compose) — see android/README.md
mcp/
  MCP server (agent router) for the manager — see mcp/README.md
render.yaml          Render blueprint
requirements.txt     fastapi, uvicorn, httpx, cryptography, psycopg2-binary
requirements-dev.txt  embedded-postgres (offline test DB)
```

---

## Run locally (when you're ready to use it)

```bash
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt
set ADMIN_PASSWORD=your-password                 # optional; else generated
set DATABASE_URL=postgresql://user:password@host:5432/kaggle_manager
uvicorn app.main:app --reload --port 8000
# open http://localhost:8000
```

Tests (all offline, no external calls — they boot a throwaway real Postgres
via `embedded-postgres`):

```bash
pip install -r requirements-dev.txt
python -m unittest discover tests -v
```

---

## Deploy to Render (free tier)

**1. Push the code to GitHub / GitLab / Bitbucket.** The repo must contain
`render.yaml` at its root. This app has no build step and needs no services other
than the web service.

**2. Create the service on Render** — either way works:

- *Blueprint (recommended):* Render → **New → Blueprint** → select the repo.
  `render.yaml` is read automatically and the service is created.
- *Manual web service:* Render → **New → Web Service** → select the repo and set:
  - Runtime: **Python 3**
  - Build command: `pip install -r requirements.txt`
  - Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
  - Health check path: `/healthz`
  - Instance type: **Free**

**3. Set environment secrets** (Render → your service → Environment):

| Variable | Needed? | What it does |
|---|---|---|
| `ADMIN_PASSWORD` | **yes** | Dashboard login password. If unset, a random one is written to `data/admin_bootstrap.txt` on first boot. |
| `DATABASE_URL` | **yes** | Postgres connection string (e.g. from Neon). The app uses Postgres, not SQLite — on Render's free tier this is the *only* durable storage. |
| `KAGGLE_ACCOUNTS` | **for Kaggle** | JSON array of accounts: `[{"name":"Account A","username":"u1","key":"k1"},...]`. IDs are 1-based by list order; the first is the default. This is the **only** way to configure Kaggle credentials — the dashboard has no credential entry. |
| `KAGGLE_USERNAME` / `KAGGLE_KEY` | optional | Legacy fallback: single account used when `KAGGLE_ACCOUNTS` is unset/invalid. |
| `KAGGLE_API_TOKEN` | optional | Bearer-token fallback when no username/key pair is configured. |

**4. Deploy** → wait for the build to finish, then open the service URL.

**5. First boot (one-time):**
1. Sign in with your admin password.
2. Set `KAGGLE_ACCOUNTS` (both accounts) in **Environment** — the dashboard
   shows them read-only under **Settings → Kaggle accounts**.
3. Create a notebook: **Notebooks → New notebook** → pick which Kaggle account
   should run it.
4. Press **Run** — watch it appear in **Jobs**, then follow the logs.
5. (Optional) Connect the MCP server to your agent — see `mcp/README.md`.

### Two Kaggle accounts

- Accounts come **only** from environment variables (`KAGGLE_ACCOUNTS` JSON
  list, or the legacy `KAGGLE_USERNAME`/`KAGGLE_KEY` pair). Nothing is stored
  in the app database — changes survive restarts and redeploys.
- Every notebook selects its account in the editor (default = first env
  account).
- Jobs inherit the notebook's account and **keep polling under that account**
  even if the env list later changes (they finish normally).
- Only one execution runs at a time globally; the queue is cross-account.

## Deployment checklist — what still needs you (authorization)

I built everything with **no external calls made from your machine**. Before it
can operate for real, *you* must authorize the following (all via the dashboard
after deploy):

- [ ] Set `ADMIN_PASSWORD` (or read the generated one).
- [ ] Set `KAGGLE_ACCOUNTS` with your **Kaggle credentials** (env-only — see
      "Deploy to Render").
- [ ] (Optional) Connect the **MCP server** to your agent — see `mcp/README.md`.
- [ ] First live run: submit a small notebook and verify the Kaggle API shapes.
      The client in `app/kaggle_client.py` isolates every endpoint so any
      response-shape mismatch is fixable in one file. Likely behaviors to note:
      - push response is empty on success; status poll returns
        `queued/running/complete/error`; output returns a zip.
      - creating a notebook first (private draft) then pushing a new version
        (run) is the supported flow.
- [ ] Check the dashboard's engine dot and job logs reflect state correctly.

---

## Limitations (documented, not hidden)

- Stopping a running job uses the stub-push trick (no official abort endpoint);
  it takes as long as Kaggle takes to finalize the stub version.
- Kaggle execution queues can be slow; polls are rate-limit friendly (default
  every 6s).
- Free tier sleeps after inactivity (~15 min); cold start takes ~1 min. The
  worker resumes automatically on wake.
- Multiple registered Kaggle accounts supported (each notebook picks one);
  still a single admin user. No multi-tenancy.
- The Logs feed is capped at 2000 lines per job to protect the free disk.
