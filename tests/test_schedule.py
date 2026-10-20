"""The daily scheduled run: topic choice, the 20-hour gap, the pause rule, resume, draft-only.

All offline: WordPress is a fake, Claude calls are fakes that count themselves.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
import yaml

from publisher import ledger, run, schedule
from tests.test_pipeline import CFG, env, make_article, write  # noqa: F401

FIRST = CFG["calendar"][0]
SECOND = CFG["calendar"][1]


def ago(hours):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0).isoformat()


def log(status, hours, **kw):
    ledger.append_log({"run_id": kw.pop("run_id", f"r{hours}"), "date": ago(hours), "status": status, **kw})


def checkpoint(run_id, **stages):
    d = ledger.RUNS / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps({"run_id": run_id, "stages": stages}))


@pytest.fixture
def calls(monkeypatch):
    """Fake Claude stages; records every call so tests can assert 'no Claude call'."""
    seen = []
    monkeypatch.setattr(run.writer, "api_key_problem", lambda: None)

    def brief(cfg, pillar, cands, titles, notes, reader=None):
        seen.append(("research", pillar, cands))
        return "brief", {"stage": "research"}

    def article(*a, **k):
        seen.append(("write",))
        return make_article(), {"stage": "write"}

    def fact(cfg, art, pillar, reader=None):
        seen.append(("fact_check",))
        return make_article()["fact_check"], [{"stage": "fact_check"}]

    monkeypatch.setattr(run.writer, "research_brief", brief)
    monkeypatch.setattr(run.writer, "write_article", article)
    monkeypatch.setattr(run.writer, "fact_check", fact)
    return seen


def sched_log():
    return schedule.read_schedule_log()


# ------------------------------------------------------------ workflow and config
def workflow():
    return yaml.safe_load((run.ROOT / ".github/workflows/publish.yml").read_text())


def test_scheduled_workflow_run_is_the_scheduled_command_and_never_gets_the_second_key():
    y = workflow()
    expr = y["jobs"]["run"]["env"]["ALLOW_PUBLISH"]
    # Only a manual `article` run with the box ticked can make it "true"; a schedule has no inputs.
    assert "inputs.command == 'article' && inputs.allow_publish" in expr and "schedule" not in expr
    script = next(s for s in y["jobs"]["run"]["steps"] if s.get("name") == "Run")["run"]
    head = script.split("plan=()")[0]
    assert 'if [ "${{ github.event_name }}" = schedule ]' in head
    assert "python -m publisher.run article --scheduled\n" in head and "exit" in head
    assert "resume-schedule)  python -m publisher.run resume-schedule ;;" in script
    assert "resume-schedule" in y[True]["workflow_dispatch"]["inputs"]["command"]["options"]
    # The log and checkpoints are committed after scheduled runs too (inputs.command is empty then).
    save = next(s for s in y["jobs"]["run"]["steps"] if s.get("name", "").startswith("Save"))
    assert save["if"] == "always() && inputs.command != 'check-setup'"


def test_shipped_config_keeps_scheduled_runs_draft_only():
    assert CFG["publishing"]["scheduled_live"] is False
    assert CFG["publishing"]["min_hours_between_posts"] == 20


# ------------------------------------------------------------ draft-only and one article
def test_scheduled_run_saves_one_draft_even_with_publish_mode_and_second_key(env, calls):
    wp, tmp, cfg = env  # mode publish, ALLOW_PUBLISH=true
    assert run.main(["article", "--scheduled"]) == 0
    assert [p["status"] for p in wp.posts.values()] == ["draft"]
    assert ("status", "publish") not in wp.calls
    assert [c[0] for c in calls] == ["research", "write", "fact_check"]  # exactly one article
    assert calls[0][1:] == (FIRST["pillar"], [f"{FIRST['topic']} (primary query: {FIRST['query']})"])
    entry = ledger.read_log()[-1]
    assert entry["status"] == "draft" and entry["calendar_topic"] == FIRST["topic"]
    state = json.loads((ledger.RUNS / entry["run_id"] / "state.json").read_text())["stages"]
    assert len(state["checks"]) == 36  # every quality gate ran
    assert [(e["event"], e.get("status")) for e in sched_log()] == [("started", None), ("finished", "draft")]


def test_scheduled_run_that_fails_a_gate_goes_to_pending_review(env, calls, monkeypatch):
    wp, tmp, _ = env
    bad = make_article()
    bad["content_html"] += "<p>About 73% of freelancers email PDFs daily.</p>"
    monkeypatch.setattr(run.writer, "write_article", lambda *a, **k: (bad, {"stage": "write"}))
    assert run.main(["article", "--scheduled"]) == 0
    assert [p["status"] for p in wp.posts.values()] == ["pending"]
    assert ledger.read_log()[-1]["status"] == "held_for_review"
    assert sched_log()[-1]["status"] == "held_for_review"


def test_live_scheduled_publishing_needs_both_config_and_second_key(env, calls, monkeypatch):
    wp, tmp, cfg = env
    cfg["publishing"]["scheduled_live"] = True
    monkeypatch.delenv("ALLOW_PUBLISH")
    assert run.main(["article", "--scheduled"]) == 0
    assert [p["status"] for p in wp.posts.values()] == ["draft"]


def test_scheduled_live_with_second_key_publishes_after_gates(env, calls):
    # Not shipped: shows what approving scheduled_live later would do.
    wp, tmp, cfg = env
    cfg["publishing"]["scheduled_live"] = True
    assert run.main(["article", "--scheduled"]) == 0
    assert [p["status"] for p in wp.posts.values()] == ["publish"]


@pytest.mark.parametrize("extra", [["--pillar", "pdf", "--topic", FIRST["topic"]], ["--article-file", "x.json"],
                                   ["--dry-run"], ["--force"], ["--refresh-fact-check"]])
def test_scheduled_run_takes_no_manual_options(env, calls, extra):
    wp, tmp, _ = env
    assert run.main(["article", "--scheduled", *extra]) == 7
    assert calls == [] and wp.posts == {} and sched_log() == []


# ------------------------------------------------------------ topic selection
def test_picks_first_calendar_topic_not_yet_attempted(env):
    checkpoint("a", pillar=FIRST["pillar"], topic=FIRST["topic"], final="held_for_review")
    assert schedule.pick_topic(CFG) == (SECOND, None)


def test_dry_runs_and_refused_runs_do_not_use_up_a_topic(env):
    checkpoint("a", pillar=FIRST["pillar"], topic=FIRST["topic"], final="dry-run")
    checkpoint("b", pillar=FIRST["pillar"], topic=FIRST["topic"], final="refused")
    assert schedule.pick_topic(CFG) == (FIRST, None)


def test_logged_calendar_topic_counts_as_attempted(env):
    log("draft", 30, pillar=FIRST["pillar"], calendar_topic=FIRST["topic"])
    assert schedule.pick_topic(CFG) == (SECOND, None)


def test_held_topic_is_not_regenerated_and_next_topic_runs(env, calls):
    wp, tmp, _ = env
    checkpoint("old", pillar=FIRST["pillar"], topic=FIRST["topic"], final="held_for_review")
    log("held_for_review", 30, run_id="old")
    run.main(["article", "--scheduled"])
    assert calls[0][1] == SECOND["pillar"] and calls[0][2][0].startswith(SECOND["topic"])


def test_no_eligible_topic_skips_without_claude_or_wordpress(env, calls):
    wp, tmp, _ = env
    for n, e in enumerate(CFG["calendar"]):
        checkpoint(f"r{n:02}", pillar=e["pillar"], topic=e["topic"], final="draft")
    assert schedule.pick_topic(CFG) is None
    assert run.main(["article", "--scheduled"]) == 0
    assert calls == [] and wp.calls == [] and sched_log() == []


# ------------------------------------------------------------ resume
def test_unfinished_calendar_run_is_resumed_without_regenerating(env, calls):
    wp, tmp, _ = env
    checkpoint("20261019T000000000000Z", pillar=FIRST["pillar"], topic=FIRST["topic"], brief="brief",
               article=make_article(), usages=[{"stage": "research"}, {"stage": "write"}])
    assert run.main(["article", "--scheduled"]) == 0
    assert calls == [("fact_check",)]  # research and writing were reused, not paid for again
    assert ledger.read_log()[-1]["run_id"] == "20261019T000000000000Z"
    assert len(wp.posts) == 1


def test_unfinished_run_without_a_calendar_topic_is_left_alone(env, calls):
    checkpoint("20261019T000000000000Z", pillar="pdf", brief="brief")  # e.g. an old manual run
    assert schedule.pick_topic(CFG) == (FIRST, None)


def test_resumed_run_that_already_created_its_post_does_not_create_another(env, calls):
    wp, tmp, _ = env
    assert run.main(["article", "--scheduled"]) == 0
    rid = ledger.read_log()[-1]["run_id"]
    state = json.loads((ledger.RUNS / rid / "state.json").read_text())
    del state["stages"]["final"]  # pretend the runner died right after WordPress saved the draft
    (ledger.RUNS / rid / "state.json").write_text(json.dumps(state))
    ledger.LOG.write_text("")
    schedule._log_path().write_text("")
    assert run.main(["article", "--scheduled"]) == 0
    assert len(wp.posts) == 1 and len(calls) == 3


# ------------------------------------------------------------ 20-hour gap
@pytest.mark.parametrize("status", schedule.ATTEMPT_STATUSES)
def test_any_attempt_in_last_20_hours_skips_without_claude(env, calls, status):
    wp, tmp, _ = env
    log(status, 19.5)
    assert run.main(["article", "--scheduled"]) == 0
    assert calls == [] and wp.calls == [] and sched_log() == []


def test_attempt_older_than_20_hours_allows_a_run(env, calls):
    log("held_for_review", 20.5)
    assert run.main(["article", "--scheduled"]) == 0
    assert calls


def test_crashed_scheduled_attempt_counts_towards_the_gap(env, calls, monkeypatch):
    wp, tmp, _ = env
    def boom(*a, **k):
        raise RuntimeError("API timeout")
    monkeypatch.setattr(run.writer, "research_brief", boom)
    with pytest.raises(RuntimeError):
        run.main(["article", "--scheduled"])
    assert sched_log()[-1]["status"] == "error"
    assert schedule.skip_reason(CFG).startswith("Skipped: the last attempt (error)")


def test_attempt_that_never_finished_counts(env):
    schedule._append({"event": "started", "attempt": "x", "run_id": "x", "date": ago(2)})
    assert "(unfinished)" in schedule.skip_reason(CFG)


def test_manual_runs_keep_their_published_only_spacing(env, calls):
    # Unchanged on purpose: a held attempt doesn't block the owner's manual live test.
    wp, tmp, _ = env
    log("held_for_review", 1)
    assert run.main(["article", "--pillar", FIRST["pillar"], "--topic", FIRST["topic"]]) == 0
    assert [p["status"] for p in wp.posts.values()] == ["publish"]


# ------------------------------------------------------------ pause after 2 unsuccessful attempts
def test_two_unsuccessful_attempts_in_a_row_pause_scheduled_runs(env, calls):
    wp, tmp, _ = env
    log("held_for_review", 50)
    log("failed_verification", 26)
    assert schedule.skip_reason(CFG).startswith("Paused: the last 2 attempts")
    assert run.main(["article", "--scheduled"]) == 0
    assert calls == [] and wp.calls == []


def test_crashes_count_as_unsuccessful(env):
    schedule._append({"event": "started", "attempt": "a", "run_id": "a", "date": ago(50)})
    schedule._append({"event": "finished", "attempt": "a", "date": ago(50), "status": "error"})
    schedule._append({"event": "started", "attempt": "b", "run_id": "b", "date": ago(26)})
    assert schedule.skip_reason(CFG).startswith("Paused")


def test_a_success_between_failures_does_not_pause(env):
    log("held_for_review", 75)
    log("draft", 50)
    log("held_duplicate", 26)
    assert schedule.skip_reason(CFG) is None


def test_resume_schedule_clears_the_pause_but_not_the_gap(env, calls):
    wp, tmp, _ = env
    log("held_for_review", 50)
    log("held_for_review", 26)
    assert run.main(["resume-schedule"]) == 0
    assert calls == [] and wp.calls == []
    assert schedule.skip_reason(CFG) is None
    assert run.main(["article", "--scheduled"]) == 0 and calls


def test_skips_happen_before_any_claude_call_even_with_no_api_key(env, monkeypatch):
    wp, tmp, _ = env
    monkeypatch.setattr(run.writer, "api_key_problem", lambda: "is not set")
    log("draft", 2)
    assert run.main(["article", "--scheduled"]) == 0
    assert sched_log() == []
