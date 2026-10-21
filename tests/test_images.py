import io
import json
from pathlib import Path

import pytest
import requests
from PIL import Image

from publisher import images, ledger, quality, run
from tests.test_pipeline import CFG, EXISTING, env, make_article, ok_links, write  # noqa: F401 (env fixture)

SAVED = Path(__file__).resolve().parent.parent / "data/runs/20261021T113230521140Z/article.json"

STEPS = ("<h2>Intro</h2><p>Hello.</p>"
         "<h2>How to do it in the app</h2><p>Open the app.</p>"
         "<ol><li><strong>Open your profile.</strong> Tap the icon.</li><li>Tap Insights. Then wait.</li>"
         "<li>Choose Total followers<ul><li>nested</li></ul></li></ol>"
         "<h2>Adjacent section</h2><ol><li>a one</li><li>b two</li><li>c three</li></ol>"
         "<h2>Things to check</h2><ul><li>One thing</li><li>Two things</li><li>Three things</li></ul>"
         "<h2>Later steps</h2><ol><li>x one</li><li>y two</li><li>z three</li></ol>"
         "<h2>FAQ</h2><ol><li>q</li><li>r</li><li>s</li></ol>")


def jpeg(w=1600, h=1000):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 120, 200)).save(buf, "JPEG")
    return buf.getvalue()


class Resp:
    def __init__(self, data=None, content=b"", ctype="application/json", status=200):
        self._data, self.content, self.status_code = data, content, status
        self.headers = {"Content-Type": ctype}

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def close(self):
        pass


def ov(id, title, license="by", **kw):
    base = {"id": id, "title": title, "license": license, "license_version": "2.0", "url": f"https://img.example/{id}.jpg",
            "width": 2000, "height": 1300, "creator": "Jane", "creator_url": "https://flickr.com/jane",
            "license_url": "https://creativecommons.org/licenses/by/2.0/", "foreign_landing_url": f"https://flickr.com/{id}",
            "source": "flickr", "tags": []}
    return {**base, **kw}


OPENVERSE = {"results": [
    ov("a1", "IMG_2034"),                                                   # camera file name, no description
    ov("a2", "Woman holding a smartphone showing Instagram", license="by-nc"),  # non-commercial: never used
    ov("a3", "Woman holding a smartphone showing Instagram", creator=""),   # CC BY without a creator to credit
    ov("a4", "Instagram logo on a smartphone"),                             # a logo, not a photo of a scene
    ov("a5", "Woman checking Instagram followers on her smartphone", tags=[{"name": "phone"}]),
    ov("a6", "Man holding smartphone", license="cc0", license_url="https://creativecommons.org/publicdomain/zero/1.0/"),
]}

COMMONS = {"query": {"pages": {
    "1": {"pageid": 1, "title": "File:Laptop and papers.jpg", "imageinfo": [{
        "mime": "image/jpeg", "width": 3000, "height": 2000, "thumburl": "https://upload.wikimedia.org/1600px-x.jpg",
        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Laptop_and_papers.jpg",
        "extmetadata": {"License": {"value": "cc-by-sa-4.0"}, "LicenseShortName": {"value": "CC BY-SA 4.0"},
                        "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"},
                        "Artist": {"value": '<a href="//commons.wikimedia.org/wiki/User:Ann">Ann</a>'},
                        "ImageDescription": {"value": "A laptop with PDF documents on an office desk"},
                        "ObjectName": {"value": "Laptop with PDF documents on a desk"}}}]},
    "2": {"pageid": 2, "title": "File:Person signing.jpg", "imageinfo": [{
        "mime": "image/jpeg", "width": 3000, "height": 2000, "thumburl": "https://upload.wikimedia.org/2.jpg",
        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Person_signing.jpg",
        "extmetadata": {"License": {"value": "cc-by-sa-4.0"}, "LicenseUrl": {"value": "https://creativecommons.org/x"},
                        "Artist": {"value": "Bob"}, "Restrictions": {"value": "personality"},
                        "ObjectName": {"value": "Person signing a PDF document on a laptop"}}}]},
    "3": {"pageid": 3, "title": "File:Office.jpg", "imageinfo": [{
        "mime": "image/jpeg", "width": 3000, "height": 2000, "thumburl": "https://upload.wikimedia.org/3.jpg",
        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Office.jpg",
        "extmetadata": {"License": {"value": "gfdl"}, "Artist": {"value": "Cy"},
                        "ObjectName": {"value": "Laptop and documents in an office"}}}]},
}}}


def fake_get(calls, openverse=None, commons=None):
    """Answers like the real services; openverse/commons may be an exception class or a status code."""
    def get(url, **kw):
        calls.append(url)
        for host, answer, payload in (("openverse", openverse, OPENVERSE), ("commons.wikimedia", commons, COMMONS)):
            if host in url:
                if isinstance(answer, type) and issubclass(answer, Exception):
                    raise answer("simulated")
                if isinstance(answer, int):
                    return Resp(status=answer)
                return Resp(answer if isinstance(answer, dict) else payload)
        return Resp(content=jpeg(), ctype="image/jpeg")
    return get


def stock(data, get):
    return images.StockSearch(data / "image_cache", get=get, sleep=lambda s: None)


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "DATA", tmp_path)
    monkeypatch.setattr(ledger, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(ledger, "LOG", tmp_path / "publication_log.jsonl")
    monkeypatch.setattr(ledger, "LOCK", tmp_path / ".run.lock")
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    return tmp_path


def insta():
    return dict(make_article(), title="How to See Where Your Instagram Followers Are From",
                focus_keyphrase="instagram follower location", slug="instagram-follower-location")


# ---------------------------------------------------------------- counts and diagrams
@pytest.mark.parametrize("words,expected", [(700, (1, 2)), (1200, (2, 3)), (2000, (3, 4)), (3000, (4, 5))])
def test_image_targets_follow_word_count_guideline(words, expected):
    assert images.target_count(words) == expected


def test_lists_are_found_with_their_section_and_short_labels():
    lists = images.top_level_lists(STEPS)
    assert [l["tag"] for l in lists] == ["ol", "ol", "ul", "ol", "ol"]  # nested list not counted
    assert lists[0]["heading"] == "How to do it in the app"
    assert lists[0]["items"] == ["Open your profile", "Tap Insights", "Choose Total followers"]


def test_diagrams_only_for_step_lists_never_bullets_neighbours_or_faq():
    lists = images.top_level_lists(STEPS)
    assert [l["heading"] for l in images.pick_lists(lists, 3)] == ["How to do it in the app", "Later steps"]
    assert [l["heading"] for l in images.pick_lists(lists, 1)] == ["How to do it in the app"]
    assert images.pick_lists(lists, 0) == []


def test_saved_article_gets_only_useful_diagrams_and_reports_fallback(data):
    art = json.loads(SAVED.read_text())
    plan = images.build(CFG, art, "instagram", data, search=stock(data, fake_get([], openverse={"results": []},
                                                                                commons={"query": {}})))
    assert plan["featured"]["fallback"] and any(n.startswith("FALLBACK") for n in plan["notes"])
    assert [i["kind"] for i in plan["inline"]] == ["steps"]  # the second step list is in the next section
    assert any("below the 3-4 guideline" in n for n in plan["notes"])
    for i in [plan["featured"], *plan["inline"]]:
        assert 10 <= len(i["alt_text"]) <= 125 and i["filename"].startswith(art["slug"])
        img = Image.open(data / i["file"])
        assert img.format == "WEBP" and img.width == 1200 and (data / i["file"]).stat().st_size < 150_000


# ---------------------------------------------------------------- licences and relevance
def test_only_a_clearly_licensed_relevant_photo_is_downloaded_and_credited(data):
    calls = []
    get = fake_get(calls)
    plan = images.build(CFG, insta(), "instagram", data, query="instagram follower location",
                        search=stock(data, get), get=get)
    f = plan["featured"]
    # a1 camera file name, a2 non-commercial, a3 no creator, a4 a logo: all skipped. a5 matches the
    # article's own words (instagram, followers) so it beats a6, which only matches scene words.
    assert f["id"] == "a5" and f["license"] == "cc-by" and f["provider"] == "openverse"
    assert f["alt_text"] == "Woman checking Instagram followers on her smartphone"
    downloads = [u for u in calls if "img.example" in u]
    assert downloads == ["https://img.example/a5.jpg"]  # nothing with an unclear licence was fetched
    assert Image.open(data / "featured.webp").size == (1200, 630)
    credit = images.credit_line(plan)
    assert "https://flickr.com/a5" in credit and "Jane" in credit and "CC BY 2.0" in credit and "Openverse" in credit


@pytest.mark.parametrize("c,ok", [
    ({"license": "cc-by", "creator": "A", "license_url": "https://cc/by", "page_url": "https://x"}, True),
    ({"license": "cc-by", "creator": "", "license_url": "https://cc/by", "page_url": "https://x"}, False),
    ({"license": "cc-by-sa", "creator": "A", "license_url": "", "page_url": "https://x"}, False),
    ({"license": "cc0", "license_url": "https://cc/zero", "page_url": "https://x"}, True),
    ({"license": "public-domain", "page_url": "https://x"}, True),
    ({"license": "cc-by-nc", "creator": "A", "license_url": "https://cc", "page_url": "https://x"}, False),
    ({"license": "cc0", "license_url": "https://cc/zero", "page_url": ""}, False),
])
def test_licence_must_be_clear_before_download(c, ok):
    assert images.licence_clear(c) is ok


def test_commons_licences_restrictions_and_credit(data):
    calls = []
    results = stock(data, fake_get(calls)).search("commons", "laptop documents")
    assert [r["id"] for r in results] == ["1"]  # personality-rights restriction and GFDL dropped
    r = results[0]
    assert r["license"] == "cc-by-sa" and r["license_version"] == "4.0" and r["creator"] == "Ann"
    assert r["creator_url"] == "https://commons.wikimedia.org/wiki/User:Ann"
    plan = {"featured": dict(r, query="laptop documents")}
    credit = images.credit_line(plan)
    assert "CC BY-SA 4.0" in credit and "Wikimedia Commons" in credit and "same licence" in credit


# ---------------------------------------------------------------- failures, retries, caching
def test_timeout_is_retried_once_then_next_query_then_next_source(data):
    calls = []
    get = fake_get(calls, openverse=requests.Timeout)
    search = stock(data, get)
    plan = images.build(CFG, dict(make_article(), focus_keyphrase="compress pdf"), "pdf", data, search=search, get=get)
    ov_calls = [u for u in calls if "openverse" in u]
    assert len(ov_calls) == 4  # two queries x (try + one retry), then Openverse counted as down
    assert plan["featured"]["provider"] == "commons" and plan["featured"]["id"] == "1"
    assert [s["result"] for s in plan["photo_searches"]][:2] == ["failed: Timeout", "failed: Timeout"]
    assert not list((data / "image_cache" / "search").glob("openverse-*"))  # failures are never cached


def test_one_failed_query_does_not_stop_the_next(data):
    calls, n = [], {"ov": 0}
    base = fake_get(calls)

    def get(url, **kw):
        if "openverse" in url:
            n["ov"] += 1
            if n["ov"] <= 2:
                return Resp(status=503)
        return base(url, **kw)
    plan = images.build(CFG, insta(), "instagram", data, query="instagram follower location",
                        search=stock(data, get), get=get)
    assert plan["featured"]["provider"] == "openverse" and plan["featured"]["id"] == "a5"
    assert plan["photo_searches"][0]["result"] == "failed: HTTP 503"


def test_client_error_is_not_retried(data):
    calls = []
    search = stock(data, fake_get(calls, openverse=401))
    with pytest.raises(images.SearchUnavailable):
        search.search("openverse", "laptop")
    assert len(calls) == 1


def test_every_source_down_falls_back_to_title_card(data):
    get = fake_get([], openverse=requests.ConnectionError, commons=requests.Timeout)
    plan = images.build(CFG, make_article(), "pdf", data, search=stock(data, get), get=get)
    assert plan["featured"]["provider"] == "in-house" and plan["featured"]["fallback"]
    assert any("FALLBACK title card" in r for r in images.report(plan))


def test_no_relevant_photo_falls_back_to_title_card(data):
    get = fake_get([], openverse={"results": [ov("z", "Sunset over the mountains")]}, commons={"query": {}})
    plan = images.build(CFG, make_article(), "pdf", data, search=stock(data, get), get=get)
    assert plan["featured"]["provider"] == "in-house"


def test_search_results_cached_and_a_used_photo_not_reused(data):
    calls = []
    get = fake_get(calls)
    search = stock(data, get)
    search.search("openverse", "instagram smartphone")
    search.search("openverse", "Instagram smartphone ")
    assert search.requests == 1 and search.log[-1]["result"].endswith("(cached)")
    plan = images.build(CFG, insta(), "instagram", data, search=search, get=get)
    assert plan["featured"]["id"] == "a5"
    images.mark_used(plan, "first-post")
    plan2 = images.build(CFG, insta(), "instagram", data, search=search, get=get)
    assert plan2["featured"].get("id") != "a5"
    # Openverse answered every query from the cache; only Commons, not searched before, was asked.
    assert all(s["result"].endswith("(cached)") for s in plan2["photo_searches"] if s["provider"] == "openverse")


def test_pexels_only_with_a_key(data):
    assert images.StockSearch(data).providers() == ["openverse", "commons"]
    assert images.StockSearch(data, pexels_key="k").providers() == ["pexels", "openverse", "commons"]


# ---------------------------------------------------------------- insertion, upload, checks
def test_diagrams_inserted_before_their_lists_and_checked_in_post():
    art = json.loads(SAVED.read_text())
    lists = images.top_level_lists(art["content_html"])
    plan = {"featured": {"provider": "in-house"},
            "inline": [{"list_index": l["index"], "alt_text": "Diagram of steps", "caption": "At a glance",
                        "width": 1200, "height": 500} for l in lists[1:3]]}
    up = [{"id": 11, "url": "https://newearns.com/wp-content/uploads/a.webp"},
          {"id": 12, "url": "https://newearns.com/wp-content/uploads/b.webp"}]
    out = images.insert(art["content_html"], plan, up)
    for m, lst in zip(up, lists[1:3]):
        at = out.index(f"wp-image-{m['id']}")
        assert out.index("<" + lst["tag"], at) - at < 400
    post = {"featured_media": 10, "content": {"raw": out}}
    feat = {"id": 10, "url": "https://newearns.com/wp-content/uploads/f.webp"}
    ok = lambda *a, **k: Resp(ctype="image/webp")
    assert images.check_in_post(post, feat, up, get=ok) == []
    assert images.check_in_post({"featured_media": 0, "content": {"raw": art["content_html"]}}, feat, up, get=ok)
    assert images.check_in_post(post, feat, up, get=lambda *a, **k: Resp(status=404, ctype="text/html"))


def test_unknown_licence_or_uncredited_cc_by_fails_image_rights():
    art = make_article()
    rep = lambda a: dict((n, ok) for n, ok, _ in quality.run_checks(a, CFG, EXISTING, set(), link_checker=ok_links,
                                                                     fact_check=a["fact_check"]).results)
    assert rep(art)["image_rights"]
    assert not rep(dict(art, featured_image={"alt_text": "A photo of a laptop", "license": "google-images"}))["image_rights"]
    assert not rep(dict(art, featured_image={"alt_text": "A photo of a laptop", "license": "cc-by-sa"}))["image_rights"]
    assert rep(dict(art, featured_image={"alt_text": "A photo of a laptop", "license": "cc-by-sa", "creator": "A",
                                         "license_url": "https://cc"}))["image_rights"]
    assert not rep(dict(art, inline_images=[{"alt_text": "short", "license": "generated-in-house"}]))["inline_images_have_alt"]


def test_published_post_carries_images_and_gates_still_judge_the_text(env):
    wp, tmp, _ = env
    art = make_article()
    art["content_html"] = art["content_html"].replace(
        "<h2>Section 3: step three</h2>", "<h2>Section 3: step three</h2><ol><li>Open it</li><li>Shrink it</li>"
        "<li>Send it</li></ol>")
    art["fact_check"]["content_sha256"] = quality.content_fingerprint(art["content_html"])
    assert run.main(["article", "--article-file", write(tmp, art)]) == 0
    post = next(iter(wp.posts.values()))
    assert post["status"] == "publish" and len(wp.media) == 2
    assert wp.media[0] == "compress-pdf-for-email-featured.webp"
    assert "wp-image-902" in post["content"] and post["featured_media"] == 901
    state = json.loads(next((tmp / "runs").glob("*/state.json")).read_text())["stages"]
    assert state["image_check"] == "ok" and state["final"] == "published"


def test_file_already_in_media_library_is_not_uploaded_again(env, monkeypatch):
    wp, tmp, _ = env
    monkeypatch.setattr(wp, "find_media", lambda name: {"id": 777, "source_url": f"https://newearns.com/u/{name}"}
                        if name.endswith("featured.webp") else None, raising=False)
    assert run.main(["article", "--article-file", write(tmp, make_article())]) == 0
    post = next(iter(wp.posts.values()))
    assert wp.media == [] and post["featured_media"] == 777


def test_image_problem_holds_post_for_review(env, monkeypatch):
    wp, tmp, _ = env
    monkeypatch.setattr(run.images, "check_in_post", lambda *a, **k: ["image 902 is not in the post content"])
    assert run.main(["article", "--article-file", write(tmp, make_article())]) == 0
    assert [p["status"] for p in wp.posts.values()] == ["pending"]


def test_image_test_makes_a_draft_and_leaves_logs_and_schedule_alone(env):
    wp, tmp, _ = env
    f = write(tmp, json.loads(SAVED.read_text()))
    assert run.main(["image-test", "--article-file", f]) == 0
    (post,) = wp.posts.values()
    assert post["status"] == "draft" and post["slug"].startswith("image-test-") and ("status", "publish") not in wp.calls
    assert post["content"].count("wp-image-") == len(wp.media) - 1 >= 1
    assert ledger.read_log() == [] and not (tmp / "schedule_log.jsonl").exists() and not (tmp / "runs").exists()
    assert not (tmp / "image_cache" / "used.json").exists()
