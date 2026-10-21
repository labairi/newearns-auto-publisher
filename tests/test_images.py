import io
import json
from pathlib import Path

import pytest
from PIL import Image

from publisher import images, ledger, quality, run
from tests.test_pipeline import CFG, EXISTING, make_article, ok_links

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
            raise RuntimeError(f"HTTP {self.status_code}")

    def close(self):
        pass


OPENVERSE = {"results": [
    {"id": "a1", "title": "IMG_2034", "license": "by", "url": "https://img.example/a1.jpg", "width": 2000, "height": 1300,
     "creator": "x", "license_url": "https://creativecommons.org/licenses/by/2.0/", "foreign_landing_url": "https://flickr.com/a1"},
    {"id": "a2", "title": "Woman holding a smartphone showing Instagram", "license": "by-sa",
     "url": "https://img.example/a2.jpg", "width": 2000, "height": 1300},
    {"id": "a3", "title": "Woman holding a smartphone showing Instagram", "license": "by", "license_version": "2.0",
     "url": "https://img.example/a3.jpg", "width": 2000, "height": 1300, "creator": "Jane",
     "creator_url": "https://flickr.com/jane", "license_url": "https://creativecommons.org/licenses/by/2.0/",
     "foreign_landing_url": "https://flickr.com/a3", "source": "flickr", "tags": [{"name": "phone"}]},
]}


def fake_get(calls):
    def get(url, **kw):
        calls.append(url)
        if "openverse" in url:
            return Resp(OPENVERSE)
        return Resp(content=jpeg(), ctype="image/jpeg")
    return get


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "DATA", tmp_path)
    monkeypatch.setattr(ledger, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(ledger, "LOG", tmp_path / "publication_log.jsonl")
    monkeypatch.setattr(ledger, "LOCK", tmp_path / ".run.lock")
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    return tmp_path


@pytest.mark.parametrize("words,expected", [(700, (1, 2)), (1200, (2, 3)), (2000, (3, 4)), (3000, (4, 5))])
def test_image_targets_follow_word_count_guideline(words, expected):
    assert images.target_count(words) == expected


def test_lists_are_found_with_their_section_and_short_labels():
    lists = images.top_level_lists(STEPS)
    assert [l["tag"] for l in lists] == ["ol", "ol", "ul", "ol", "ol"]  # nested list not counted
    assert lists[0]["heading"] == "How to do it in the app"
    assert lists[0]["items"] == ["Open your profile", "Tap Insights", "Choose Total followers"]


def test_steps_first_no_neighbours_no_faq_bullets_only_to_reach_minimum():
    lists = images.top_level_lists(STEPS)
    chosen = images.pick_lists(lists, want_max=3, want_min=1)
    assert [l["heading"] for l in chosen] == ["How to do it in the app", "Later steps"]
    chosen = images.pick_lists(lists, want_max=3, want_min=3)
    assert [l["heading"] for l in chosen] == ["How to do it in the app", "Later steps"]  # the bulleted list neighbours both
    assert images.pick_lists(lists, want_max=0, want_min=0) == []


def test_saved_article_gets_featured_plus_diagrams_within_guideline(data):
    art = json.loads(SAVED.read_text())
    plan = images.build(CFG, art, "instagram", data)
    lo, hi = plan["target"]
    assert (lo, hi) == (3, 4) and lo <= 1 + len(plan["inline"]) <= hi
    assert plan["featured"]["license"] == "generated-in-house"  # no stock results in tests
    for i in [plan["featured"], *plan["inline"]]:
        assert 10 <= len(i["alt_text"]) <= 125 and i["filename"].endswith(".webp")
        assert i["filename"].startswith(art["slug"])
        img = Image.open(data / i["file"])
        assert img.format == "WEBP" and img.width == 1200
        assert (data / i["file"]).stat().st_size < 150_000


def test_only_relevant_properly_licensed_photo_is_used_and_credited(data):
    calls = []
    search = images.StockSearch(data / "image_cache", get=fake_get(calls))
    art = make_article()
    plan = images.build(CFG, art, "instagram", data, query="instagram follower location", search=search,
                        get=fake_get(calls))
    f = plan["featured"]
    # a1 has a camera filename for a title, a2 is CC BY-SA (not requested): both skipped.
    assert f["id"] == "a3" and f["license"] == "cc-by" and f["alt_text"].startswith("Woman holding a smartphone")
    assert Image.open(data / "featured.webp").size == (1200, 630)
    credit = images.credit_line(plan)
    assert "https://flickr.com/a3" in credit and "Jane" in credit and "CC BY 2.0" in credit
    assert quality.IMAGE_LICENSES and f["license"] in quality.IMAGE_LICENSES


def test_search_results_are_cached_and_used_photos_not_reused(data):
    calls = []
    get = fake_get(calls)
    search = images.StockSearch(data / "image_cache", get=get)
    search.search("openverse", "instagram on smartphone")
    search.search("openverse", "Instagram on smartphone ")
    assert search.requests == 1
    plan = images.build(CFG, make_article(), "instagram", data, search=search, get=get)
    images.mark_used(plan, "first-post")
    plan2 = images.build(CFG, make_article(), "instagram", data, search=search, get=get)
    assert plan2["featured"]["provider"] == "in-house"
    # Queries already searched come from the cache: only the one new query costs a request.
    assert plan2["stock_api_calls"] == 1 and calls.count("https://api.openverse.org/v1/images/") == 3


def test_search_failure_falls_back_to_title_card(data):
    def broken(url, **kw):
        raise RuntimeError("429 Too Many Requests")
    search = images.StockSearch(data / "image_cache", get=broken)
    plan = images.build(CFG, make_article(), "pdf", data, search=search)
    assert plan["featured"]["provider"] == "in-house"


def test_pexels_used_first_only_with_a_key(data):
    assert images.StockSearch(data).providers() == ["openverse"]
    assert images.StockSearch(data, pexels_key="k").providers() == ["pexels", "openverse"]


def test_diagrams_inserted_before_their_lists_and_checked_in_post():
    art = json.loads(SAVED.read_text())
    lists = images.top_level_lists(art["content_html"])
    plan = {"featured": {"provider": "in-house"},
            "inline": [{"list_index": l["index"], "alt_text": "Diagram of steps", "caption": "At a glance",
                        "width": 1200, "height": 500} for l in lists[1:3]]}
    up = [{"id": 11, "url": "https://newearns.com/wp-content/uploads/a.webp"},
          {"id": 12, "url": "https://newearns.com/wp-content/uploads/b.webp"}]
    out = images.insert(art["content_html"], plan, up)
    for (img, m), lst in zip(zip(plan["inline"], up), lists[1:3]):
        at = out.index(f"wp-image-{m['id']}")
        assert out.index(lst["tag"] == "ol" and "<ol" or "<ul", at) - at < 400
    assert images.insert(art["content_html"], {"featured": {"provider": "in-house"}, "inline": []}, []) == art["content_html"]
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
    assert not rep(dict(art, featured_image={"alt_text": "A photo of a laptop", "license": "cc-by"}))["image_rights"]
    assert not rep(dict(art, inline_images=[{"alt_text": "short", "license": "generated-in-house"}]))["inline_images_have_alt"]


def test_published_post_carries_images_and_gates_still_judge_the_text(env):
    from tests.test_pipeline import write
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


def test_image_problem_holds_post_for_review(env, monkeypatch):
    from tests.test_pipeline import write
    wp, tmp, _ = env
    monkeypatch.setattr(run.images, "check_in_post", lambda *a, **k: ["image 902 is not in the post content"])
    assert run.main(["article", "--article-file", write(tmp, make_article())]) == 0
    assert [p["status"] for p in wp.posts.values()] == ["pending"]


def test_image_test_makes_one_draft_and_leaves_logs_and_schedule_alone(env):
    from tests.test_pipeline import write
    wp, tmp, _ = env
    f = write(tmp, json.loads(SAVED.read_text()))
    assert run.main(["image-test", "--article-file", f]) == 0
    (post,) = wp.posts.values()
    assert post["status"] == "draft" and post["slug"].startswith("image-test-") and ("status", "publish") not in wp.calls
    assert post["content"].count("wp-image-") == len(wp.media) - 1 >= 2
    assert ledger.read_log() == [] and not (tmp / "schedule_log.jsonl").exists() and not (tmp / "runs").exists()
    assert not (tmp / "image_cache" / "used.json").exists()
    assert run.main(["image-test", "--article-file", f]) == 6  # never a second test draft


from tests.test_pipeline import env  # noqa: E402,F401  (fixture)
