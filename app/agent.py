"""Autonomous AI assistant for the Kaggle Cloud Manager.

Design notes
------------
- Uses any OpenAI-compatible `/chat/completions` endpoint (base URL + key +
  model are user-provided via dashboard Settings).
- The model plans freely and emits JSON actions. It is NOT given native
  function-calling, so non-OpenAI providers work identically. The executor
  validates every action against the static schema below.
- Sensitive actions (submit/cancel/delete/schedule) are gated by the executor,
  never by the model: a "pending approval" record is created and the agent
  pauses until a human decides. This gate lives in code and cannot be
  influenced by LLM output.
- All conversation + pending state live in SQLite, so approvals survive
  restarts and the agent resumes where it left off.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Optional

from . import config, db, scheduler, security
from .kaggle_client import ACCELERATORS as ACL_ACCELERATORS
from .worker import stop_job

MAX_TOOL_RESULT_CHARS = 3000

class AgentError(Exception):
    pass


class AgentConfigError(AgentError):
    pass


class AgentLLMError(AgentError):
    pass


# ------------------------------------------------------------------ tool schema

TOOL_SCHEMA: dict[str, dict] = {
    "list_notebooks": {
        "desc": "List all notebooks in this manager (id, title, slug, schedule, last run).",
        "params": {},
        "sensitive": False,
    },
    "get_notebook": {
        "desc": "Show details of one notebook, including its current code (truncated).",
        "params": {"id": "notebook id (int)"},
        "sensitive": False,
    },
    "create_notebook": {
        "desc": "Create a new notebook locally (not submitted to Kaggle yet).",
        "params": {"title": "title", "description": "optional description", "code": "optional python code",
                   "accelerator": "optional: auto|cpu|gpu_t4|gpu_t4_x2|gpu_p100|gpu_v100|gpu_l4|gpu_a100|gpu_h100|tpu_v3_8",
                   "internet": "optional bool: enable internet/dataset access (default true)"},
        "sensitive": False,
    },
    "update_notebook_source": {
        "desc": "Edit a notebook's code stored locally (does not touch Kaggle).",
        "params": {"id": "notebook id", "code": "new code", "title": "optional new title", "description": "optional new description",
                   "accelerator": "optional accelerator key (see create)", "internet": "optional bool"},
        "sensitive": False,
    },
    "submit_notebook": {
        "desc": "Queue a Kaggle execution of a notebook (it will run remotely). Requires approval.",
        "params": {"id": "notebook id", "comment": "optional comment"},
        "sensitive": True,
    },
    "cancel_job": {
        "desc": "Cancel a queued/uncertain job, or STOP a running one by pushing an immediate-exit stub (outputs are snapshotted first). Requires approval.",
        "params": {"job_id": "job id (int)"},
        "sensitive": True,
    },
    "requeue_job": {
        "desc": "Put an interrupted/failed/uncertain job back in the queue to retry. Requires approval.",
        "params": {"job_id": "job id (int)"},
        "sensitive": True,
    },
    "delete_notebook": {
        "desc": "Delete a notebook from this manager (local only; nothing is removed from Kaggle). Requires approval.",
        "params": {"id": "notebook id"},
        "sensitive": True,
    },
    "update_schedule": {
        "desc": "Set or clear a cron schedule for a notebook (e.g. '0 9 * * 1' = Mondays 09:00 UTC). Requires approval.",
        "params": {"id": "notebook id", "cron": "5-field cron string, or '' to disable"},
        "sensitive": True,
    },
    "get_job_status": {
        "desc": "Show the current status of a job.",
        "params": {"job_id": "job id (int)"},
        "sensitive": False,
    },
    "list_jobs": {
        "desc": "List recent jobs, optionally filtered by status (queued/running/success/failed/cancelled/interrupted/uncertain).",
        "params": {"status": "optional status filter", "limit": "optional max rows (default 20)"},
        "sensitive": False,
    },
    "get_job_logs": {
        "desc": "Return the tail of a job's execution log.",
        "params": {"job_id": "job id", "limit": "optional number of lines (default 100)"},
        "sensitive": False,
    },
    "get_overview": {
        "desc": "Return dashboard statistics (job counts, success rate, active job, recent runs).",
        "params": {},
        "sensitive": False,
    },
    "list_approvals": {
        "desc": "List approval requests awaiting a decision.",
        "params": {"status": "optional: pending (default)"},
        "sensitive": False,
    },
    "get_settings_summary": {
        "desc": "Show the current AI provider and Kaggle configuration state (secrets never shown).",
        "params": {},
        "sensitive": False,
    },
}

SENSITIVE_TOOLS = {name for name, spec in TOOL_SCHEMA.items() if spec["sensitive"]}

SYSTEM_PROMPT = f"""You are the AI operations assistant for "Kaggle Cloud Manager", a private
remote controller that runs Kaggle notebook executions on behalf of the owner,
using one authorized Kaggle identity. You operate a small web platform.

CAPABILITIES (call these tools; you decide the plan and order):
{json.dumps(TOOL_SCHEMA, indent=1)}

RULES
- You respond with exactly one JSON object per turn. Use either:
    {{"answer": "..."}}                       (final reply to the user)
    {{"action": {{"tool": "...", "params": {{...}}, "reason": "..."}}}}
    {{"actions": [ ...same shape... ]}}       (several steps at once)
- To inspect results of an action you just took, continue in the next turn;
  tool results are provided to you as context after each action.
- SENSITIVE actions (submit_notebook, cancel_job, requeue_job,
  delete_notebook, update_schedule) ALWAYS require human approval: issue the
  action with a clear "reason"; the system will pause and ask the owner. Do
  not treat that as failure — wait for the outcome in a later turn.
- Plan before acting: check the current state (list_jobs / get_overview /
  list_notebooks) before submitting or retrying. Do not submit a notebook
  twice for the same goal.
- Only one execution runs at a time; extra submissions queue up automatically.
- Never invent tool names, IDs, or results. If a result says an action needs
  approval or failed, adapt.
- Never expose credentials or secrets in answers.
- Be concise and friendly. Final answers summarize what you did and what
  happened or what awaits approval."""


# ------------------------------------------------------------------ helpers

def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    if not slug:
        slug = "notebook"
    return slug[:60]


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise AgentError("model reply did not contain a JSON object")
    try:
        obj = json.loads(text[start : end + 1])
    except ValueError as exc:
        raise AgentError(f"model reply was not valid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise AgentError("model reply JSON was not an object")
    return obj


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------ LLM client

def llm_chat(cfg: dict, messages: list[dict]) -> str:
    import httpx
    base = (cfg.get("ai_base_url") or "").rstrip("/")
    key = cfg.get("ai_api_key") or ""
    model = cfg.get("ai_model") or ""
    if not base or not key or not model:
        raise AgentConfigError(
            "AI provider is not configured. Add base URL, API key and model in "
            "Settings, then try again."
        )
    try:
        temperature = float(cfg.get("ai_temperature", 0.2))
    except (TypeError, ValueError):
        temperature = 0.2
    body = {
        "model": model,
        "messages": messages,
        "temperature": max(0.0, min(temperature, 1.5)),
        "max_tokens": 1200,
    }
    try:
        resp = httpx.post(
            f"{base}/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {key}"},
            timeout=config.LLM_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise AgentLLMError(f"AI provider request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise AgentLLMError(
            f"AI provider returned HTTP {resp.status_code}: {resp.text[:300]}"
        )
    try:
        payload = resp.json()
        content = payload["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError) as exc:
        raise AgentLLMError(f"unexpected AI provider response: {resp.text[:300]}") from exc
    if not content or not content.strip():
        raise AgentLLMError("AI provider returned an empty reply")
    return content


# ------------------------------------------------------------------ agent

class Agent:
    def __init__(self, facade, db_path=None, ai_settings_provider=None):
        self.facade = facade
        self.db_path = db_path
        self._ai_provider = ai_settings_provider

    # -- settings ---------------------------------------------------------

    def _ai_settings(self) -> dict:
        if self._ai_provider:
            return self._ai_provider()
        key_enc = db.get_setting("ai_api_key", "", db_path=self.db_path)
        try:
            key = security.decrypt_secret(key_enc) if key_enc else ""
        except Exception:
            key = ""
        return {
            "ai_base_url": db.get_setting("ai_base_url", "", db_path=self.db_path),
            "ai_api_key": key,
            "ai_model": db.get_setting("ai_model", "", db_path=self.db_path),
            "ai_temperature": db.get_setting("ai_temperature", 0.2, db_path=self.db_path),
        }

    # -- conversation helpers ---------------------------------------------

    def _history(self) -> list[dict]:
        return db.fetch_all(
            "SELECT role, content, meta FROM chat_messages ORDER BY id DESC LIMIT ?",
            (config.MAX_CHAT_HISTORY,),
            db_path=self.db_path,
        )[::-1]

    def _append(self, role: str, content: str, meta: Optional[dict] = None) -> None:
        db.execute(
            "INSERT INTO chat_messages(role, content, ts, meta) VALUES(?,?,?,?)",
            (role, content, _now(), json.dumps(meta or {})),
            db_path=self.db_path,
        )

    def _llm_messages(self) -> list[dict]:
        msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
        for m in self._history():
            role, content = m["role"], m["content"]
            if role == "note":
                try:
                    meta = json.loads(m.get("meta") or "{}")
                except (ValueError, TypeError):
                    meta = {}
                tag = meta.get("tool", "context") if isinstance(meta, dict) else "context"
                msgs.append({"role": "user", "content": f"[{tag} result] {content}"})
            else:
                msgs.append({"role": role, "content": content})
        return msgs

    # -- public entry points ----------------------------------------------

    def run(self, text: str) -> dict:
        """Handle a new user message. Returns {"answer":..., "pending_approval":...}."""
        if not text or not text.strip():
            raise AgentError("message is empty")
        self._append("user", text.strip())
        return self._agent_loop()

    def resume(self, approval_id: int, approve: bool, decided_by: str = "admin") -> dict:
        """Called after a human decides a pending approval. Executes the action
        if approved, then lets the agent continue."""
        approval = db.fetch_one(
            "SELECT * FROM approvals WHERE id=?", (approval_id,), db_path=self.db_path
        )
        if not approval:
            raise AgentError("approval not found")
        if approval["status"] != "pending":
            raise AgentError("approval was already decided")
        db.execute(
            "UPDATE approvals SET status=?, decided_at=?, decided_by=?, result_note=? WHERE id=?",
            (
                "approved" if approve else "rejected",
                _now(),
                decided_by,
                "executed" if approve else "not executed",
                approval_id,
            ),
            db_path=self.db_path,
        )
        try:
            params = json.loads(approval["params"] or "{}")
        except ValueError:
            params = {}
        if approve:
            result = self._execute(approval["tool"], params)
            note = (
                f"Approval for '{approval['tool']}' was granted and the action ran. "
                f"Result: {result}"
            )
        else:
            note = (
                f"The owner REJECTED '{approval['tool']}' (params: "
                f"{json.dumps(params)[:200]}). It was NOT executed. Adjust your plan."
            )
        self._append("note", note[:4000], {"tool": approval["tool"]})
        return self._agent_loop()

    # -- core loop ----------------------------------------------------------

    def _agent_loop(self) -> dict:
        for turn in range(config.MAX_AGENT_TURNS):
            raw = llm_chat(self._ai_settings(), self._llm_messages())
            try:
                obj = _extract_json(raw)
            except AgentError as exc:
                if turn == config.MAX_AGENT_TURNS - 1:
                    raise
                self._append("note", f"reply parse issue: {exc}", {"tool": "_parse"})
                continue

            answer = obj.get("answer")
            action = obj.get("action")
            actions = obj.get("actions") if isinstance(obj.get("actions"), list) else None
            pending = obj.get("pending_approval")

            if not action and not actions:
                if answer:
                    self._append("assistant", str(answer)[:8000])
                    return {"answer": str(answer), "pending_approval": None, "turns": turn + 1}
                if pending:
                    continue
                if turn == config.MAX_AGENT_TURNS - 1:
                    break
                self._append("note", "no action or answer in reply; retry", {"tool": "_parse"})
                continue

            to_run = [action] if action else actions
            for act in to_run:
                if not isinstance(act, dict) or "tool" not in act:
                    self._append("note", "malformed action block; retry", {"tool": "_parse"})
                    continue
                tool = str(act.get("tool", ""))
                if tool not in TOOL_SCHEMA:
                    self._append(
                        "note",
                        f"unknown tool '{tool}'. Available: {', '.join(TOOL_SCHEMA)}",
                        {"tool": tool},
                    )
                    continue
                params = act.get("params") or {}
                if not isinstance(params, dict):
                    params = {}
                reason = str(act.get("reason", ""))[:500]

                if tool in SENSITIVE_TOOLS:
                    approval_id = db.execute(
                        "INSERT INTO approvals(status, tool, params, reason, created_at) "
                        "VALUES('pending',?,?,?,?)",
                        (tool, json.dumps(params), reason, _now()),
                        db_path=self.db_path,
                    )
                    self._append(
                        "assistant",
                        f"⏸ Waiting for your approval: {tool} "
                        f"({json.dumps(params)[:200]}). Reason: {reason or 'not stated'}",
                        {"tool": tool, "approval_id": approval_id},
                    )
                    return {
                        "answer": None,
                        "pending_approval": approval_id,
                        "message": f"Approval required for '{tool}'",
                    }

                result = self._execute(tool, params)
                self._append("note", result, {"tool": tool})

            if not actions:  # single action executed; let model continue planning
                continue
        raise AgentError(
            "Agent reached its turn limit without a final answer. Please be more "
            "specific or approve pending actions."
        )

    # -- executor -----------------------------------------------------------

    def _execute(self, tool: str, params: dict) -> str:
        handler = getattr(self, f"_tool_{tool}", None)
        if handler is None:
            return f"tool '{tool}' has no implementation"
        try:
            result = handler(params)
        except AgentError as exc:
            return f"error: {exc}"
        except Exception as exc:  # defensive: never kill the loop on tool bugs
            return f"error: unexpected {type(exc).__name__}: {exc}"
        text = result if isinstance(result, str) else json.dumps(result, default=str)
        return text[:MAX_TOOL_RESULT_CHARS]

    def _nb(self, notebook_id: int) -> dict:
        row = db.fetch_one(
            "SELECT * FROM notebooks WHERE id=?", (int(notebook_id),), db_path=self.db_path
        )
        if not row:
            raise AgentError(f"notebook {notebook_id} does not exist")
        return row

    # -- tool implementations ----------------------------------------------

    def _tool_list_notebooks(self, params: dict) -> str:
        rows = db.fetch_all(
            "SELECT id, title, slug, schedule, enabled, last_run_at FROM notebooks "
            "ORDER BY updated_at DESC LIMIT 100",
            db_path=self.db_path,
        )
        if not rows:
            return "No notebooks yet. Use create_notebook to make one."
        lines = []
        for r in rows:
            lines.append(
                f"- {r['id']}: {r['title']} (slug={r['slug']}, schedule={r['schedule'] or 'off'}, "
                f"enabled={bool(r['enabled'])}, last run={r['last_run_at'] or 'never'})"
            )
        return "\n".join(lines)

    def _tool_get_notebook(self, params: dict) -> str:
        nb = self._nb(params.get("id"))
        source = nb.get("source") or "{}"
        try:
            nb_json = json.loads(source)
            code = ""
            for cell in nb_json.get("cells", []):
                if cell.get("cell_type") == "code":
                    code += "".join(cell.get("source", []))
        except (ValueError, AttributeError):
            code = source
        return (
            f"notebook {nb['id']} «{nb['title']}» slug={nb['slug']}\n"
            f"description: {nb.get('description','')}\n"
            f"schedule: {nb.get('schedule') or 'off'} (enabled={bool(nb.get('enabled'))})\n"
            f"code ({len(code)} chars):\n{code[:2500]}"
        )

    def _tool_create_notebook(self, params: dict) -> str:
        title = str(params.get("title", "")).strip()
        if not title:
            raise AgentError("title is required")
        slug = _slugify(title)
        if db.fetch_one("SELECT 1 FROM notebooks WHERE slug=?", (slug,), db_path=self.db_path):
            slug = f"{slug}-{datetime.now().strftime('%H%M%S')}"
        code = params.get("code") or ""
        accelerator = str(params.get("accelerator", "auto")).strip().lower() or "auto"
        if accelerator not in ACL_ACCELERATORS:
            raise AgentError(f"unknown accelerator '{accelerator}'")
        internet = 1 if params.get("internet", True) else 0
        cells = [
            {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
             "source": (code.splitlines(keepends=True) if code else ["print('Hello from Kaggle Cloud Manager')"])}
        ]
        source = json.dumps({
            "cells": cells,
            "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python",
                                        "name": "python3"}},
            "nbformat": 4, "nbformat_minor": 5,
        })
        now = _now()
        nid = db.execute(
            "INSERT INTO notebooks(slug, title, description, source, accelerator, internet, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (slug, title, str(params.get("description", "")), source, accelerator, internet, now, now),
            db_path=self.db_path,
        )
        return f"created notebook id={nid} slug={slug} title={title!r} accelerator={accelerator} (stored locally; submit it to run)"

    def _tool_update_notebook_source(self, params: dict) -> str:
        nb = self._nb(params.get("id"))
        updates, values = [], []
        if params.get("code") is not None:
            code = str(params["code"])
            try:
                src = json.loads(nb["source"] or "{}")
                if isinstance(src, dict) and "cells" in src:
                    src["cells"] = [
                        {"cell_type": "code", "execution_count": None, "metadata": {},
                         "outputs": [], "source": code.splitlines(keepends=True)}
                    ]
                else:
                    src = {"cells": [
                        {"cell_type": "code", "execution_count": None, "metadata": {},
                         "outputs": [], "source": code.splitlines(keepends=True)}]}
            except (ValueError, AttributeError):
                src = {"cells": [
                    {"cell_type": "code", "execution_count": None, "metadata": {},
                     "outputs": [], "source": code.splitlines(keepends=True)}]}
            updates.append("source=?")
            values.append(json.dumps(src))
        if params.get("title"):
            updates.append("title=?")
            values.append(str(params["title"]).strip())
        if params.get("description") is not None:
            updates.append("description=?")
            values.append(str(params["description"]))
        if "accelerator" in params:
            accelerator = str(params["accelerator"]).strip().lower()
            if accelerator not in ACL_ACCELERATORS:
                raise AgentError(f"unknown accelerator '{accelerator}'")
            updates.append("accelerator=?")
            values.append(accelerator)
        if "internet" in params:
            updates.append("internet=?")
            values.append(1 if params["internet"] else 0)
        if not updates:
            raise AgentError("nothing to update (provide code/title/description/accelerator/internet)")
        updates.append("updated_at=?")
        values.append(_now())
        values.append(int(params["id"]))
        db.execute(f"UPDATE notebooks SET {', '.join(updates)} WHERE id=?", values, db_path=self.db_path)
        return f"updated notebook {int(params['id'])} locally (not yet pushed to Kaggle)."

    def _tool_submit_notebook(self, params: dict) -> str:
        nb = self._nb(params.get("id"))
        jid = db.enqueue_job(nb["id"], kind="agent", db_path=self.db_path)
        db.execute(
            "UPDATE jobs SET meta=? WHERE id=?",
            (json.dumps({"notebook_title": nb["title"], "notebook_slug": nb["slug"],
                         "comment": str(params.get("comment", ""))}), jid),
            db_path=self.db_path,
        )
        return (
            f"queued job {jid} for notebook «{nb['title']}» (slug={nb['slug']}). "
            f"It will run as soon as no other execution is active."
        )

    def _tool_cancel_job(self, params: dict) -> str:
        job = db.fetch_one("SELECT * FROM jobs WHERE id=?", (int(params.get("job_id", 0)),), db_path=self.db_path)
        if not job:
            raise AgentError("job not found")
        if job["status"] in (config.JOB_QUEUED, config.JOB_UNCERTAIN):
            db.execute(
                "UPDATE jobs SET status=?, finished_at=?, error='cancelled by user' WHERE id=?",
                (config.JOB_CANCELLED, _now(), job["id"]),
                db_path=self.db_path,
            )
            db.add_job_log(job["id"], "job cancelled by user", db_path=self.db_path)
            return f"job {job['id']} cancelled."
        if job["status"] in (config.JOB_RUNNING, config.JOB_STOPPING):
            try:
                message = stop_job(self.facade, job["id"], db_path=self.db_path)
            except ValueError as exc:
                raise AgentError(str(exc))
            return message
        raise AgentError(f"job is {job['status']} — cannot cancel")

    def _tool_requeue_job(self, params: dict) -> str:
        job = db.fetch_one("SELECT * FROM jobs WHERE id=?", (int(params.get("job_id", 0)),), db_path=self.db_path)
        if not job:
            raise AgentError("job not found")
        if job["status"] in config.ACTIVE_STATUSES:
            raise AgentError(f"job is {job['status']} — not requeueable yet")
        db.execute(
            "UPDATE jobs SET status=?, error='', finished_at=NULL WHERE id=?",
            (config.JOB_QUEUED, job["id"]),
            db_path=self.db_path,
        )
        db.add_job_log(job["id"], "job requeued by user", db_path=self.db_path)
        return f"job {job['id']} requeued; it will retry when the queue is free."

    def _tool_delete_notebook(self, params: dict) -> str:
        nb = self._nb(params.get("id"))
        active = db.fetch_one(
            "SELECT 1 FROM jobs WHERE notebook_id=? AND status IN ('queued','running','uncertain') LIMIT 1",
            (nb["id"],),
            db_path=self.db_path,
        )
        if active:
            raise AgentError("notebook has an active or queued job; cannot delete it now")
        db.execute("DELETE FROM notebooks WHERE id=?", (nb["id"],), db_path=self.db_path)
        return f"deleted notebook {nb['id']} «{nb['title']}» locally (Kaggle copy untouched)."

    def _tool_update_schedule(self, params: dict) -> str:
        nb = self._nb(params.get("id"))
        cron = str(params.get("cron", "")).strip()
        err = scheduler.cron_is_valid(cron)
        if err:
            raise AgentError(err)
        db.execute("UPDATE notebooks SET schedule=?, updated_at=? WHERE id=?", (cron, _now(), nb["id"]), db_path=self.db_path)
        return f"schedule for «{nb['title']}» is now {cron or 'off'} (UTC)."

    def _tool_get_job_status(self, params: dict) -> str:
        job = db.fetch_one("SELECT * FROM jobs WHERE id=?", (int(params.get("job_id", 0)),), db_path=self.db_path)
        if not job:
            raise AgentError("job not found")
        meta = job.get("meta") or "{}"
        title = ""
        try:
            title = json.loads(meta).get("notebook_title", "")
        except ValueError:
            pass
        return (
            f"job {job['id']} [{title or 'notebook deleted'}]: status={job['status']}, "
            f"kaggle={job['kaggle_status'] or 'n/a'}, "
            f"created={job['created_at']}, started={job['started_at'] or '-'}, "
            f"finished={job['finished_at'] or '-'}, error={job['error'] or 'none'}"
        )

    def _tool_list_jobs(self, params: dict) -> str:
        status = str(params.get("status", "")).strip()
        try:
            limit = max(1, min(int(params.get("limit", 20)), 100))
        except (TypeError, ValueError):
            limit = 20
        if status:
            rows = db.fetch_all(
                "SELECT id, notebook_id, kind, status, kaggle_status, created_at, finished_at "
                "FROM jobs WHERE status=? ORDER BY id DESC LIMIT ?",
                (status, limit),
                db_path=self.db_path,
            )
        else:
            rows = db.fetch_all(
                "SELECT id, notebook_id, kind, status, kaggle_status, created_at, finished_at "
                "FROM jobs ORDER BY id DESC LIMIT ?",
                (limit,),
                db_path=self.db_path,
            )
        if not rows:
            return "no jobs found"
        return "\n".join(
            f"- {r['id']}: notebook={r['notebook_id']}, kind={r['kind']}, status={r['status']}, "
            f"kaggle={r['kaggle_status'] or '-'}, created={r['created_at']}, "
            f"finished={r['finished_at'] or '-'}"
            for r in rows
        )

    def _tool_get_job_logs(self, params: dict) -> str:
        job_id = int(params.get("job_id", 0))
        job = db.fetch_one("SELECT id FROM jobs WHERE id=?", (job_id,), db_path=self.db_path)
        if not job:
            raise AgentError("job not found")
        try:
            limit = max(1, min(int(params.get("limit", 100)), 500))
        except (TypeError, ValueError):
            limit = 100
        rows = db.fetch_all(
            "SELECT ts, line FROM job_logs WHERE job_id=? ORDER BY id DESC LIMIT ?",
            (job_id, limit),
            db_path=self.db_path,
        )[::-1]
        if not rows:
            return f"job {job_id}: no log lines yet."
        return "\n".join(f"[{r['ts']}] {r['line']}" for r in rows)[:MAX_TOOL_RESULT_CHARS]

    def _tool_get_overview(self, params: dict) -> str:
        rows = db.fetch_all(
            "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status", db_path=self.db_path
        )
        counts = {r["status"]: r["n"] for r in rows}
        total = sum(counts.values())
        done = counts.get("success", 0) + counts.get("failed", 0)
        rate = round(100 * counts.get("success", 0) / done) if done else None
        active = db.fetch_one(
            "SELECT id, status FROM jobs WHERE status IN ('queued','running','uncertain') "
            "ORDER BY id LIMIT 1",
            db_path=self.db_path,
        )
        nb_count = db.fetch_one("SELECT COUNT(*) AS n FROM notebooks", db_path=self.db_path)
        return (
            f"notebooks={nb_count['n']}, total jobs={total}, "
            f"success={counts.get('success',0)} failed={counts.get('failed',0)} "
            f"cancelled={counts.get('cancelled',0)} interrupted={counts.get('interrupted',0)} "
            f"uncertain={counts.get('uncertain',0)} queued={counts.get('queued',0)} "
            f"running={counts.get('running',0)}; success rate={rate if rate is not None else 'n/a'}%; "
            f"next active={active['id'] if active else 'none'}"
        )

    def _tool_list_approvals(self, params: dict) -> str:
        status = str(params.get("status", "pending")).strip() or "pending"
        rows = db.fetch_all(
            "SELECT id, tool, params, reason, created_at FROM approvals WHERE status=? "
            "ORDER BY id DESC LIMIT 20",
            (status,),
            db_path=self.db_path,
        )
        if not rows:
            return f"no {status} approvals"
        return "\n".join(
            f"- #{r['id']} {r['tool']} params={r['params'][:200]} reason={r['reason'][:120]} ({r['created_at']})"
            for r in rows
        )

    def _tool_get_settings_summary(self, params: dict) -> str:
        cfg = self._ai_settings()
        has_key = bool(cfg.get("ai_api_key"))
        kaggle_username = db.get_setting("kaggle_username", "", db_path=self.db_path)
        return (
            f"AI: base={cfg.get('ai_base_url') or 'not set'}, model={cfg.get('ai_model') or 'not set'}, "
            f"key={'configured' if has_key else 'NOT set'}, temp={cfg.get('ai_temperature')};\n"
            f"Kaggle identity: {kaggle_username or 'NOT configured (env fallback)'}"
        )
