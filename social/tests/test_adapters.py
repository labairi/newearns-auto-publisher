import json

import pytest
import requests

from socialpub.errors import PublishError
from socialpub.platforms import build_adapter
from socialpub.platforms.base import PublishRequest
from socialpub.platforms.bluesky import facets
from conftest import FakeHttp, Resp

REQ = PublishRequest(post_id="p-one", account_id="a", attempt_id="x1", text="Hello", since_iso="2026-01-01T00:00:00Z")
IMG = {"type": "image", "url": "https://cdn.example/a.jpg", "alt": "a cat"}
VID = {"type": "video", "url": "https://cdn.example/a.mp4"}


def adapter(platform, http, secrets=None, **acc):
    names = {"bluesky": {"identifier": "ID", "app_password": "PW"}, "mastodon": {"access_token": "TOK"},
             "threads": {"access_token": "TOK"}, "facebook_page": {"page_token": "TOK"}, "instagram": {"access_token": "TOK"}}[platform]
    account = {"id": "a", "platform": platform, "secrets": names, **acc}
    sec = secrets if secrets is not None else {"ID": "me.bsky.social", "PW": "pw-pw-pw", "TOK": "tok-123456"}
    return build_adapter(account, sec, http, lambda s: None)


def req(**kw):
    d = dict(REQ.__dict__); d.update(kw); return PublishRequest(**d)


# ---------- Bluesky ----------
def test_bluesky_facets_use_utf8_byte_offsets():
    text = "café ☕ see https://example.com/x now #Tag"
    f = facets(text)
    b = text.encode()
    link = [x for x in f if x["features"][0]["$type"].endswith("link")][0]
    tag = [x for x in f if x["features"][0]["$type"].endswith("tag")][0]
    assert b[link["index"]["byteStart"]:link["index"]["byteEnd"]] == b"https://example.com/x"
    assert b[tag["index"]["byteStart"]:tag["index"]["byteEnd"]] == b"#Tag" and tag["features"][0]["tag"] == "Tag"
    assert link["index"]["byteStart"] != text.index("https")        # char offset would be wrong here

def test_bluesky_trailing_punctuation_not_in_link():
    f = facets("go to https://example.com/x.")
    assert f[0]["features"][0]["uri"] == "https://example.com/x"

def bsky_http(create=None):
    h = FakeHttp()
    h.add("POST", "createSession", Resp(200, {"accessJwt": "JWT", "did": "did:plc:abc", "handle": "me.bsky.social"}))
    h.add("POST", "createRecord", create or Resp(200, {"uri": "at://did:plc:abc/app.bsky.feed.post/3k2", "cid": "c"}))
    return h

def test_bluesky_publish_text():
    h = bsky_http(); r = adapter("bluesky", h).publish(req(text="Hi #x"))
    assert r.provider_id.endswith("/3k2") and r.url == "https://bsky.app/profile/me.bsky.social/post/3k2"
    body = h.to("createRecord")[0][2]["json"]
    assert body["repo"] == "did:plc:abc" and body["record"]["text"] == "Hi #x" and body["record"]["facets"]
    assert h.to("createRecord")[0][2]["headers"]["Authorization"] == "Bearer JWT"

def test_bluesky_publish_image_uploads_blob_with_alt():
    h = bsky_http()
    h.add("GET", "cdn.example", Resp(200, content=b"\xff\xd8jpegbytes"))
    h.add("POST", "uploadBlob", Resp(200, {"blob": {"$type": "blob", "ref": {"$link": "bafy"}}}))
    adapter("bluesky", h).publish(req(media=[IMG]))
    assert h.to("uploadBlob")[0][2]["headers"]["Content-Type"] == "image/jpeg"
    emb = h.to("createRecord")[0][2]["json"]["record"]["embed"]
    assert emb["images"][0]["alt"] == "a cat"

def test_bluesky_video_refused():
    with pytest.raises(PublishError) as e:
        adapter("bluesky", bsky_http()).publish(req(media=[VID]))
    assert e.value.kind == "permanent"

def test_bluesky_bad_password_is_permanent_oauth_failure():
    h = FakeHttp().add("POST", "createSession", Resp(401, {"error": "AuthenticationRequired"}))
    with pytest.raises(PublishError) as e:
        adapter("bluesky", h).publish(req())
    assert e.value.kind == "permanent" and e.value.status == 401

def test_bluesky_timeout_on_create_is_uncertain_and_find_existing_adopts():
    h = bsky_http(create=requests.exceptions.ReadTimeout("slow"))
    with pytest.raises(PublishError) as e:
        adapter("bluesky", h).publish(req(text="Hello"))
    assert e.value.kind == "uncertain"
    h.add("GET", "listRecords", Resp(200, {"records": [{"uri": "at://did:plc:abc/app.bsky.feed.post/9z", "value": {"text": "Hello", "createdAt": "2026-06-01T00:00:00Z"}}]}))
    found = adapter("bluesky", h).find_existing(req(text="Hello"))
    assert found and found.provider_id.endswith("/9z")
    assert adapter("bluesky", h).find_existing(req(text="Other")) is None

def test_bluesky_rate_limit_is_transient_and_5xx_on_create_is_uncertain():
    with pytest.raises(PublishError) as e:
        adapter("bluesky", bsky_http(Resp(429, {}))).publish(req())
    assert e.value.kind == "transient"
    with pytest.raises(PublishError) as e:
        adapter("bluesky", bsky_http(Resp(503, {}))).publish(req())
    assert e.value.kind == "uncertain"

def test_connection_error_before_sending_is_transient():
    h = FakeHttp().add("POST", "createSession", requests.exceptions.ConnectionError("down"))
    with pytest.raises(PublishError) as e:
        adapter("bluesky", h).publish(req())
    assert e.value.kind == "transient"


# ---------- Mastodon ----------
def test_mastodon_idempotency_key_is_stable_per_post_and_account():
    def make():
        return FakeHttp().add("POST", "/api/v1/statuses", Resp(200, {"id": "111", "url": "https://m.example/@me/111"}))
    h1, h2 = make(), make()
    acc = dict(instance="https://m.example")
    r = adapter("mastodon", h1, **acc).publish(req(attempt_id="A")); adapter("mastodon", h2, **acc).publish(req(attempt_id="B"))
    k1 = h1.calls[0][2]["headers"]["Idempotency-Key"]; k2 = h2.calls[0][2]["headers"]["Idempotency-Key"]
    assert k1 == k2 and r.provider_id == "111"
    assert h1.calls[0][2]["data"]["status"] == "Hello" and h1.calls[0][2]["headers"]["Authorization"] == "Bearer tok-123456"

def test_mastodon_requires_https_instance():
    with pytest.raises(PublishError) as e:
        adapter("mastodon", FakeHttp(), instance="http://m.example").publish(req())
    assert e.value.kind == "permanent"

def test_mastodon_async_video_polls_until_ready():
    h = FakeHttp()
    h.add("GET", "cdn.example", Resp(200, content=b"vid"))
    h.add("POST", "/api/v2/media", Resp(202, {"id": "m9"}))
    h.add("GET", "/api/v1/media/m9", [Resp(206, {}), Resp(200, {"id": "m9"})])
    h.add("POST", "/api/v1/statuses", Resp(200, {"id": "5", "url": "u"}))
    adapter("mastodon", h, instance="https://m.example").publish(req(media=[VID]))
    assert len(h.to("/api/v1/media/m9")) == 2
    assert h.to("/statuses")[0][2]["data"]["media_ids[]"] == ["m9"]

def test_mastodon_expired_token_is_permanent():
    h = FakeHttp().add("POST", "/api/v1/statuses", Resp(401, {"error": "invalid token"}))
    with pytest.raises(PublishError) as e:
        adapter("mastodon", h, instance="https://m.example").publish(req())
    assert e.value.kind == "permanent"


# ---------- Threads ----------
def threads_http(publish=None):
    h = FakeHttp()
    h.add("GET", "/me", Resp(200, {"id": "77", "username": "me"}))
    h.add("POST", "/77/threads_publish", publish or Resp(200, {"id": "post1"}))
    h.add("POST", "/77/threads", Resp(200, {"id": "cont1"}))
    h.add("GET", "/post1", Resp(200, {"permalink": "https://threads.net/@me/post/abc"}))
    return h

def test_threads_text_two_step_flow():
    h = threads_http(); r = adapter("threads", h).publish(req())
    create = [c for c in h.calls if c[0] == "POST"]
    assert create[0][1].endswith("/77/threads") and create[0][2]["params"]["media_type"] == "TEXT"
    assert create[1][1].endswith("/77/threads_publish") and create[1][2]["params"]["creation_id"] == "cont1"
    assert r.provider_id == "post1" and r.url.endswith("/abc")

def test_threads_image_container():
    h = threads_http(); adapter("threads", h).publish(req(media=[IMG]))
    p = h.to("/77/threads")[0][2]["params"]
    assert p["media_type"] == "IMAGE" and p["image_url"] == IMG["url"]

def test_threads_video_waits_for_processing_and_reports_errors():
    h = threads_http(); h.add("GET", "/cont1", [Resp(200, {"status": "IN_PROGRESS"}), Resp(200, {"status": "FINISHED"})])
    adapter("threads", h).publish(req(media=[VID]))
    h = threads_http(); h.add("GET", "/cont1", Resp(200, {"status": "ERROR", "error_message": "bad codec"}))
    with pytest.raises(PublishError, match="bad codec"):
        adapter("threads", h).publish(req(media=[VID]))

def test_threads_permalink_failure_does_not_undo_a_successful_post():
    h = threads_http(); h.routes = [r for r in h.routes if r[1] != "/post1"]; h.add("GET", "/post1", Resp(500, {}))
    assert adapter("threads", h).publish(req()).provider_id == "post1"

def test_threads_publish_timeout_uncertain_but_container_timeout_transient():
    with pytest.raises(PublishError) as e:
        adapter("threads", threads_http(requests.exceptions.ReadTimeout("x"))).publish(req())
    assert e.value.kind == "uncertain"
    h = threads_http(); h.routes = [r for r in h.routes if r[1] != "/77/threads"]; h.add("POST", "/77/threads", requests.exceptions.ReadTimeout("x"))
    with pytest.raises(PublishError) as e:
        adapter("threads", h).publish(req())
    assert e.value.kind == "transient"           # no public post exists yet, so retrying is safe

def test_threads_expired_token_permanent():
    h = FakeHttp().add("GET", "/me", Resp(400, {"error": {"code": 190}}))
    with pytest.raises(PublishError) as e:
        adapter("threads", h).publish(req())
    assert e.value.kind == "permanent"


# ---------- Facebook ----------
def fb(h, **kw):
    return adapter("facebook_page", h, page_id="12345", **kw)

def test_facebook_endpoints_by_media_type():
    h = FakeHttp().add("POST", "/12345/feed", Resp(200, {"id": "12345_1"}))
    r = fb(h).publish(req()); assert r.provider_id == "12345_1" and h.calls[0][2]["data"]["message"] == "Hello"
    h = FakeHttp().add("POST", "/12345/photos", Resp(200, {"id": "9", "post_id": "12345_9"}))
    assert fb(h).publish(req(media=[IMG])).provider_id == "12345_9" and h.calls[0][2]["data"]["url"] == IMG["url"]
    h = FakeHttp().add("POST", "/12345/videos", Resp(200, {"id": "v1"}))
    fb(h).publish(req(media=[VID])); assert h.calls[0][2]["data"]["file_url"] == VID["url"]

def test_facebook_page_id_must_be_numeric_and_permission_error_is_permanent():
    with pytest.raises(PublishError):
        adapter("facebook_page", FakeHttp(), page_id="abc").publish(req())
    h = FakeHttp().add("POST", "/feed", Resp(403, {"error": {"message": "(#200) permissions error"}}))
    with pytest.raises(PublishError) as e:
        fb(h).publish(req())
    assert e.value.kind == "permanent" and "200" in str(e.value)


# ---------- Instagram ----------
def ig_http():
    h = FakeHttp()
    h.add("POST", "/555/media_publish", Resp(200, {"id": "media1"}))
    h.add("POST", "/555/media", Resp(200, {"id": "cont1"}))
    h.add("GET", "/media1", Resp(200, {"permalink": "https://instagram.com/p/abc"}))
    return h

def test_instagram_image_flow():
    h = ig_http(); r = adapter("instagram", h, ig_user_id="555").publish(req(media=[IMG]))
    assert h.to("/555/media")[0][2]["data"]["image_url"] == IMG["url"] and h.to("/555/media")[0][2]["data"]["caption"] == "Hello"
    assert r.provider_id == "media1" and r.url.endswith("/abc")

def test_instagram_rejects_png_and_missing_media_before_any_call():
    h = ig_http()
    with pytest.raises(PublishError, match="JPEG"):
        adapter("instagram", h, ig_user_id="555").publish(req(media=[{"type": "image", "url": "https://c.example/a.png"}]))
    with pytest.raises(PublishError, match="needs an image"):
        adapter("instagram", h, ig_user_id="555").publish(req())
    assert h.calls == []

def test_instagram_reel_polls_status():
    h = ig_http(); h.add("GET", "/cont1", [Resp(200, {"status_code": "IN_PROGRESS"}), Resp(200, {"status_code": "FINISHED"})])
    adapter("instagram", h, ig_user_id="555").publish(req(media=[VID]))
    assert h.to("/555/media")[0][2]["data"]["media_type"] == "REELS" and len(h.to("/cont1")) == 2

def test_instagram_reel_error_status_permanent():
    h = ig_http(); h.add("GET", "/cont1", Resp(200, {"status_code": "ERROR"}))
    with pytest.raises(PublishError) as e:
        adapter("instagram", h, ig_user_id="555").publish(req(media=[VID]))
    assert e.value.kind == "permanent"

def test_instagram_publish_5xx_uncertain():
    h = ig_http(); h.routes[0] = ("POST", "/555/media_publish", Resp(500, {}))
    with pytest.raises(PublishError) as e:
        adapter("instagram", h, ig_user_id="555").publish(req(media=[IMG]))
    assert e.value.kind == "uncertain"


# ---------- shared ----------
def test_missing_secret_names_the_secret_not_its_value():
    with pytest.raises(PublishError) as e:
        adapter("threads", FakeHttp(), secrets={}).publish(req())
    assert "TOK" in str(e.value) and e.value.kind == "permanent"

def test_unbuilt_platform_adapter_refused():
    with pytest.raises(PublishError, match="not built"):
        build_adapter({"id": "a", "platform": "x"}, {}, FakeHttp())

def test_verify_is_read_only():
    h = FakeHttp().add("GET", "/me", Resp(200, {"id": "1", "username": "me"}))
    assert adapter("threads", h).verify()["identity"] == "me" and all(c[0] == "GET" for c in h.calls)
