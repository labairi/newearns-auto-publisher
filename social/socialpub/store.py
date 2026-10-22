"""File-backed queue.

Ownership is split so two writers never edit the same file:

  queue/posts/<id>.json      content. Owned by the person / dashboard.
  queue/state/<id>.json      runtime state. Owned by the publisher workflow only.
  queue/commands/<uuid>.json requests from the dashboard (cancel, retry, publish_now). The publisher
                             consumes and deletes them at the start of a run.
  queue/log.jsonl            append-only event log, written by the publisher only.

Because content and state are separate files and only one workflow runs at a time (concurrency
group), a dashboard edit and a publisher run cannot corrupt each other.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .timeutil import iso, utcnow

SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")
TARGET_DONE = ("published",)
TARGET_OPEN = ("pending", "failed")


class StoreError(RuntimeError):
    pass


def redact(text: str, secrets: list[str] | None) -> str:
    """Remove every known secret value from text before it is logged or stored."""
    out = str(text)
    for s in secrets or []:
        if s and len(s) >= 6:
            out = out.replace(s, "[redacted]")
    out = re.sub(r"(access_token|accessJwt|Authorization|Bearer|password|client_secret)([=:\s\"']+)([A-Za-z0-9._\-~+/=]{8,})",
                 r"\1\2[redacted]", out)
    return out


def _atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Store:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.posts = self.root / "queue" / "posts"
        self.states = self.root / "queue" / "state"
        self.commands = self.root / "queue" / "commands"
        self.log_path = self.root / "queue" / "log.jsonl"
        self.config = self.root / "config"
        self.secrets: list[str] = []

    # ---------- config ----------
    def read_json(self, path: Path, default: Any = None) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise StoreError(f"{path.name} is not valid JSON: {e}") from e

    def accounts(self) -> dict[str, dict]:
        cfg = self.read_json(self.config / "accounts.json", {"accounts": []})
        return {a["id"]: a for a in cfg.get("accounts", []) if isinstance(a, dict) and "id" in a}

    def settings(self) -> dict:
        s = {"max_attempts": 3, "stale_publishing_minutes": 30, "dedupe_window_hours": 24}
        s.update(self.read_json(self.config / "settings.json", {}) or {})
        return s

    # ---------- posts ----------
    def _pid(self, pid: str) -> str:
        if not isinstance(pid, str) or not SAFE_ID.match(pid):
            raise StoreError(f"unsafe post id: {pid!r}")
        return pid

    def post_ids(self) -> list[str]:
        if not self.posts.exists():
            return []
        return sorted(p.stem for p in self.posts.glob("*.json") if SAFE_ID.match(p.stem))

    def load_post(self, pid: str) -> dict:
        p = self.posts / f"{self._pid(pid)}.json"
        if not p.exists():
            raise StoreError(f"no such post: {pid}")
        return self.read_json(p)

    def save_post(self, post: dict) -> None:
        pid = self._pid(post.get("id"))
        post = dict(post)
        post["updated_at"] = iso(utcnow())
        post.setdefault("created_at", post["updated_at"])
        _atomic_write(self.posts / f"{pid}.json", json.dumps(post, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    # ---------- state ----------
    def load_state(self, pid: str) -> dict | None:
        p = self.states / f"{self._pid(pid)}.json"
        return self.read_json(p) if p.exists() else None

    def save_state(self, state: dict) -> None:
        pid = self._pid(state.get("id"))
        state = dict(state)
        state["updated_at"] = iso(utcnow())
        _atomic_write(self.states / f"{pid}.json", json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    # ---------- log ----------
    def log(self, event: str, post_id: str = "", account: str = "", **detail: Any) -> None:
        rec = {"ts": iso(utcnow()), "event": event}
        if post_id:
            rec["post_id"] = post_id
        if account:
            rec["account"] = account
        for k, v in detail.items():
            rec[k] = redact(v, self.secrets) if isinstance(v, str) else v
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")

    def read_log(self, limit: int | None = None) -> list[dict]:
        if not self.log_path.exists():
            return []
        rows = [json.loads(x) for x in self.log_path.read_text(encoding="utf-8").splitlines() if x.strip()]
        return rows[-limit:] if limit else rows

    # ---------- commands ----------
    def pending_commands(self) -> list[tuple[Path, dict]]:
        if not self.commands.exists():
            return []
        out = []
        for p in sorted(self.commands.glob("*.json")):
            try:
                out.append((p, json.loads(p.read_text(encoding="utf-8"))))
            except json.JSONDecodeError:
                out.append((p, {"action": "invalid"}))
        return out


def content_hash(post: dict, target_text: dict[str, str]) -> str:
    """Hash of what actually gets posted: the text for each target and the media. Used so a changed
    post is never confused with one that was already published."""
    blob = json.dumps({"t": target_text, "m": [(m.get("type"), m.get("url")) for m in post.get("media") or []]},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def display_status(post: dict, state: dict | None) -> str:
    """draft | scheduled | publishing | published | partial | failed | uncertain | cancelled"""
    if state and state.get("cancelled"):
        return "cancelled"
    ts = list((state or {}).get("targets", {}).values())
    if ts:
        sts = [t["status"] for t in ts]
        if "publishing" in sts:
            return "publishing"
        if all(s in ("published", "skipped") for s in sts) and "published" in sts:
            return "published"
        if "published" in sts and any(s in ("failed", "uncertain", "pending") for s in sts):
            return "partial"
        if "uncertain" in sts:
            return "uncertain"
        if "failed" in sts:
            return "failed"
    return "scheduled" if post.get("intent") == "scheduled" else "draft"
