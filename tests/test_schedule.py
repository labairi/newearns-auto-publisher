"""The daily scheduled run: topic choice, the 20-hour gap, the pause rule, resume, live vs draft.

All offline: WordPress is a fake, Claude calls are fakes that count themselves.
"""
import copy
import json
from datetime import datetime, timedelta, timezone

import pytest
import yaml

from publisher import ledger, run, schedule
from tests.test_pipeline import CFG, env, make_article, write  # noqa: F401

# Most tests here are about topic order, the gap and the pause, so they run with the weekly
# product rule off; the topic-mix tests at the end switch it on.
FLAT = copy.deepcopy(CFG)
FLAT["topic_mix"]["product_every_days"] = 0
FIRST = CFG["calendar"][0]
SECOND = CFG["calendar"][1]
PRODUCT = schedule.product_pillars(CFG)


@pytest.fixture(autouse=True)
def flat_mix(request):
    if "env" in request.fixturenames:
        request.getfixturevalue("env")[2]["topic_mix"]["product_every_days"] = 0


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


def test_scheduled_workflow_run_is_the_scheduled_command_and_gets_the_second_key_only_on_main():
    y = workflow()
    expr = y["jobs"]["run"]["env"]["ALLOW_PUBLISH"]
    # "true" only on main: for a schedule, or a manual `article` run with the box ticked.
    assert expr.startswith("${{ (github.event_name == 'schedule' || (inputs.command == 'article' && "
                           "inputs.allow_publish)) && github.ref == 'refs/heads/main' && 'true'")
    # A run that waited in the concurrency group checks out the branch tip, so it sees the last run's log.
    checkout = next(s for s in y["jobs"]["run"]["steps"] if s.get("uses", "").startswith("actions/checkout"))
    assert checkout["with"]["ref"] == "${{ github.ref_name }}"
    assert y["concurrency"] == {"group": "publisher", "cancel-in-progress": False}
    script = next(s for s in y["jobs"]["run"]["steps"] if s.get("name") == "Run")["run"]
    head = script.split("plan=()")[0]
    assert 'if [ "${{ github.event_name }}" = schedule ]' in head
    assert "python -m publisher.run article --scheduled\n" in head and "exit" in head
    assert "resume-schedule)  python -m publisher.run resume-schedule ;;" in script
    assert "resume-schedule" in y[True]["workflow_dispatch"]["inputs"]["command"]["options"]
    # The log and checkpoints are committed after scheduled runs too (inputs.command is empty then).
    save = next(s for s in y["jobs"]["run"]["steps"] if s.get("name", "").startswith("Save"))
    assert save["if"] == "always() && inputs.command != 'check-setup'"


def test_shipped_config_allows_live_scheduled_runs_with_daily_spacing():
    assert CFG["publishing"]["scheduled_live"] is True and CFG["publishing"]["mode"] == "publish"
    assert CFG["publishing"]["min_hours_between_posts"] == 20


def test_schedule_is_11_00_morocco_time():
    # Morocco is on UTC+0 all year since 2026-09-20 (no DST, no Ramadan change), so 11:00 local = 11:00 UTC.
    crons = [c["cron"] for c in workflow()[True]["schedule"]]
    assert crons == ["0 11 * * *", "30 11 * * *"]


# ------------------------------------------------------------ draft-only and one article
def test_scheduled_live_off_saves_one_draft_even_with_publish_mode_and_second_key(env, calls):
    wp, tmp, cfg = env  # mode publish, ALLOW_PUBLISH=true
    cfg["publishing"]["scheduled_live"] = False  # the off switch
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
    assert cfg["publishing"]["scheduled_live"] is True
    monkeypatch.delenv("ALLOW_PUBLISH")  # e.g. a scheduled run that is not on main
    assert run.main(["article", "--scheduled"]) == 0
    assert [p["status"] for p in wp.posts.values()] == ["draft"]


def test_shipped_scheduled_run_publishes_one_article_live_after_all_gates(env, calls):
    wp, tmp, cfg = env  # shipped config, ALLOW_PUBLISH=true as on main
    assert run.main(["article", "--scheduled"]) == 0
    assert [p["status"] for p in wp.posts.values()] == ["publish"]
    assert [c[0] for c in calls] == ["research", "write", "fact_check"]  # exactly one article
    entry = ledger.read_log()[-1]
    assert entry["status"] == "published" and entry["calendar_topic"] == FIRST["topic"]
    state = json.loads((ledger.RUNS / entry["run_id"] / "state.json").read_text())["stages"]
    assert len(state["checks"]) == 36 and all(c["passed"] for c in state["checks"])
    # The next day's run is blocked by the 20-hour gap, so there is never a second post the same day.
    assert run.main(["article", "--scheduled"]) == 0
    assert len(wp.posts) == 1 and len(calls) == 3


def test_live_scheduled_run_that_fails_url_verification_goes_back_to_pending(env, calls, monkeypatch):
    wp, tmp, _ = env
    monkeypatch.setattr(run, "public_url_ok", lambda url, t: (False, "HTTP 404"))
    assert run.main(["article", "--scheduled"]) == 5
    assert [p["status"] for p in wp.posts.values()] == ["pending"]
    assert sched_log()[-1]["status"] == "failed_verification"


@pytest.mark.parametrize("extra", [["--pillar", "pdf", "--topic", FIRST["topic"]], ["--article-file", "x.json"],
                                   ["--dry-run"], ["--force"], ["--refresh-fact-check"]])
def test_scheduled_run_takes_no_manual_options(env, calls, extra):
    wp, tmp, _ = env
    assert run.main(["article", "--scheduled", *extra]) == 7
    assert calls == [] and wp.posts == {} and sched_log() == []


# ------------------------------------------------------------ topic selection
def test_picks_first_calendar_topic_not_yet_attempted(env):
    checkpoint("a", pillar=FIRST["pillar"], topic=FIRST["topic"], final="held_for_review")
    assert schedule.pick_topic(FLAT) == (SECOND, None)


def test_dry_runs_and_refused_runs_do_not_use_up_a_topic(env):
    checkpoint("a", pillar=FIRST["pillar"], topic=FIRST["topic"], final="dry-run")
    checkpoint("b", pillar=FIRST["pillar"], topic=FIRST["topic"], final="refused")
    assert schedule.pick_topic(FLAT) == (FIRST, None)


def test_logged_calendar_topic_counts_as_attempted(env):
    log("draft", 30, pillar=FIRST["pillar"], calendar_topic=FIRST["topic"])
    assert schedule.pick_topic(FLAT) == (SECOND, None)


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
    assert schedule.pick_topic(FLAT) is None
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
    assert schedule.pick_topic(FLAT) == (FIRST, None)


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


# ------------------------------------------------------------ posts that went live outside the pipeline
def held_post(wp, pid, run_id, entry, hours, published_by_hand_hours=None):
    """A held run of `entry` (log + checkpoint + WordPress post), optionally published by hand later."""
    checkpoint(run_id, pillar=entry["pillar"], topic=entry["topic"], final="held_for_review")
    log("held_for_review", hours, run_id=run_id, post_id=pid, pillar=entry["pillar"], slug=f"slug-{pid}")
    wp.posts[pid] = {"id": pid, "slug": f"slug-{pid}", "status": "pending", "title": entry["topic"],
                     "content": "<p>x</p>", "link": f"https://newearns.com/slug-{pid}/"}
    if published_by_hand_hours is not None:
        wp.posts[pid].update(status="publish", date_gmt=ago(published_by_hand_hours)[:19])


def test_post_published_by_hand_is_logged_once_and_its_topic_is_skipped(env, calls):
    wp, tmp, _ = env
    held_post(wp, 3695, "r1", FIRST, hours=40, published_by_hand_hours=39)
    assert run.main(["article", "--scheduled"]) == 0
    live = [e for e in ledger.read_log() if e.get("post_id") == 3695 and e["status"] == "published"]
    assert len(live) == 1 and live[0]["source"] == "wordpress" and live[0]["calendar_topic"] == FIRST["topic"]
    assert live[0]["date"].startswith(ago(39)[:16])  # when it actually went live, for the 20-hour gap
    assert calls[0][1] == SECOND["pillar"] and calls[0][2][0].startswith(SECOND["topic"])
    assert sum(1 for c in wp.calls if c[0] == "create") == 1  # one new post, not a copy of 3695
    # Running again never logs it twice, and never regenerates or republishes the first topic.
    assert run.main(["article", "--scheduled"]) == 0  # skipped by the 20-hour gap
    assert len([e for e in ledger.read_log() if e.get("post_id") == 3695 and e["status"] == "published"]) == 1
    assert [c[0] for c in calls] == ["research", "write", "fact_check"]


def test_post_published_by_hand_counts_as_a_success_for_the_pause(env, calls):
    wp, tmp, _ = env
    held_post(wp, 3695, "r1", FIRST, hours=40, published_by_hand_hours=39)
    held_post(wp, 3699, "r2", SECOND, hours=25)
    assert schedule.skip_reason(CFG).startswith("Paused")  # what the log alone says
    schedule.reconcile(wp.all_posts())
    assert schedule.skip_reason(CFG) is None  # held, live by hand, held: not 2 failures in a row


def test_post_published_by_hand_recently_still_counts_for_the_20_hour_gap(env, calls):
    wp, tmp, _ = env
    held_post(wp, 3695, "r1", FIRST, hours=40, published_by_hand_hours=3)
    assert run.main(["article", "--scheduled"]) == 0
    assert calls == [] and not any(c[0] == "create" for c in wp.calls)


def test_still_pending_post_is_not_logged_as_published(env, calls):
    wp, tmp, _ = env
    held_post(wp, 3699, "r2", FIRST, hours=40)
    assert schedule.reconcile(wp.all_posts()) == []
    assert schedule.pick_topic(FLAT) == (SECOND, None)  # held topics are still not retried


def test_wordpress_unreachable_stops_before_any_claude_call(env, calls, monkeypatch):
    wp, tmp, _ = env
    def down(**kw):
        raise run.WordPressError("GET /wp/v2/posts -> 503")
    monkeypatch.setattr(wp, "all_posts", down)
    with pytest.raises(run.WordPressError):
        run.main(["article", "--scheduled"])
    assert calls == [] and sched_log() == []


def test_repeated_invocations_take_successive_topics_and_never_republish(env, calls, monkeypatch):
    wp, tmp, _ = env
    n = iter(range(1, 10))
    def article(*a, **k):
        calls.append(("write",))
        i = next(n)
        return make_article(slug=f"compress-pdf-for-email-{i}"), {"stage": "write"}
    monkeypatch.setattr(run.writer, "write_article", article)
    for day in range(3):
        before = sum(1 for c in wp.calls if c[0] == "create")
        assert run.main(["article", "--scheduled"]) in (0, 6)
        assert sum(1 for c in wp.calls if c[0] == "create") - before <= 1  # at most one article per invocation
        # Move the clock on a day: shift every logged date back 24 hours.
        lines = [json.loads(l) for l in ledger.LOG.read_text().splitlines()]
        for e in lines:
            e["date"] = (datetime.fromisoformat(e["date"]) - timedelta(hours=24)).isoformat()
        ledger.LOG.write_text("".join(json.dumps(e) + "\n" for e in lines))
        sched = [json.loads(l) for l in schedule._log_path().read_text().splitlines()]
        for e in sched:
            e["date"] = (datetime.fromisoformat(e["date"]) - timedelta(hours=24)).isoformat()
        schedule._log_path().write_text("".join(json.dumps(e) + "\n" for e in sched))
    researched = [c[2][0].split(" (primary")[0] for c in calls if c[0] == "research"]
    assert researched == [e["topic"] for e in CFG["calendar"][:3]]  # each topic once, in calendar order
    assert len(wp.posts) == 3


# ------------------------------------------------------------ topic mix: one product post a week
def test_shipped_config_has_one_product_post_a_week():
    assert CFG["topic_mix"]["product_every_days"] == 7
    assert PRODUCT == {"pdf", "instagram"}
    assert {"money", "passive", "ai"} <= set(CFG["pillars"]) - PRODUCT
    for name in ("money", "passive", "ai"):
        assert CFG["categories"][CFG["pillars"][name]["category"]]  # an existing category id


def mix(days):
    cfg = copy.deepcopy(CFG)
    cfg["topic_mix"]["product_every_days"] = days
    return cfg


def open_entries(cfg, product):
    used = {(e["pillar"], e["topic"]) for e in cfg["calendar"][:3]}  # the three already published
    return [e for e in cfg["calendar"] if (e["pillar"], e["topic"]) not in used
            and (e["pillar"] in PRODUCT) == product]


def test_product_topic_runs_when_none_in_the_last_7_days(env):
    log("published", 24 * 8, pillar="pdf", calendar_topic=CFG["calendar"][0]["topic"])
    for e in CFG["calendar"][1:3]:
        checkpoint(e["topic"][:8], pillar=e["pillar"], topic=e["topic"], final="published")
    entry, _ = schedule.pick_topic(mix(7))
    assert entry == open_entries(CFG, True)[0]


def test_other_topic_runs_within_7_days_of_a_product_post(env):
    for e in CFG["calendar"][:3]:
        checkpoint(e["topic"][:8], pillar=e["pillar"], topic=e["topic"], final="published")
    log("published", 24 * 3, pillar="instagram")
    entry, _ = schedule.pick_topic(mix(7))
    assert entry == open_entries(CFG, False)[0] and entry["pillar"] not in PRODUCT


def test_product_slot_counts_calendar_days_and_crashed_attempts(env):
    now = datetime(2026, 10, 29, 11, 0, tzinfo=timezone.utc)
    ledger.append_log({"run_id": "p", "date": "2026-10-22T15:15:48+00:00", "status": "published", "pillar": "pdf"})
    assert schedule.product_slot_due(mix(7), now) is True        # 7 calendar days, though < 168 hours
    assert schedule.product_slot_due(mix(7), now - timedelta(days=1)) is False
    schedule._append({"event": "started", "attempt": "x", "run_id": "x", "date": "2026-10-28T11:00:00+00:00",
                      "pillar": "instagram", "topic": "t"})       # crashed: never finished
    assert schedule.product_slot_due(mix(7), now) is False
    assert schedule.product_slot_due(mix(0), now) is True


def test_no_other_topic_left_skips_instead_of_an_early_product_post(env):
    log("published", 24, pillar="pdf")
    cfg = mix(7)
    for n, e in enumerate(cfg["calendar"]):
        if e["pillar"] not in PRODUCT:
            checkpoint(f"o{n:02}", pillar=e["pillar"], topic=e["topic"], final="draft")
    assert schedule.pick_topic(cfg) is None


def test_due_product_slot_with_no_product_topic_left_runs_another_topic(env):
    cfg = mix(7)
    for n, e in enumerate(cfg["calendar"]):
        if e["pillar"] in PRODUCT:
            checkpoint(f"p{n:02}", pillar=e["pillar"], topic=e["topic"], final="draft")
    entry, _ = schedule.pick_topic(cfg)
    assert entry == open_entries(CFG, False)[0]


def test_two_scheduled_weeks_have_one_product_post_each(env, calls, monkeypatch):
    wp, tmp, cfg = env
    cfg["topic_mix"]["product_every_days"] = 7
    monkeypatch.setattr(schedule, "PAUSE_AFTER", 99)  # held fake articles must not pause the run
    for e in CFG["calendar"][:3]:
        checkpoint(e["topic"][:8], pillar=e["pillar"], topic=e["topic"], final="published")
    n = iter(range(1, 30))
    def article(*a, **k):
        calls.append(("write",))
        return make_article(slug=f"post-{next(n)}"), {"stage": "write"}
    monkeypatch.setattr(run.writer, "write_article", article)
    for day in range(14):
        assert run.main(["article", "--scheduled"]) in (0, 6)
        for path in (ledger.LOG, schedule._log_path()):
            if path.exists():
                lines = [json.loads(l) for l in path.read_text().splitlines()]
                for e in lines:
                    e["date"] = (datetime.fromisoformat(e["date"]) - timedelta(hours=24)).isoformat()
                path.write_text("".join(json.dumps(e) + "\n" for e in lines))
    pillars = [c[1] for c in calls if c[0] == "research"]
    assert len(pillars) == 14
    assert [p in PRODUCT for p in pillars] == [True] + [False] * 6 + [True] + [False] * 6
