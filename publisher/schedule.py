"""The daily scheduled run: whether to run at all, and which one calendar topic.

Everything here reads local files only and runs before any Claude call, so a
skipped day costs nothing.

data/schedule_log.jsonl  one line when a scheduled attempt starts, one when it
                         ends, plus `schedule_resumed` markers. An attempt that
                         started but never ended (crash, runner killed) still
                         counts as an unsuccessful attempt.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from . import ledger

# Outcomes of a non-dry-run attempt as written to data/publication_log.jsonl.
ATTEMPT_STATUSES = ("published", "draft", "held_for_review", "held_duplicate", "failed_verification")
SUCCESS = ("published", "draft", "already-published")
PAUSE_AFTER = 2  # consecutive unsuccessful attempts before scheduled runs stop


def _log_path():
    return ledger.DATA / "schedule_log.jsonl"


def read_schedule_log() -> list[dict]:
    p = _log_path()
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def _append(entry: dict) -> None:
    ledger.DATA.mkdir(parents=True, exist_ok=True)
    with _log_path().open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _when(s: str) -> datetime:
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def attempts() -> list[dict]:
    """Every non-dry-run attempt, oldest first: {"date", "run_id", "status"}.

    Manual and scheduled runs that reached an outcome are in the publication log.
    Scheduled attempts that ended without one (an error, a crash) come from the
    schedule log, so they count towards the 20-hour rule and the pause too.
    """
    out = [{"date": e["date"], "run_id": e.get("run_id"), "status": e["status"]}
           for e in ledger.read_log() if e.get("status") in ATTEMPT_STATUSES]
    sched = read_schedule_log()
    ended = {e["attempt"]: e["status"] for e in sched if e.get("event") == "finished"}
    for e in sched:
        if e.get("event") == "started":
            status = ended.get(e["attempt"], "unfinished")
            if status not in ATTEMPT_STATUSES:  # those are already in the publication log
                out.append({"date": e["date"], "run_id": e["run_id"], "status": status})
    return sorted(out, key=lambda a: _when(a["date"]))


def reconcile(live_posts: list[dict]) -> list[dict]:
    """Record held or draft posts that have since gone live in WordPress (e.g. published by
    hand in WP Admin), so the log matches the site. Appends one `published` entry per post,
    once; returns the new entries.

    Without this, a post the owner published would still read as held: the pause would
    count it as a failure and the 20-hour gap wouldn't see when it actually went live.
    """
    live = {p["id"]: p for p in live_posts}
    log = ledger.read_log()
    done = {e.get("post_id") for e in log if e.get("status") == "published"}
    topics = {run_id: s.get("topic") for run_id, s in _runs()}
    added = []
    for e in log:
        pid = e.get("post_id")
        if pid in live and pid not in done and e.get("status") in ("held_for_review", "draft", "failed_verification"):
            p = live[pid]
            went_live = p.get("date_gmt")
            entry = {"run_id": e.get("run_id"), "date": f"{went_live}+00:00" if went_live else ledger.now_iso(),
                     "status": "published", "source": "wordpress", "post_id": pid, "url": p.get("link"),
                     "slug": p.get("slug"), "pillar": e.get("pillar"), "topic": e.get("topic"),
                     "calendar_topic": e.get("calendar_topic") or topics.get(e.get("run_id")),
                     "note": f"was {e['status']}; found live in WordPress on {ledger.now_iso()}"}
            ledger.append_log(entry)
            done.add(pid)
            added.append(entry)
    return added


def skip_reason(cfg: dict, now: datetime | None = None) -> str | None:
    """Why today's scheduled run must not start, or None if it may."""
    now = now or datetime.now(timezone.utc)
    hist = attempts()
    gap = cfg["publishing"]["min_hours_between_posts"]
    if hist:
        age_h = (now - _when(hist[-1]["date"])).total_seconds() / 3600
        if age_h < gap:
            return (f"Skipped: the last attempt ({hist[-1]['status']}) was {age_h:.1f}h ago; "
                    f"scheduled runs wait {gap}h after any non-dry-run attempt.")
    resumed = [_when(e["date"]) for e in read_schedule_log() if e.get("event") == "schedule_resumed"]
    since = [a for a in hist if not resumed or _when(a["date"]) > max(resumed)]
    tail = since[-PAUSE_AFTER:]
    if len(tail) == PAUSE_AFTER and all(a["status"] not in SUCCESS for a in tail):
        return (f"Paused: the last {PAUSE_AFTER} attempts were unsuccessful "
                f"({', '.join(a['status'] for a in tail)}). Fix the cause, then run the "
                "`resume-schedule` command to let scheduled runs continue.")
    return None


def _runs() -> list[tuple[str, dict]]:
    if not ledger.RUNS.exists():
        return []
    out = []
    for d in sorted(ledger.RUNS.iterdir(), reverse=True):  # newest first
        st = d / "state.json"
        if st.exists():
            out.append((d.name, json.loads(st.read_text())["stages"]))
    return out


def _key(pillar, topic) -> tuple:
    return pillar, (topic or "").strip().lower()


def product_pillars(cfg: dict) -> set[str]:
    """Pillars whose articles are about one of our own products."""
    return {name for name, p in cfg["pillars"].items() if p.get("product")}


def product_slot_due(cfg: dict, now: datetime | None = None) -> bool:
    """True when no product article was attempted in the last `topic_mix.product_every_days`
    calendar days, counting manual and scheduled attempts, crashed ones included.

    With 7, a product post on a Monday makes the next one due the following Monday; a day
    the schedule skipped (or GitHub dropped) moves it to the next run, not to the next week.
    """
    every = (cfg.get("topic_mix") or {}).get("product_every_days") or 0
    if not every:
        return True
    now = now or datetime.now(timezone.utc)
    prod = product_pillars(cfg)
    dates = [_when(e["date"]) for e in ledger.read_log()
             if e.get("pillar") in prod and e.get("status") in ATTEMPT_STATUSES]
    dates += [_when(e["date"]) for e in read_schedule_log()
              if e.get("event") == "started" and e.get("pillar") in prod]
    return not dates or (now.date() - max(dates).date()).days >= every


def pick_topic(cfg: dict, now: datetime | None = None) -> tuple[dict, str | None] | None:
    """(calendar entry, run_id to resume or None) for today's run, or None if nothing is eligible.

    A topic counts as attempted once any run of it reached a WordPress outcome
    (published, draft, held, duplicate, failed verification). Held topics are not
    retried automatically: retrying would pay for a new article. Dry runs don't count.

    Topic mix (`topic_mix.product_every_days`, 0 = off): when the product slot is due, the
    first open product topic runs (any other topic if none is left); otherwise only
    topics without a product run, and a day with none left is skipped rather than
    spent on a product post early.
    """
    calendar = {_key(e["pillar"], e["topic"]): e for e in cfg.get("calendar") or []}
    runs = _runs()
    attempted = {_key(s.get("pillar"), s.get("topic")) for _, s in runs if s.get("final") in ATTEMPT_STATUSES}
    attempted |= {_key(e.get("pillar"), e.get("calendar_topic")) for e in ledger.read_log()
                  if e.get("calendar_topic") and e.get("status") in ATTEMPT_STATUSES}
    # Finish an interrupted calendar run first: its finished stages are reused, not paid for again.
    for run_id, s in runs:
        k = _key(s.get("pillar"), s.get("topic"))
        if "final" not in s and k in calendar and k not in attempted:
            return calendar[k], run_id
    open_ = [e for k, e in calendar.items() if k not in attempted]  # calendar order
    every = (cfg.get("topic_mix") or {}).get("product_every_days") or 0
    if every:
        prod = product_pillars(cfg)
        products = [e for e in open_ if e["pillar"] in prod]
        others = [e for e in open_ if e["pillar"] not in prod]
        open_ = (products + others) if product_slot_due(cfg, now) else others
    return (open_[0], None) if open_ else None


def start(run_id: str, entry: dict) -> str:
    attempt = f"{run_id}@{ledger.now_iso()}"
    _append({"event": "started", "attempt": attempt, "run_id": run_id, "date": ledger.now_iso(),
             "pillar": entry["pillar"], "topic": entry["topic"]})
    return attempt


def finish(attempt: str, status: str) -> None:
    _append({"event": "finished", "attempt": attempt, "date": ledger.now_iso(), "status": status})


def resume() -> None:
    _append({"event": "schedule_resumed", "date": ledger.now_iso()})
