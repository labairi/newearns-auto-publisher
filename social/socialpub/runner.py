"""The publisher. One call to `run()` processes everything that is due, safely.

Guarantees (each one has a test):
  * A target is marked 'publishing' and saved (and checkpointed to git) BEFORE the platform is
    called, so a crash mid-call is visible afterwards instead of being forgotten.
  * A call that may have been processed ('uncertain') is never retried automatically.
  * A target already 'published' is never sent again, even if the post file is edited later.
  * The same text+media is not posted twice to the same account inside the dedupe window.
  * A post that is far past its time is not published late by surprise.
  * Errors are stored with secrets removed.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path
from typing import Callable

from .errors import PublishError
from .platforms import build_adapter
from .platforms.base import PublishRequest
from .rules import BACKOFF_MINUTES, PLATFORMS
from .store import Store, StoreError, content_hash, redact
from .timeutil import add_minutes, iso, parse_iso, to_utc, utcnow
from .validate import derived_text, validate_post

Checkpoint = Callable[[str], None]


def _targets_text(post: dict, accounts: dict) -> dict[str, tuple[str, str]]:
    out = {}
    for t in post.get("targets", []):
        a = accounts.get(t.get("account"))
        if a and a.get("platform") in PLATFORMS:
            out[t["account"]] = derived_text(post, a["platform"])
    return out


def new_state(post: dict) -> dict:
    return {"id": post["id"], "version": 1, "cancelled": False, "force_now": False, "content_hash": "",
            "validation_error": "", "targets": {t["account"]: {"status": "pending", "attempts": 0} for t in post.get("targets", [])}}


def process_commands(store: Store, now, checkpoint: Checkpoint | None = None) -> list[str]:
    """Apply dashboard requests. Every command is idempotent and is deleted after it is applied."""
    done: list[str] = []
    for path, cmd in store.pending_commands():
        action, pid = cmd.get("action"), cmd.get("post_id", "")
        try:
            if action not in ("cancel", "retry", "publish_now", "mark_published"):
                raise ValueError(f"unknown command {action!r}")
            post = store.load_post(pid)
            st = store.load_state(pid) or new_state(post)
            if action == "cancel":
                st["cancelled"] = True
                for t in st["targets"].values():
                    if t["status"] in ("pending", "failed"):
                        t["status"] = "cancelled"
                store.log("cancelled", pid)
            elif action == "publish_now":
                if st["cancelled"]:
                    raise ValueError("this post was cancelled")
                st["force_now"] = True
                store.log("publish_now_requested", pid)
            elif action == "retry":
                acc = cmd.get("account")
                for a, t in st["targets"].items():
                    if acc and a != acc:
                        continue
                    if t["status"] == "uncertain" and not cmd.get("confirm_not_posted"):
                        store.log("retry_refused", pid, a, reason="uncertain: set confirm_not_posted after checking the platform")
                        continue
                    if t["status"] in ("failed", "uncertain", "cancelled"):
                        t.update(status="pending", attempts=0, last_error="", error_kind="", next_attempt_at="")
                        st["cancelled"] = False
                        store.log("retry_requested", pid, a)
            elif action == "mark_published":
                a = cmd.get("account")
                t = st["targets"].get(a)
                if not t:
                    raise ValueError(f"unknown account {a!r}")
                t.update(status="published", provider_post_id=str(cmd.get("provider_post_id", "manual")), url=str(cmd.get("url", "")),
                         published_at=iso(now), last_error="", manual=True)
                store.log("marked_published", pid, a, url=str(cmd.get("url", "")))
            store.save_state(st)
            done.append(f"{action}:{pid}")
        except Exception as e:  # a bad command must not stop the run
            store.log("command_failed", pid, action=str(action), error=str(e))
        path.unlink(missing_ok=True)
    if done and checkpoint:
        checkpoint("social: apply commands")
    return done


def heal_stuck(store: Store, now, stale_minutes: int) -> list[str]:
    """A target left in 'publishing' means a run died mid-call. We cannot know if it posted."""
    healed = []
    for pid in store.post_ids():
        try:
            st = store.load_state(pid)
        except StoreError:
            continue                                   # reported as invalid in run(); never overwritten
        if not st:
            continue
        ch = False
        for a, t in st["targets"].items():
            if t["status"] == "publishing" and now - parse_iso(t.get("started_at", iso(now))) > timedelta(minutes=stale_minutes):
                t.update(status="uncertain", error_kind="uncertain",
                         last_error="A run stopped while sending this. It may or may not have posted. Check the platform.")
                store.log("stuck_to_uncertain", pid, a)
                healed.append(f"{pid}/{a}")
                ch = True
        if ch:
            store.save_state(st)
    return healed


def _published_recently(store: Store, now, hours: int) -> list[tuple[str, str, str, str]]:
    """(post_id, account, content_hash, platform) of targets published inside the window."""
    out = []
    cutoff = now - timedelta(hours=hours)
    for pid in store.post_ids():
        try:
            st = store.load_state(pid)
        except StoreError:
            continue
        if not st:
            continue
        for a, t in st["targets"].items():
            if t["status"] == "published" and t.get("published_at") and parse_iso(t["published_at"]) >= cutoff:
                out.append((pid, a, t.get("content_hash", ""), t.get("platform", "")))
    return out


def run(store: Store, *, http, now=None, dry_run: bool = False, only_post: str | None = None,
        secrets: dict[str, str] | None = None, checkpoint: Checkpoint | None = None, sleep=None) -> dict:
    clock = (lambda: now) if now is not None else utcnow   # tests inject a fixed time
    now = now or utcnow()
    secrets = secrets or {}
    store.secrets = [v for v in secrets.values() if v]
    cfg = store.settings()
    accounts = store.accounts()
    summary = {"started": iso(now), "dry_run": dry_run, "commands": [], "healed": [], "published": [], "failed": [],
               "uncertain": [], "skipped": [], "would_publish": [], "invalid": [], "retry_waiting": []}
    cp = (lambda m: checkpoint(m)) if (checkpoint and not dry_run) else (lambda m: None)

    if not dry_run:
        summary["commands"] = process_commands(store, now, checkpoint)
        summary["healed"] = heal_stuck(store, now, cfg["stale_publishing_minutes"])

    recent = _published_recently(store, now, cfg["dedupe_window_hours"])
    day = _published_recently(store, now, 24)
    max_late = timedelta(hours=cfg.get("max_lateness_hours", 24))

    for pid in store.post_ids():
        if only_post and pid != only_post:
            continue
        try:                                           # one unreadable file must never block every other post
            post = store.load_post(pid)
            st = store.load_state(pid)
        except StoreError as e:
            summary["invalid"].append({"post": pid, "errors": [str(e)]})
            continue
        if st and st.get("cancelled"):
            continue
        if post.get("intent") != "scheduled" and not (st and st.get("force_now")):
            continue
        errs = validate_post(post, accounts, for_schedule=(post.get("intent") == "scheduled") or bool(st and st.get("force_now")))
        if post.get("intent") != "scheduled" and st and st.get("force_now"):
            errs = [e for e in errs if not e.startswith("a scheduled post needs")]
        if errs:
            summary["invalid"].append({"post": pid, "errors": errs})
            if st is not None and st.get("validation_error") != errs[0] and not dry_run:
                st["validation_error"] = errs[0]
                store.save_state(st)
                store.log("invalid", pid, error=errs[0])
            elif st is None and not dry_run:
                st = new_state(post)
                st["validation_error"] = errs[0]
                store.save_state(st)
                store.log("invalid", pid, error=errs[0])
            continue
        forced = bool(st and st.get("force_now"))
        sched = post.get("scheduled") or {}
        due_at = now if forced or not sched.get("local") else to_utc(sched["local"], sched["tz"])
        if due_at > now:
            continue
        late = now - due_at
        texts = _targets_text(post, accounts)
        if st is None:
            st = new_state(post)
        for t in post["targets"]:                       # targets added after the first run
            st["targets"].setdefault(t["account"], {"status": "pending", "attempts": 0})
        st["validation_error"] = ""

        for acc_id, tgt in st["targets"].items():
            account = accounts.get(acc_id)
            if not account or acc_id not in texts:
                continue                               # target removed from the post: leave history alone
            platform = account["platform"]
            title, text = texts[acc_id]
            chash = content_hash(post, {acc_id: text})
            status = tgt["status"]
            if status in ("published", "cancelled", "skipped", "publishing", "uncertain"):
                if status == "uncertain" and not dry_run:
                    summary["uncertain"].append({"post": pid, "account": acc_id, "error": tgt.get("last_error", "")})
                continue
            if status == "failed":
                if tgt.get("error_kind") == "permanent" or tgt.get("attempts", 0) >= cfg["max_attempts"]:
                    continue
                if tgt.get("next_attempt_at") and parse_iso(tgt["next_attempt_at"]) > now:
                    summary["retry_waiting"].append({"post": pid, "account": acc_id, "at": tgt["next_attempt_at"]})
                    continue
            if not forced and late > max_late:
                tgt.update(status="failed", error_kind="permanent", last_error=f"missed its time by {int(late.total_seconds() // 3600)} hours; "
                           "it was not posted late. Reschedule it or publish it now.")
                summary["failed"].append({"post": pid, "account": acc_id, "error": tgt["last_error"]})
                if not dry_run:
                    store.log("missed", pid, acc_id, late_hours=int(late.total_seconds() // 3600))
                continue
            if any(h == chash and a == acc_id and p != pid for p, a, h, _ in recent):
                tgt.update(status="skipped", last_error="the same text and media was already posted to this account in the last "
                           f"{cfg['dedupe_window_hours']} hours")
                summary["skipped"].append({"post": pid, "account": acc_id, "why": "duplicate content"})
                if not dry_run:
                    store.log("skipped_duplicate", pid, acc_id)
                continue
            cap = PLATFORMS[platform].get("daily_cap")
            if cap and sum(1 for _, a, _, pl in day if a == acc_id) >= int(cap * 0.9):
                summary["skipped"].append({"post": pid, "account": acc_id, "why": f"close to the {cap} posts per 24 hours {platform} allows; will try next run"})
                continue
            if dry_run:
                summary["would_publish"].append({"post": pid, "account": acc_id, "platform": platform, "text": text[:80]})
                continue

            # ---- write-ahead: record the attempt BEFORE calling the platform ----
            attempt_id = uuid.uuid4().hex[:12]
            tgt.update(status="publishing", attempts=tgt.get("attempts", 0) + 1, attempt_id=attempt_id, started_at=iso(clock()),
                       platform=platform, content_hash=chash, last_error="", error_kind="")
            st["content_hash"] = chash
            store.save_state(st)
            store.log("attempt", pid, acc_id, attempt=tgt["attempts"], attempt_id=attempt_id)
            cp("social: attempt " + pid)

            req = PublishRequest(post_id=pid, account_id=acc_id, attempt_id=attempt_id, text=text, title=title,
                                 media=list(post.get("media") or []), settings=(post.get("settings") or {}).get(platform, {}),
                                 since_iso=iso(now - timedelta(hours=2)))
            result = None
            try:
                adapter = build_adapter(account, secrets, http, sleep)
                result = adapter.publish(req)
            except PublishError as e:
                err, kind = redact(str(e), store.secrets), e.kind
            except Exception as e:  # a bug or an unexpected failure: we cannot know if the call went out
                err, kind = redact(f"unexpected {type(e).__name__}: {e}", store.secrets), "uncertain"
            if result is None and kind == "uncertain":
                try:                                    # ask the platform if it did get the post (only where the API allows)
                    found = build_adapter(account, secrets, http, sleep).find_existing(req)
                except Exception:
                    found = None
                if found:
                    result = found
                    tgt["adopted"] = True
            if result is not None:
                tgt.update(status="published", provider_post_id=result.provider_id, url=result.url, published_at=iso(clock()), last_error="", error_kind="")
                summary["published"].append({"post": pid, "account": acc_id, "url": result.url})
                store.log("published", pid, acc_id, provider_post_id=result.provider_id, url=result.url, adopted=bool(tgt.get("adopted")))
                day.append((pid, acc_id, chash, platform))
                recent.append((pid, acc_id, chash, platform))
            else:
                tgt.update(last_error=err, error_kind=kind)
                if kind == "uncertain":
                    tgt["status"] = "uncertain"
                    summary["uncertain"].append({"post": pid, "account": acc_id, "error": err})
                elif kind == "transient" and tgt["attempts"] < cfg["max_attempts"]:
                    wait = BACKOFF_MINUTES[min(tgt["attempts"] - 1, len(BACKOFF_MINUTES) - 1)]
                    tgt.update(status="failed", next_attempt_at=iso(add_minutes(clock(), wait)))
                    summary["failed"].append({"post": pid, "account": acc_id, "error": err, "will_retry_at": tgt["next_attempt_at"]})
                else:
                    tgt["status"] = "failed"
                    if kind == "transient":
                        tgt["error_kind"] = "permanent"; tgt["last_error"] = err + " (gave up after " + str(tgt["attempts"]) + " attempts)"
                    summary["failed"].append({"post": pid, "account": acc_id, "error": tgt["last_error"]})
                store.log("failed" if tgt["status"] == "failed" else "uncertain", pid, acc_id, error=err, kind=kind, attempt=tgt["attempts"])
            store.save_state(st)
            cp("social: result " + pid)
        if not dry_run:
            st["force_now"] = False if all(t["status"] != "pending" for t in st["targets"].values()) else st.get("force_now", False)
            store.save_state(st)
    if not dry_run:
        store.log("run_summary", **{k: len(v) for k, v in summary.items() if isinstance(v, list)})
        cp("social: run summary")
    return summary
