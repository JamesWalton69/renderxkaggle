# Kaggle Cloud Manager — MCP server (agent router)

An [MCP](https://modelcontextprotocol.io) server that turns the Kaggle Cloud
Manager's REST API into tools. Any MCP-capable agent (Claude Desktop, OpenCode,
Cursor, …) can then do Kaggle work through the manager: run/stop notebooks,
watch jobs, and approve/reject the assistant's actions.

The server holds the admin password (`ADMIN_PASSWORD` env) and handles login →
`kc_session` cookie internally (refresh on 401). Agents never see credentials.

## Tools

| Tool | What it does |
|---|---|
| `manager_status` | Engine state, counts, active job, pending approvals, recent jobs |
| `list_notebooks` | All notebooks + last-run status |
| `create_notebook` | Create a notebook (title, code, accelerator, internet) |
| `run_notebook` | Queue a run → returns job id |
| `list_jobs` | Jobs, optionally filtered by status |
| `get_job` | Job detail including logs |
| `stop_or_cancel_job` | Cancel queued/uncertain, or stop running/stopping (stub push) |
| `requeue_job` | Requeue a terminal job |
| `list_approvals` | Pending AI-assistant approvals |
| `decide_approval` | Approve/reject an approval |

The manager's own assistant chat is deliberately not exposed — the connecting
agent *is* the intelligence; approvals stay gated through the tools above.

## Run locally (stdio — for Claude Desktop / OpenCode / etc.)

```powershell
cd mcp
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
$env:ADMIN_PASSWORD = "your-admin-password"
$env:MANAGER_URL = "https://renderxkaggle.onrender.com"   # optional, default
python -m mcp_server
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "kaggle-manager": {
      "command": "python",
      "args": ["-m", "mcp_server"],
      "cwd": "E:\\Coding\\Projects\\kagglexrender\\mcp",
      "env": { "ADMIN_PASSWORD": "your-admin-password" }
    }
  }
}
```

OpenCode:

```bash
opencode mcp add kaggle-manager -- python -m mcp_server --cwd /path/to/mcp
# ADMIN_PASSWORD via environment of the shell that launches opencode
```

## Run remotely (Streamable HTTP — Render or any host)

```powershell
python -m mcp_server.serve_http   # listens on $PORT or 8000
```

Render blueprint (add to your service or a new one):

- **Build:** `pip install -r mcp/requirements.txt`
- **Start:** `python -m mcp_server.serve_http`
- **Env:** `ADMIN_PASSWORD` (required), `MANAGER_URL` (optional), `PORT` (auto)
- Then point your MCP client at `https://<your-service>.onrender.com` (MCP
  clients that support Streamable HTTP / SSE connect to the root URL).

## Notes

- Timeouts: connect 20s (Render cold-start allowance), read 60s per call —
  stop/cancel can take a few seconds while outputs are snapshotted.
- Errors: backend `detail` strings are surfaced verbatim (e.g. a 409 on stop
  before the run reached Kaggle) — the agent can decide to retry.
- The manager keeps sessions for 30 days; the server re-logs-in automatically
  if a session is rejected.
