"""Thin client for the official Kaggle Kernels API v1.

Alignment notes (reference: github.com/barryallen16/kaggle-automation):
- Kaggle keys kernels by their SLUGIFIED TITLE. Pushing an existing title
  updates that kernel; a new title creates a new one. The push metadata `id`
  can be the string ref "<user>/<slug>", so no numeric-id discovery is needed.
- auth: Basic (username:key) OR Bearer token via KAGGLE_API_TOKEN env.
- A running kernel can be stopped by pushing an immediate-exit stub as a new
  version (no abort endpoint exists). Grab outputs BEFORE the stub push; after
  it, latest output is just the stub log.

Endpoints (https://www.kaggle.com/docs/api — "Kernels"):
  GET  /kernels/list?user=..&pageSize=..   -> kernels owned by the user
  POST /kernels/push                        -> create draft / push new version (runs it)
  GET  /kernels/status?user=..&kernel=..    -> execution status
  GET  /kernels/output?user=..&kernel=..    -> output zip
  GET  /kernels/log?user=..&kernel=..       -> live log text

NOTE: response shapes were implemented from the public API docs and this
reference; they are isolated here so a first live deployment can verify them
in one place (see README "Deployment checklist").
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import time
import zipfile
from pathlib import Path
from typing import Optional

from . import config


class ApiError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.message = message
        self.status = status


class CredentialsMissing(ApiError):
    pass


# Friendly name -> (machine_shape, enable_gpu, enable_tpu)
ACCELERATORS: dict[str, tuple[str, bool, bool]] = {
    "auto": ("", False, False),
    "cpu": ("", False, False),
    "gpu_t4": ("nvidia-tesla-t4", True, False),
    "gpu_t4_x2": ("nvidia-tesla-t4-x2", True, False),
    "gpu_p100": ("nvidia-tesla-p100", True, False),
    "gpu_v100": ("nvidia-tesla-v100", True, False),
    "gpu_l4": ("nvidia-l4", True, False),
    "gpu_a100": ("nvidia-a100", True, False),
    "gpu_h100": ("nvidia-h100", True, False),
    "tpu_v3_8": ("tpu-v3-8", False, True),
}

PUSH_CONFLICT_RETRY_DELAYS = (5, 10, 20)


def _extract_ref(text: str) -> Optional[str]:
    m = re.search(r"kaggle\.com/code/([\w\-]+)/([\w\-]+)", text or "")
    return f"{m.group(1)}/{m.group(2)}" if m else None


def _normalize_source(source_json: str) -> str:
    """Ensure the uploaded payload is an executable .ipynb JSON document."""
    try:
        nb = json.loads(source_json or "{}")
    except ValueError:
        nb = {}
    if not isinstance(nb, dict):
        nb = {}
    if not isinstance(nb.get("cells"), list):
        cells = [{
            "cell_type": "code", "execution_count": None, "metadata": {},
            "outputs": [],
            "source": [line + "\n" for line in (source_json or "").splitlines()],
        }]
    else:
        cells = nb["cells"]
    nb["cells"] = cells
    nb["metadata"] = nb.get("metadata") or {}
    nb["metadata"].setdefault("kernelspec", {
        "display_name": "Python 3", "language": "python", "name": "python3",
    })
    nb["nbformat"] = nb.get("nbformat", 4)
    nb["nbformat_minor"] = nb.get("nbformat_minor", 2)
    return json.dumps(nb)


class KaggleFacade:
    """Wraps all Kaggle interactions.

    `settings` is a callable returning a dict with optional kaggle_username /
    kaggle_key (legacy single-identity path). `account_provider(account_id)`
    returns {"username":..., "key":...} for a registered account, or None.
    Fallback order: account -> env KAGGLE_API_TOKEN -> legacy settings/env pair.
    """

    BASE = "https://www.kaggle.com/api/v1"
    TIMEOUT = 60.0

    def __init__(self, settings_provider=None, account_provider=None):
        self._settings = settings_provider or (lambda: {})
        self._accounts = account_provider or (lambda account_id: None)

    # ------------------------------------------------------------------ auth

    def _auth(self, account: Optional[dict] = None) -> tuple:
        if account and account.get("username") and account.get("key"):
            return ("basic", str(account["username"]).strip(), str(account["key"]).strip())
        s = self._settings()
        u = s.get("kaggle_username") or os.environ.get("KAGGLE_USERNAME", "")
        k = s.get("kaggle_key") or os.environ.get("KAGGLE_KEY", "")
        tok = os.environ.get("KAGGLE_API_TOKEN", "")
        if not u or not k:
            if tok:
                return ("bearer", "", tok.strip())
            raise CredentialsMissing(
                "Kaggle credentials are not configured. Set KAGGLE_ACCOUNTS "
                "(JSON list) or KAGGLE_USERNAME / KAGGLE_KEY / "
                "KAGGLE_API_TOKEN as environment variables."
            )
        return ("basic", u.strip(), k.strip())

    def _client(self, auth: tuple):
        # lazy import so the module can be imported without httpx installed
        import httpx
        headers = {"Content-Type": "application/json"}
        if auth[0] == "bearer":
            headers["Authorization"] = f"Bearer {auth[2]}"
        else:
            basic = base64.b64encode(f"{auth[1]}:{auth[2]}".encode()).decode()
            headers["Authorization"] = f"Basic {basic}"
            headers["X-Kaggle-Authorization"] = f"Bearer {auth[2]}"
        return httpx.Client(base_url=self.BASE, timeout=self.TIMEOUT, headers=headers)

    @staticmethod
    def _raise_for(resp) -> None:
        if resp.status_code < 400:
            return
        try:
            data = resp.json()
            message = data.get("message") or data.get("error") or resp.text[:300]
        except Exception:
            message = resp.text[:300]
        raise ApiError(message, resp.status_code)

    def _owner(self, auth: tuple) -> str:
        return auth[1] if auth[0] == "basic" else "me"

    # ------------------------------------------------------------------ core

    def list_notebooks(self, page_size: int = 50, account: Optional[dict] = None) -> list[dict]:
        auth = self._auth(account)
        resp = self._client(auth).get(
            "/kernels/list",
            params={"user": self._owner(auth), "pageSize": page_size, "page": 1},
        )
        self._raise_for(resp)
        items = resp.json() or []
        return [i for i in items if isinstance(i, dict)]

    @staticmethod
    def _build_metadata(owner: str, nb: dict, new_version: bool) -> dict:
        accelerator = str(nb.get("accelerator") or "auto").strip().lower()
        machine, gpu, tpu = ACCELERATORS.get(accelerator, ("", False, False))
        internet = bool(nb.get("internet", True))
        return {
            "id": f"{owner}/{nb['slug']}",
            "slug": nb["slug"],
            "title": (nb.get("title") or nb["slug"])[:50],
            "isPrivate": True,
            "language": "python",
            "kernel_type": "notebook",
            "enable_gpu": "true" if gpu else "false",
            "enable_tpu": "true" if tpu else "false",
            "enable_internet": "true" if internet else "false",
            "machine_shape": machine,
            "competition_sources": [],
            "dataset_sources": [],
            "kernel_sources": [],
            "model_sources": [],
        }

    def _push(self, nb: dict, new_version: bool, account: Optional[dict] = None) -> Optional[str]:
        auth = self._auth(account)
        owner = self._owner(auth)
        body = {
            "new_version": new_version,
            "metadata": self._build_metadata(owner, nb, new_version),
            "source": {
                "language": "python",
                "kernel_type": "notebook",
                "code": _normalize_source(nb.get("source", "{}")),
            },
        }
        last_resp = None
        for attempt in range(len(PUSH_CONFLICT_RETRY_DELAYS) + 1):
            last_resp = self._client(auth).post("/kernels/push", json=body)
            if last_resp.status_code == 409 and attempt < len(PUSH_CONFLICT_RETRY_DELAYS):
                time.sleep(PUSH_CONFLICT_RETRY_DELAYS[attempt])
                continue
            break
        self._raise_for(last_resp)
        return _extract_ref(last_resp.text)

    def ensure_notebook(self, nb: dict, account: Optional[dict] = None) -> str:
        """Create the notebook on Kaggle if it does not exist (private draft)."""
        auth = self._auth(account)
        ref = self._push(nb, new_version=False, account=account)
        return ref or f"{self._owner(auth)}/{nb['slug']}"

    def run_notebook(self, nb: dict, account: Optional[dict] = None) -> str:
        """Push a new version — queues an execution (like Save & Run)."""
        auth = self._auth(account)
        ref = self._push(nb, new_version=True, account=account)
        return ref or f"{self._owner(auth)}/{nb['slug']}"

    def stop_notebook(self, nb: dict, account: Optional[dict] = None) -> str:
        """Stop a running kernel by pushing an immediate-exit stub version."""
        stub_source = json.dumps({
            "cells": [{
                "cell_type": "code", "execution_count": None, "metadata": {},
                "outputs": [],
                "source": ["print('Stopped by Kaggle Cloud Manager')\n"],
            }],
            "metadata": {"kernelspec": {
                "display_name": "Python 3", "language": "python", "name": "python3",
            }},
            "nbformat": 4, "nbformat_minor": 2,
        })
        stub_nb = {
            "slug": nb["slug"],
            "title": nb.get("title", nb["slug"]),
            "accelerator": "cpu",
            "internet": False,
            "source": stub_source,
        }
        auth = self._auth(account)
        ref = self._push(stub_nb, new_version=True, account=account)
        return ref or f"{self._owner(auth)}/{nb['slug']}"

    def get_status(self, ref: str, account: Optional[dict] = None) -> dict:
        user, slug = ref.split("/", 1)
        resp = self._client(self._auth(account)).get(
            "/kernels/status", params={"user": user, "kernel": slug}
        )
        self._raise_for(resp)
        data = resp.json() or {}
        return {
            "status": str(data.get("status", "")).lower(),
            "message": data.get("message") or "",
            "failure_message": data.get("failureMessage") or "",
        }

    def get_logs(self, ref: str, account: Optional[dict] = None) -> str:
        user, slug = ref.split("/", 1)
        resp = self._client(self._auth(account)).get(
            "/kernels/log", params={"user": user, "kernel": slug}
        )
        self._raise_for(resp)
        try:
            data = resp.json()
            if isinstance(data, dict):
                return data.get("logs") or json.dumps(data)
            return resp.text
        except Exception:
            return resp.text

    def download_output(self, ref: str, dest_dir: Path,
                        account: Optional[dict] = None) -> list[str]:
        user, slug = ref.split("/", 1)
        resp = self._client(self._auth(account)).get(
            "/kernels/output", params={"user": user, "kernel": slug}
        )
        self._raise_for(resp)
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        zip_path = dest_dir / "output.zip"
        zip_path.write_bytes(resp.content)
        names = []
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(dest_dir)
                names = zf.namelist()
        except zipfile.BadZipFile:
            names = ["output.zip"]
        finally:
            if names and "output.zip" in names:
                shutil.copy(zip_path, dest_dir / "output.zip")
        return names
