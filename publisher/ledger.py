"""Publication log + run checkpoints.

data/publication_log.jsonl  one line per finished run (published, held, failed)
data/runs/<run_id>/          checkpoint of every stage so a crashed run resumes
                             instead of regenerating (and re-paying for) work.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("PUBLISHER_DATA_DIR", ROOT / "data"))
LOG = DATA / "publication_log.jsonl"
RUNS = DATA / "runs"
LOCK = DATA / ".run.lock"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_log() -> list[dict]:
    if not LOG.exists():
        return []
    return [json.loads(line) for line in LOG.read_text().splitlines() if line.strip()]


def append_log(entry: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def logged_slugs() -> set[str]:
    return {e["slug"] for e in read_log() if e.get("slug") and e.get("status") in ("published", "held_for_review", "draft")}


class RunLock:
    """Stops two scheduled runs overlapping (GitHub also uses a concurrency group)."""

    def __enter__(self):
        DATA.mkdir(parents=True, exist_ok=True)
        if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 3 * 3600:
            raise RuntimeError(f"another run holds {LOCK}; delete it if that run is dead")
        LOCK.write_text(now_iso())
        return self

    def __exit__(self, *exc):
        LOCK.unlink(missing_ok=True)


class Checkpoint:
    def __init__(self, run_id: str):
        self.dir = RUNS / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "state.json"
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {"run_id": run_id, "stages": {}}

    def get(self, stage: str):
        return self.state["stages"].get(stage)

    def put(self, stage: str, value) -> None:
        self.state["stages"][stage] = value
        self.state["updated"] = now_iso()
        self.path.write_text(json.dumps(self.state, indent=2, ensure_ascii=False))

    def save_file(self, name: str, data: bytes | str) -> Path:
        p = self.dir / name
        p.write_bytes(data if isinstance(data, bytes) else data.encode())
        return p


def unfinished_run() -> str | None:
    """Most recent run that never reached a terminal stage, for --resume."""
    if not RUNS.exists():
        return None
    for d in sorted(RUNS.iterdir(), reverse=True):
        st = d / "state.json"
        if st.exists() and "final" not in json.loads(st.read_text())["stages"]:
            return d.name
    return None
