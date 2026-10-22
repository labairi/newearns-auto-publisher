import json
from datetime import datetime, timezone

import pytest

from socialpub.importer import import_posts, parse_ai_output
from socialpub.store import Store, StoreError, content_hash, display_status, redact
from socialpub.timeutil import TimeError, to_utc
from socialpub.validate import derived_text, validate_accounts, validate_post
from conftest import make_store, post

ACC = {"b": {"id": "b", "platform": "bluesky"}, "i": {"id": "i", "platform": "instagram"},
       "x": {"id": "x", "platform": "x"}, "y": {"id": "y", "platform": "youtube"}, "t": {"id": "t", "platform": "threads"}}


# ---------- time zones ----------
def test_fixed_offset_zone_to_utc():
    assert to_utc("2026-10-25T10:30", "Asia/Tokyo") == datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc)

def test_casablanca_uses_the_zone_database_not_a_hardcoded_offset():
    # Morocco's offset changes (Ramadan), and old tzdata copies disagree, so compare with the database itself.
    from zoneinfo import ZoneInfo
    got = to_utc("2026-10-25T10:30", "Africa/Casablanca")
    want = datetime(2026, 10, 25, 10, 30, tzinfo=ZoneInfo("Africa/Casablanca")).astimezone(timezone.utc)
    assert got == want

def test_dst_gap_rejected():
    with pytest.raises(TimeError, match="does not exist"):
        to_utc("2026-03-29T02:30", "Europe/Paris")

def test_dst_overlap_rejected():
    with pytest.raises(TimeError, match="twice"):
        to_utc("2026-10-25T02:30", "Europe/Paris")

def test_normal_paris_time_summer_and_winter():
    assert to_utc("2026-07-01T12:00", "Europe/Paris").hour == 10
    assert to_utc("2026-12-01T12:00", "Europe/Paris").hour == 11

@pytest.mark.parametrize("local,tz", [("2026-10-25T10:30", "Mars/Base"), ("not a time", "UTC"), ("2026-10-25T10:30+01:00", "UTC")])
def test_bad_time_inputs(local, tz):
    with pytest.raises(TimeError):
        to_utc(local, tz)


# ---------- validation ----------
def test_valid_post():
    assert validate_post(post(accounts=("b",)), ACC) == []

def test_unknown_field_and_bad_id():
    e = validate_post(post(accounts=("b",), id="X", bogus=1), ACC)
    assert any("unknown field" in x for x in e) and any("id must be" in x for x in e)

def test_over_limit_text():
    e = validate_post(post(accounts=("b",), primary="x" * 301), ACC)
    assert any("301 characters" in x for x in e)

def test_per_platform_version_overrides():
    p = post(accounts=("b",), primary="x" * 301, versions={"bluesky": {"text": "short"}})
    assert validate_post(p, ACC) == []

def test_derived_text_fits_by_dropping_hashtags():
    p = {"primary": "a" * 290, "cta": "", "hashtags": ["one", "two"]}
    assert derived_text(p, "bluesky")[1] == "a" * 290

def test_instagram_requires_media_and_jpeg():
    assert any("needs an image" in x for x in validate_post(post(accounts=("i",)), ACC))
    p = post(accounts=("i",), media=[{"type": "image", "url": "https://e.com/a.png"}])
    assert any("JPEG" in x for x in validate_post(p, ACC))
    p["media"][0]["url"] = "https://e.com/a.jpg"
    assert validate_post(p, ACC) == []

def test_invalid_media_url_and_type():
    e = validate_post(post(accounts=("b",), media=[{"type": "image", "url": "http://e.com/a.jpg"}, {"type": "gif", "url": "x"}]), ACC)
    assert any("public https" in x for x in e) and any("'image' or 'video'" in x for x in e)

def test_too_many_images_and_video_mixing():
    imgs = [{"type": "image", "url": f"https://e.com/{n}.jpg"} for n in range(5)]
    assert any("at most 4" in x for x in validate_post(post(accounts=("b",), media=imgs), ACC))
    mix = [{"type": "image", "url": "https://e.com/a.jpg"}, {"type": "video", "url": "https://e.com/a.mp4"}]
    assert any("cannot mix" in x or "does not take video" in x for x in validate_post(post(accounts=("t",), media=mix), ACC))

def test_unbuilt_platform_is_rejected_not_skipped():
    e = validate_post(post(accounts=("x",)), ACC)
    assert any("not built yet" in x for x in e)

def test_schedule_requires_fields_and_valid_time():
    p = post(accounts=("b",)); p["scheduled"] = {"local": "2026-03-29T02:30", "tz": "Europe/Paris"}
    assert any("does not exist" in x for x in validate_post(p, ACC))
    p["scheduled"] = {}
    assert any("needs scheduled.local" in x for x in validate_post(p, ACC))

def test_draft_needs_no_schedule():
    p = post(accounts=("b",), intent="draft"); p.pop("scheduled")
    assert validate_post(p, ACC) == []

def test_duplicate_and_unknown_target():
    assert any("twice" in x for x in validate_post(post(accounts=("b", "b")), ACC))
    assert any("unknown account" in x for x in validate_post(post(accounts=("nope",)), ACC))

def test_accounts_reject_secret_values():
    bad = {"accounts": [{"id": "b1", "platform": "bluesky", "secrets": {"app_password": "A" * 45}}]}
    assert any("looks like a real secret" in x for x in validate_accounts(bad))
    ok = {"accounts": [{"id": "b1", "platform": "bluesky", "secrets": {"app_password": "BSKY_APP_PW"}}]}
    assert validate_accounts(ok) == []


# ---------- store / queue ----------
def test_create_edit_and_list_posts(tmp_path):
    s = make_store(tmp_path)
    s.save_post(post("one-post")); s.save_post(post("two-post"))
    assert s.post_ids() == ["one-post", "two-post"]
    p = s.load_post("one-post"); p["primary"] = "edited"; s.save_post(p)
    assert s.load_post("one-post")["primary"] == "edited"
    assert "created_at" in s.load_post("one-post") and "updated_at" in s.load_post("one-post")

@pytest.mark.parametrize("bad", ["../etc/passwd", "A", "a/b", ""])
def test_unsafe_ids_refused(tmp_path, bad):
    with pytest.raises(StoreError):
        make_store(tmp_path).load_post(bad)

def test_atomic_write_leaves_no_temp_files(tmp_path):
    s = make_store(tmp_path); s.save_post(post("one-post"))
    assert [p.name for p in s.posts.iterdir()] == ["one-post.json"]

def test_corrupt_json_is_a_clear_error(tmp_path):
    s = make_store(tmp_path); s.posts.mkdir(parents=True); (s.posts / "bad-one.json").write_text("{nope")
    with pytest.raises(StoreError, match="not valid JSON"):
        s.load_post("bad-one")

def test_content_hash_changes_with_text_and_media():
    p = post(); a = content_hash(p, {"m1": "x"})
    assert a != content_hash(p, {"m1": "y"})
    assert a != content_hash({**p, "media": [{"type": "image", "url": "https://e.com/a.jpg"}]}, {"m1": "x"})

def test_display_status():
    assert display_status({"intent": "draft"}, None) == "draft"
    assert display_status({"intent": "scheduled"}, None) == "scheduled"
    t = lambda *s: {"targets": {str(i): {"status": x} for i, x in enumerate(s)}}  # noqa: E731
    assert display_status({}, t("published", "published")) == "published"
    assert display_status({}, t("published", "failed")) == "partial"
    assert display_status({}, t("uncertain")) == "uncertain"
    assert display_status({}, t("failed")) == "failed"
    assert display_status({}, {**t("pending"), "cancelled": True}) == "cancelled"


# ---------- secrets ----------
def test_redact_known_values_and_token_shapes():
    out = redact("bad token SECRETVALUE123 and access_token=abcd1234efgh5678 Bearer zzzzzzzzzzzz", ["SECRETVALUE123"])
    assert "SECRETVALUE123" not in out and "abcd1234efgh5678" not in out and "zzzzzzzzzzzz" not in out

def test_log_redacts(tmp_path):
    s = make_store(tmp_path); s.secrets = ["TOPSECRET99"]
    s.log("x", "pid-one", error="boom TOPSECRET99")
    assert "TOPSECRET99" not in s.log_path.read_text()


# ---------- AI import ----------
def test_import_makes_drafts_only_and_never_overwrites(tmp_path):
    s = make_store(tmp_path)
    txt = '```json\n[{"title":"A post","primary":"hi","targets":[{"account":"m1"}],"scheduled":{"local":"2026-10-25T10:00","tz":"UTC"},"intent":"scheduled","evil":1}]\n```'
    r1 = import_posts(s, txt); r2 = import_posts(s, txt)
    assert len(r1["saved"]) == 1 and len(r2["saved"]) == 1 and r1["saved"] != r2["saved"]
    p = s.load_post(r1["saved"][0])
    assert p["intent"] == "draft" and "scheduled" not in p and "evil" not in p

def test_import_rejects_invalid(tmp_path):
    r = import_posts(make_store(tmp_path), '{"posts":[{"title":"x","primary":"hi","targets":[{"account":"ghost"}]}]}')
    assert r["saved"] == [] and r["rejected"][0]["errors"]

def test_parse_ai_output_errors():
    with pytest.raises(ValueError):
        parse_ai_output("sorry, I cannot")
    with pytest.raises(json.JSONDecodeError):
        parse_ai_output("[{bad json")
