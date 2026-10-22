import json
from datetime import timedelta

import pytest

from socialpub.platforms import mock as mockmod
from socialpub.runner import heal_stuck, run
from socialpub.timeutil import iso
from conftest import FakeHttp, make_store, post

H = FakeHttp()


def go(store, now, **kw):
    return run(store, http=H, now=now, sleep=lambda s: None, **kw)


def state(store, pid="hello-post"):
    return store.load_state(pid)


def test_full_flow_draft_to_provider_confirmation(store, now):
    p = post(); p["intent"] = "draft"; p.pop("scheduled"); store.save_post(p)
    assert go(store, now)["published"] == []                       # a draft is never published
    p["intent"] = "scheduled"; p["scheduled"] = {"local": "2026-10-25T10:30", "tz": "Asia/Tokyo"}; store.save_post(p)
    s = go(store, now)
    assert len(s["published"]) == 1
    st = state(store)["targets"]["m1"]
    assert st["status"] == "published" and st["provider_post_id"] == "m1" and st["url"].startswith("https://mock.example/")
    assert mockmod.STORE["m1"][0]["text"] == "Hello world"
    events = [e["event"] for e in store.read_log()]
    assert events.index("attempt") < events.index("published")      # write-ahead record comes first


def test_future_post_waits(store, now):
    store.save_post(post(local="2026-10-25T23:00"))
    s = go(store, now)
    assert s["published"] == [] and not mockmod.STORE.get("m1")
    s = go(store, now + timedelta(hours=3))
    assert len(s["published"]) == 1


def test_running_twice_does_not_post_twice(store, now):
    store.save_post(post())
    go(store, now); go(store, now); go(store, now + timedelta(hours=1))
    assert len(mockmod.STORE["m1"]) == 1


def test_editing_after_publish_does_not_repost(store, now):
    store.save_post(post()); go(store, now)
    p = store.load_post("hello-post"); p["primary"] = "A different caption"; store.save_post(p)
    go(store, now + timedelta(hours=1))
    assert len(mockmod.STORE["m1"]) == 1


def test_dry_run_writes_nothing_and_posts_nothing(store, now):
    store.save_post(post())
    s = go(store, now, dry_run=True)
    assert len(s["would_publish"]) == 1 and not store.states.exists() and not store.log_path.exists() and not mockmod.STORE.get("m1")


def test_two_targets_one_fails_permanently_other_published(store, now):
    store.save_post(post(accounts=("m1", "m2")))
    mockmod.SCRIPT["m2"] = ["permanent"]
    s = go(store, now)
    t = state(store)["targets"]
    assert t["m1"]["status"] == "published" and t["m2"]["status"] == "failed" and t["m2"]["error_kind"] == "permanent"
    go(store, now + timedelta(days=1)); go(store, now + timedelta(days=2))
    assert len(mockmod.STORE["m1"]) == 1 and not mockmod.STORE.get("m2")        # no auto-retry, no repost


def test_transient_retries_with_backoff_then_gives_up(store, now):
    store.save_post(post(local="2026-10-25T20:30"))                 # due at 11:30 UTC
    mockmod.SCRIPT["m1"] = ["transient", "transient", "transient", "ok"]
    t0 = now
    s = go(store, t0)
    assert s["failed"][0]["will_retry_at"]
    s = go(store, t0 + timedelta(minutes=5))                         # too early: waiting
    assert s["retry_waiting"] and state(store)["targets"]["m1"]["attempts"] == 1
    go(store, t0 + timedelta(minutes=20))                            # attempt 2 (15 min wait passed)
    go(store, t0 + timedelta(minutes=20 + 61))                       # attempt 3
    tg = state(store)["targets"]["m1"]
    assert tg["attempts"] == 3 and tg["status"] == "failed" and tg["error_kind"] == "permanent" and "gave up" in tg["last_error"]
    go(store, t0 + timedelta(days=1))                                # never a 4th attempt
    assert state(store)["targets"]["m1"]["attempts"] == 3 and not mockmod.STORE.get("m1")


def test_transient_then_success(store, now):
    store.save_post(post())
    mockmod.SCRIPT["m1"] = ["transient", "ok"]
    go(store, now); go(store, now + timedelta(minutes=20))
    assert state(store)["targets"]["m1"]["status"] == "published" and len(mockmod.STORE["m1"]) == 1


def test_uncertain_is_never_auto_retried(store, now):
    store.save_post(post())
    mockmod.SCRIPT["m1"] = ["uncertain", "ok"]
    go(store, now)
    assert state(store)["targets"]["m1"]["status"] == "uncertain"
    go(store, now + timedelta(hours=5)); go(store, now + timedelta(days=2))
    assert not mockmod.STORE.get("m1")


def test_uncertain_but_platform_has_the_post_is_adopted_not_duplicated(store, now):
    store.save_post(post())
    mockmod.SCRIPT["m1"] = ["uncertain_created"]
    s = go(store, now)
    tg = state(store)["targets"]["m1"]
    assert tg["status"] == "published" and tg["adopted"] and len(mockmod.STORE["m1"]) == 1 and s["published"]


def test_crash_between_write_ahead_and_result_heals_to_uncertain(store, now, monkeypatch):
    store.save_post(post())
    def boom(self, req): raise KeyboardInterrupt()
    monkeypatch.setattr(mockmod.MockAdapter, "publish", boom)
    with pytest.raises(KeyboardInterrupt):
        go(store, now)
    assert state(store)["targets"]["m1"]["status"] == "publishing"         # the attempt was recorded before the call
    monkeypatch.undo()
    go(store, now + timedelta(minutes=5))                                  # too soon to judge: left alone
    assert state(store)["targets"]["m1"]["status"] == "publishing" and not mockmod.STORE.get("m1")
    go(store, now + timedelta(minutes=45))
    assert state(store)["targets"]["m1"]["status"] == "uncertain" and not mockmod.STORE.get("m1")


def test_unexpected_exception_is_uncertain_not_failed(store, now, monkeypatch):
    store.save_post(post())
    monkeypatch.setattr(mockmod.MockAdapter, "publish", lambda self, req: 1 / 0)
    go(store, now)
    assert state(store)["targets"]["m1"]["status"] == "uncertain"


def test_too_late_posts_are_not_published_by_surprise(store, now):
    store.save_post(post(local="2026-10-20T10:00"))                      # 5 days late
    s = go(store, now)
    assert not mockmod.STORE.get("m1") and "missed its time" in state(store)["targets"]["m1"]["last_error"]
    store.accounts()   # still loads
    # publish_now overrides deliberately
    (store.commands).mkdir(parents=True, exist_ok=True)
    (store.commands / "a.json").write_text(json.dumps({"action": "retry", "post_id": "hello-post"}))
    (store.commands / "b.json").write_text(json.dumps({"action": "publish_now", "post_id": "hello-post"}))
    go(store, now)
    assert len(mockmod.STORE["m1"]) == 1


def test_duplicate_content_to_same_account_is_skipped(store, now):
    store.save_post(post("first-post")); store.save_post(post("second-post"))
    s = go(store, now)
    assert len(mockmod.STORE["m1"]) == 1 and s["skipped"] and "duplicate" in s["skipped"][0]["why"]


def test_duplicate_guard_expires_after_window(store, now):
    store.save_post(post("first-post")); go(store, now)
    store.save_post(post("second-post", local="2026-10-26T20:00"))
    go(store, now + timedelta(hours=30))
    assert len(mockmod.STORE["m1"]) == 2


def test_cancel_before_publication(store, now):
    store.save_post(post(local="2026-10-25T23:00")); go(store, now)
    store.commands.mkdir(parents=True, exist_ok=True)
    (store.commands / "c.json").write_text(json.dumps({"action": "cancel", "post_id": "hello-post"}))
    s = go(store, now + timedelta(hours=5))
    assert s["commands"] == ["cancel:hello-post"] and not mockmod.STORE.get("m1")
    assert not list(store.commands.glob("*.json"))


def test_retry_command_resets_failed_and_requires_confirmation_for_uncertain(store, now):
    store.save_post(post()); mockmod.SCRIPT["m1"] = ["uncertain"]; go(store, now)
    store.commands.mkdir(parents=True, exist_ok=True)
    (store.commands / "r1.json").write_text(json.dumps({"action": "retry", "post_id": "hello-post"}))
    go(store, now)
    assert state(store)["targets"]["m1"]["status"] == "uncertain" and not mockmod.STORE.get("m1")
    (store.commands / "r2.json").write_text(json.dumps({"action": "retry", "post_id": "hello-post", "confirm_not_posted": True}))
    go(store, now)
    assert state(store)["targets"]["m1"]["status"] == "published" and len(mockmod.STORE["m1"]) == 1


def test_mark_published_records_manual_confirmation(store, now):
    store.save_post(post()); mockmod.SCRIPT["m1"] = ["uncertain"]; go(store, now)
    store.commands.mkdir(parents=True, exist_ok=True)
    (store.commands / "m.json").write_text(json.dumps({"action": "mark_published", "post_id": "hello-post", "account": "m1", "url": "https://x/1"}))
    go(store, now)
    t = state(store)["targets"]["m1"]
    assert t["status"] == "published" and t["manual"] and not mockmod.STORE.get("m1")


def test_bad_command_does_not_stop_the_run(store, now):
    store.save_post(post()); store.commands.mkdir(parents=True, exist_ok=True)
    (store.commands / "bad.json").write_text("{nope")
    (store.commands / "ghost.json").write_text(json.dumps({"action": "cancel", "post_id": "ghost-post"}))
    s = go(store, now)
    assert len(s["published"]) == 1 and not list(store.commands.glob("*.json"))


def test_invalid_post_is_reported_not_published(tmp_path, now):
    s = make_store(tmp_path, accounts=[{"id": "m1", "platform": "mock"}, {"id": "xx", "platform": "x"}])
    s.save_post(post(accounts=("m1", "xx")))
    r = go(s, now)
    assert r["invalid"] and not mockmod.STORE.get("m1")


def test_per_platform_daily_cap_defers(tmp_path, now, monkeypatch):
    from socialpub import rules
    monkeypatch.setitem(rules.PLATFORMS["mock"], "daily_cap", 2)
    s = make_store(tmp_path)
    s.save_post(post("one-post", primary="a")); s.save_post(post("two-post", primary="b"))
    r = go(s, now)
    assert len(mockmod.STORE["m1"]) == 1 and any("close to the" in x["why"] for x in r["skipped"])


def test_missing_secret_fails_permanently_without_leaking(tmp_path, now):
    s = make_store(tmp_path, accounts=[{"id": "bsky", "platform": "bluesky", "secrets": {"identifier": "BSKY_ID", "app_password": "BSKY_PW"}}])
    s.save_post(post(accounts=("bsky",)))
    r = go(s, now, secrets={"BSKY_ID": "me.bsky.social"})
    t = state(s)["targets"]["bsky"]
    assert t["status"] == "failed" and t["error_kind"] == "permanent" and "BSKY_PW" in t["last_error"] and "BSKY_ID" not in t["last_error"].replace("BSKY_PW", "")
    assert H.calls == []


def test_secret_values_never_reach_state_or_log(tmp_path, now, monkeypatch):
    s = make_store(tmp_path)
    s.save_post(post())
    from socialpub.errors import PublishError
    def bad(self, req): raise PublishError("permanent", "rejected with token SUPERSECRET-TOKEN-123")
    monkeypatch.setattr(mockmod.MockAdapter, "publish", bad)
    go(s, now, secrets={"K": "SUPERSECRET-TOKEN-123"})
    blob = (s.states / "hello-post.json").read_text() + s.log_path.read_text()
    assert "SUPERSECRET-TOKEN-123" not in blob


def test_checkpoint_called_before_platform_call(store, now):
    store.save_post(post()); order = []
    orig = mockmod.MockAdapter.publish
    def spy(self, req): order.append("platform"); return orig(self, req)
    mockmod.MockAdapter.publish = spy
    try:
        go(store, now, checkpoint=lambda m: order.append("cp:" + m.split()[1]))
    finally:
        mockmod.MockAdapter.publish = orig
    assert order.index("cp:attempt") < order.index("platform") < order.index("cp:result")


def test_only_post_filter(store, now):
    store.save_post(post("one-post", primary="a")); store.save_post(post("two-post", primary="b"))
    go(store, now, only_post="two-post")
    assert [p["text"] for p in mockmod.STORE["m1"]] == ["b"]


def test_target_added_after_first_run_is_published_but_old_one_is_not_resent(tmp_path, now):
    s = make_store(tmp_path); s.save_post(post()); go(s, now)
    p = s.load_post("hello-post"); p["targets"].append({"account": "m2"}); s.save_post(p)
    go(s, now + timedelta(hours=1))
    assert len(mockmod.STORE["m1"]) == 1 and len(mockmod.STORE["m2"]) == 1


def test_dst_gap_post_is_invalid_not_posted_an_hour_off(store, now):
    store.save_post(post(local="2026-03-29T02:30", tz="Europe/Paris"))
    r = go(store, now)
    assert r["invalid"] and not mockmod.STORE.get("m1")


def test_heal_stuck_leaves_recent_attempts_alone(store, now):
    store.save_post(post())
    st = {"id": "hello-post", "version": 1, "cancelled": False, "force_now": False, "content_hash": "", "validation_error": "",
          "targets": {"m1": {"status": "publishing", "attempts": 1, "started_at": iso(now - timedelta(minutes=5))}}}
    store.save_state(st)
    assert heal_stuck(store, now, 30) == []
    assert heal_stuck(store, now + timedelta(minutes=40), 30) == ["hello-post/m1"]


def test_one_corrupt_file_does_not_block_other_posts(store, now):
    store.save_post(post("good-post"))
    (store.posts / "broken-one.json").write_text("{nope")
    (store.states).mkdir(parents=True, exist_ok=True); (store.states / "good-post.json").write_text("not json")
    r = go(store, now)
    assert any(i["post"] == "broken-one" for i in r["invalid"]) and any(i["post"] == "good-post" for i in r["invalid"])
    store.states.joinpath("good-post.json").unlink(); store.save_post(post("fine-post", primary="other"))
    assert len(go(store, now)["published"]) >= 1
