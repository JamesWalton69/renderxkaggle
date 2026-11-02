"""Agent logic tests — offline, no external calls.

The LLM call is patched with scripted JSON replies, so we verify the agent's
decision loop, tool execution, and the hard approval gate without any network.
"""
import json
import tempfile
import unittest
from pathlib import Path

from app import agent as agent_mod
from app import config, db
from app.agent import Agent


class FakeFacade:
    def __init__(self):
        self.runs = []

    def ensure_notebook(self, nb):
        return f"fake/{nb['slug']}"

    def run_notebook(self, nb):
        self.runs.append(nb["slug"])
        return f"fake/{nb['slug']}"

    def get_status(self, ref):
        return {"status": "complete", "message": "", "failure_message": ""}

    def download_output(self, ref, dest):
        return []


class AgentTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        db.init_db(self.db_path)
        self.facade = FakeFacade()
        self.agent = Agent(self.facade, db_path=self.db_path, ai_settings_provider=lambda: {
            "ai_base_url": "https://fake.local/v1",
            "ai_api_key": "k",
            "ai_model": "m",
            "ai_temperature": 0.2,
        })
        self._orig_llm = agent_mod.llm_chat
        self.replies = []
        agent_mod.llm_chat = self._fake_llm

    def tearDown(self):
        agent_mod.llm_chat = self._orig_llm
        self.tmp.cleanup()

    def _fake_llm(self, cfg, messages):
        if not self.replies:
            raise AssertionError("no scripted LLM reply left")
        return self.replies.pop(0)

    def _seed_notebook(self, slug="demo"):
        now = db.now_iso()
        return db.execute(
            "INSERT INTO notebooks(slug,title,description,source,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (slug, slug.title(), "", "{}", now, now),
            db_path=self.db_path,
        )

    def test_plain_answer(self):
        self.replies.append(json.dumps({"answer": "Hello! I can manage your Kaggle runs."}))
        result = self.agent.run("hi")
        self.assertEqual(result["answer"], "Hello! I can manage your Kaggle runs.")
        self.assertIsNone(result["pending_approval"])

    def test_nonsensitive_action_then_answer(self):
        self.replies.append(json.dumps({"action": {"tool": "list_notebooks", "params": {}}}))
        self.replies.append(json.dumps({"answer": "2 notebooks found."}))
        result = self.agent.run("what do I have?")
        self.assertEqual(result["answer"], "2 notebooks found.")
        history = db.fetch_all("SELECT role, content FROM chat_messages ORDER BY id", db_path=self.db_path)
        roles = [h["role"] for h in history]
        self.assertIn("note", roles)  # tool result recorded

    def test_sensitive_action_requires_approval_and_does_not_execute(self):
        nb = self._seed_notebook()
        self.replies.append(json.dumps({
            "action": {"tool": "submit_notebook", "params": {"id": nb},
                        "reason": "user asked to run it"},
        }))
        result = self.agent.run("please run the demo notebook")
        self.assertIsNotNone(result["pending_approval"])
        # nothing was enqueued — gate enforced
        jobs = db.fetch_all("SELECT * FROM jobs", db_path=self.db_path)
        self.assertEqual(len(jobs), 0)
        self.assertEqual(self.facade.runs, [])
        approval = db.fetch_one("SELECT * FROM approvals", db_path=self.db_path)
        self.assertEqual(approval["status"], "pending")

    def test_resume_approved_executes_and_continues(self):
        nb = self._seed_notebook()
        self.replies.append(json.dumps({
            "action": {"tool": "submit_notebook", "params": {"id": nb},
                        "reason": "go"},
        }))
        result = self.agent.run("run it")
        aproval_id = result["pending_approval"]
        # after approval, the agent continues and eventually answers
        self.replies.append(json.dumps({"answer": "Submitted! Job #1 queued."}))
        resumed = self.agent.resume(aproval_id, approve=True)
        self.assertEqual(resumed["answer"], "Submitted! Job #1 queued.")
        jobs = db.fetch_all("SELECT * FROM jobs", db_path=self.db_path)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["status"], "queued")
        approval = db.fetch_one("SELECT * FROM approvals", db_path=self.db_path)
        self.assertEqual(approval["status"], "approved")

    def test_resume_rejected_skips_execution(self):
        nb = self._seed_notebook()
        self.replies.append(json.dumps({
            "action": {"tool": "submit_notebook", "params": {"id": nb},
                        "reason": "go"},
        }))
        result = self.agent.run("run it")
        aid = result["pending_approval"]
        self.replies.append(json.dumps({"answer": "Understood, nothing was submitted."}))
        resumed = self.agent.resume(aid, approve=False)
        self.assertEqual(resumed["answer"], "Understood, nothing was submitted.")
        jobs = db.fetch_all("SELECT * FROM jobs", db_path=self.db_path)
        self.assertEqual(len(jobs), 0)

    def test_unknown_tool_recovers(self):
        self.replies.append(json.dumps({"action": {"tool": "destroy_world", "params": {}}}))
        self.replies.append(json.dumps({"answer": "I don't have that tool."}))
        result = self.agent.run("do the thing")
        self.assertIn("don't have that tool", result["answer"])

    def test_config_missing_raises(self):
        agent_no_cfg = Agent(self.facade, db_path=self.db_path, ai_settings_provider=lambda: {})
        agent_mod.llm_chat = self._orig_llm  # allow real path to fail fast on config
        with self.assertRaises(agent_mod.AgentConfigError):
            agent_no_cfg.run("hi")


if __name__ == "__main__":
    unittest.main()
