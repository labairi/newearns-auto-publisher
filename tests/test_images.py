import io
import json
from pathlib import Path

import pytest
import requests
from PIL import Image

from publisher import images, ledger, quality, run
from tests.test_pipeline import CFG, EXISTING, env, make_article, ok_links, write  # noqa: F401 (env fixture)

SAVED = Path(__file__).resolve().parent.parent / "data/runs/20261021T113230521140Z/article.json"

LIMITS = ("<h2>Check which attachment limit applies to you</h2><p>Limits differ.</p>"
          "<table><thead><tr><th>Provider</th><th>Limit</th><th>Source</th></tr></thead><tbody>"
          "<tr><td>Gmail</td><td>25 MB total</td><td><a href='https://a'>Gmail Help</a></td></tr>"
          "<tr><td>Outlook desktop</td><td>20 MB default</td><td>x</td></tr>"
          "<tr><td>Exchange Server</td><td>10 MB default</td><td>x</td></tr>"
          "<tr><td>Apple Mail Drop</td><td>5 GB</td><td>x</td></tr></tbody></table>"
          "<h2>Pick a method</h2><table><thead><tr><th>You have</th><th>Method</th></tr></thead><tbody>"
          "<tr><td>A Mac</td><td>Preview</td></tr><tr><td>Acrobat</td><td>Compress</td></tr>"
          "<tr><td>Word</td><td>Export</td></tr></tbody></table>"
          "<p>Try the <a href=\"https://private-pdf-editor.com/compress?utm_source=newearns\">Private PDF Editor "
          "compressor</a>.</p><p><em>Disclosure: Private PDF Editor is built by the team behind New Earns.</em></p>"
          "<h2>FAQ</h2><p>Done.</p>")


def png(w=1280, h=800):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (240, 240, 250)).save(buf, "PNG")
    return buf.getvalue()


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


def test_chart_only_from_a_table_of_quantities_in_one_unit():
    tabs = images.tables(LIMITS)
    data = images.chart_data(tabs[0])
    assert data["title"] == "Check which attachment limit applies to you" and data["unit"] == "MB"
    assert [(b["label"], b["value"], b["text"]) for b in data["bars"]] == [
        ("Gmail", 25, "25 MB"), ("Outlook desktop", 20, "20 MB"), ("Exchange Server", 10, "10 MB"),
        ("Apple Mail Drop", 5120, "5 GB")]
    assert images.chart_data(tabs[1]) is None  # no quantities: no chart
    mixed = dict(tabs[0], rows=[["a", "5 MB"], ["b", "10%"], ["c", "3 days"]])
    assert images.chart_data(mixed) is None
    assert images.chart_data(dict(tabs[0], rows=tabs[0]["rows"][:2])) is None  # too few rows


def test_chart_renders_as_a_compact_webp_with_outlier_break():
    data = images.chart_data(images.tables(LIMITS)[0])
    img, height = images.bar_chart(data, "pdf")
    pic = Image.open(io.BytesIO(img))
    assert pic.format == "WEBP" and pic.size == (1200, height) and len(img) < 80_000


def test_screenshot_only_of_our_own_product_page_placed_after_its_paragraph(data):
    shots = []

    def shoot(url):
        shots.append(url)
        return png(), "Compress PDF - Private PDF Editor"
    art = dict(make_article(), content_html=LIMITS + '<p><a href="https://adobe.com/acrobat">Acrobat</a></p>')
    inline = images.inline_visuals(CFG, art, "pdf", data, 2, shoot=shoot)
    assert shots == ["https://private-pdf-editor.com/compress"]  # UTM dropped, Adobe never captured
    shot, chart = inline
    assert shot["kind"] == "screenshot" and shot["license"] == "own-screenshot"
    assert shot["alt_text"] == ('Screenshot of the Private PDF Editor page "Compress PDF - Private PDF Editor" '
                                "at private-pdf-editor.com/compress")
    assert "screenshot taken" in shot["caption"] and shot["filename"].endswith("-private-pdf-editor-screenshot.webp")
    assert chart["kind"] == "chart" and chart["alt_text"].startswith("Bar chart of limit by provider: Gmail 25 MB")
    up = [{"id": 21, "url": "https://newearns.com/u/s.webp"}, {"id": 22, "url": "https://newearns.com/u/c.webp"}]
    out = images.insert(LIMITS, {"featured": {"provider": "in-house"}, "inline": inline}, up)
    assert out.index("wp-image-22") > out.index("</table>") and out.index("wp-image-22") < out.index("<h2>Pick")
    assert out.index("wp-image-21") > out.index("Disclosure:") and out.index("wp-image-21") < out.index("<h2>FAQ")


def test_no_inline_visual_when_nothing_would_add_value(data):
    art = dict(make_article(), content_html="<h2>Steps</h2><ol><li>Open</li><li>Save</li><li>Send</li></ol>")
    assert images.inline_visuals(CFG, art, "pdf", data, 2) == []


def test_failed_screenshot_is_skipped_not_faked(data):
    def broken(url):
        raise RuntimeError("timeout")
    said = []
    inline = images.inline_visuals(CFG, dict(make_article(), content_html=LIMITS), "pdf", data, 2, say=said.append,
                                   shoot=broken)
    assert [i["kind"] for i in inline] == ["chart"] and "no screenshot" in said[0]


def test_saved_article_reports_fallback_and_stays_under_the_ceiling(data):
    art = json.loads(SAVED.read_text())
    plan = images.build(CFG, art, "instagram", data, search=stock(data, fake_get([], openverse={"results": []},
                                                                                commons={"query": {}})))
    assert plan["featured"]["fallback"] and any(n.startswith("FALLBACK") for n in plan["notes"])
    assert plan["inline"] == []  # no table of quantities, and no browser in tests
    f = plan["featured"]
    assert 10 <= len(f["alt_text"]) <= 125 and f["filename"].startswith(art["slug"])
    img = Image.open(data / f["file"])
    assert img.format == "WEBP" and img.width == 1200


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


def test_every_source_down_falls_back_to_the_topic_illustration(data):
    get = fake_get([], openverse=requests.ConnectionError, commons=requests.Timeout)
    plan = images.build(CFG, make_article(), "pdf", data, search=stock(data, get), get=get)
    f = plan["featured"]
    assert f["provider"] == "in-house" and f["fallback"] and f["kind"] == "illustration" and f["motif"] == "compress"
    assert f["alt_text"].startswith("Illustration of a laptop screen where a large PDF file shrinks")
    assert any("FALLBACK in-house illustration (compress)" in r for r in images.report(plan))
    img = Image.open(data / "featured.webp")
    assert img.size == (1200, 630) and f["bytes"] < 40_000


def test_title_card_only_if_the_illustration_cannot_be_drawn(data, monkeypatch):
    def broken(*a, **k):
        raise OSError("no fonts")
    monkeypatch.setattr(images, "topic_illustration", broken)
    get = fake_get([], openverse=requests.ConnectionError, commons=requests.Timeout)
    plan = images.build(CFG, make_article(), "pdf", data, search=stock(data, get), get=get)
    assert plan["featured"]["kind"] == "title-card"
    assert any("FALLBACK title card" in r for r in images.report(plan))


def test_no_relevant_photo_falls_back_to_the_illustration(data):
    get = fake_get([], openverse={"results": [ov("z", "Sunset over the mountains")]}, commons={"query": {}})
    plan = images.build(CFG, make_article(), "pdf", data, search=stock(data, get), get=get)
    assert plan["featured"]["provider"] == "in-house" and plan["featured"]["kind"] == "illustration"


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
def test_images_checked_in_the_saved_post():
    up = [{"id": 11, "url": "https://newearns.com/wp-content/uploads/a.webp"}]
    inline = [{"anchor": {"table_index": 0}, "alt_text": "Bar chart", "caption": "c", "width": 1200, "height": 500}]
    out = images.insert(LIMITS, {"featured": {"provider": "in-house"}, "inline": inline}, up)
    post = {"featured_media": 10, "content": {"raw": out}}
    feat = {"id": 10, "url": "https://newearns.com/wp-content/uploads/f.webp"}
    ok = lambda *a, **k: Resp(ctype="image/webp")
    assert images.check_in_post(post, feat, up, get=ok) == []
    assert images.check_in_post({"featured_media": 0, "content": {"raw": LIMITS}}, feat, up, get=ok)
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
        "<h2>Section 3: step three</h2>", "<h2>Section 3: step three</h2><table><tr><th>Provider</th><th>Limit</th></tr>"
        "<tr><td>Gmail</td><td>25 MB</td></tr><tr><td>Outlook</td><td>20 MB</td></tr>"
        "<tr><td>Exchange</td><td>10 MB</td></tr></table>")
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


def test_image_test_makes_a_draft_and_leaves_logs_and_schedule_alone(env, monkeypatch):
    wp, tmp, _ = env
    monkeypatch.setattr(images, "screenshot", lambda url: (png(), "Instagramaps"))
    f = write(tmp, json.loads(SAVED.read_text()))
    assert run.main(["image-test", "--article-file", f]) == 0
    (post,) = wp.posts.values()
    assert post["status"] == "draft" and post["slug"].startswith("image-test-") and ("status", "publish") not in wp.calls
    assert post["content"].count("wp-image-") == len(wp.media) == 2  # featured first, then the Instagramaps screenshot
    assert post["content"].startswith('<figure class="wp-block-image size-large newearns-featured-image">')
    assert ledger.read_log() == [] and not (tmp / "schedule_log.jsonl").exists() and not (tmp / "runs").exists()
    assert not (tmp / "image_cache" / "used.json").exists()


@pytest.mark.parametrize("desc,text,year", [
    ("Reagan and Gorbachev signing", "President Reagan and General Secretary Gorbachev signing the treaty documents", 1987),
    ("Laptop Heat Pipe", "Laptop heat pipe", 0),                     # one object, no scene
    ("Document signing ceremony", "Ministers sign documents at a ceremony", 2012),
    ("Woman working at home with laptop and documents", "", 1999),    # too old to look current
])
def test_off_topic_historical_or_single_object_photos_rejected(desc, text, year):
    kw = CFG["images"]["photo_keywords"]["pdf"]
    c = {"description": desc, "text": text, "tags": [], "width": 4000, "height": 3000, "image_url": "https://x",
         "year": year}
    assert images.score(c, images.terms("compress a PDF for email"), kw) == 0


def test_scene_with_people_and_the_article_subject_wins():
    kw = CFG["images"]["photo_keywords"]["pdf"]
    base = {"text": "", "tags": [], "width": 4000, "height": 3000, "image_url": "https://x", "year": 2021}
    good = images.score(dict(base, description="Woman compressing a PDF file on her laptop"),
                        images.terms("compress a PDF for email"), kw)
    beach = images.score(dict(base, description="Golden Gardens laptop user",
                              text="Man sitting on a beach, using a laptop computer"),
                         images.terms("compress a PDF for email"), kw)
    assert good > beach


# ---------------------------------------------------------------- topic relevance
COMPRESS = images.topic_for(CFG, "pdf", "How to Compress a PDF for Email")
PDF_DEVICES = CFG["images"]["photo_must_show"]["pdf"]


def photo(desc, text=""):
    return {"description": desc, "text": text, "tags": [], "width": 4000, "height": 3000, "image_url": "https://x",
            "year": 2020}


@pytest.mark.parametrize("desc,text", [
    ("Two men signing a book", "Ministers sign an agreement at a table"),          # what the owner saw
    ("Reagan and Gorbachev signing", "signing the INF treaty documents"),
    ("Business meeting with laptop", "Colleagues in a meeting around a laptop"),
    ("Handshake over a laptop", "Two businessmen shaking hands"),
    ("Woman working at home with laptop and documents on a table", ""),           # paperwork, draft 3717
    ("Laptop next to a stack of books", ""),
    ("Contract and laptop on a desk", "Signing a contract"),
])
def test_wrong_scenes_are_rejected_for_a_compress_pdf_article(desc, text):
    ok, why = images.relevance(photo(desc, text), COMPRESS, images.terms("compress a PDF for email"), PDF_DEVICES)
    assert not ok and why.startswith("wrong scene")


def test_generic_laptop_photo_is_not_enough_for_a_specific_topic():
    ok, why = images.relevance(photo("Woman using a laptop at home"), COMPRESS, images.terms("compress pdf"),
                               PDF_DEVICES)
    assert not ok and why.startswith("nothing about")


@pytest.mark.parametrize("desc", ["PDF document open on a laptop screen", "Laptop showing a file compression tool",
                                  "Woman attaching a PDF to an email on her laptop"])
def test_photo_showing_the_task_on_a_device_is_accepted(desc):
    ok, why = images.relevance(photo(desc), COMPRESS, images.terms("compress a PDF for email"), PDF_DEVICES)
    assert ok, why


def test_photo_must_show_the_device_in_its_title():
    ok, why = images.relevance(photo("Zip archive", "pdf compression concept"), COMPRESS, set(), PDF_DEVICES)
    assert not ok and why.startswith("no laptop")


def test_signing_is_fine_when_the_article_is_about_signing():
    sign = images.topic_for(CFG, "pdf", "How to Sign a PDF on Your Phone")
    assert sign["motif"] == "sign"
    ok, _ = images.relevance(photo("Person signing a document on a tablet with a stylus"), sign,
                             images.terms("sign a pdf"), PDF_DEVICES)
    assert ok
    ok, why = images.relevance(photo("Two men shaking hands on a laptop deal"), sign, set(), PDF_DEVICES)
    assert not ok


@pytest.mark.parametrize("pillar,title,motif", [
    ("pdf", "How to Compress a PDF for Email", "compress"),
    ("pdf", "How to Password-Protect a PDF", "lock"),
    ("pdf", "How to Unlock a PDF", "lock"),
    ("pdf", "How to Merge PDF Files", "merge"),
    ("pdf", "Convert a PDF to Word", "convert"),
    ("pdf", "Best Free PDF Readers", "pdf"),
    ("instagram", "Where Your Instagram Followers Live", "location"),
    ("instagram", "Best Time to Post on Instagram", "phone"),
    ("money", "Freelance Writing Jobs for Beginners", "earnings"),
    ("money", "Edit Videos for Money", "earnings"),  # topic rules for one pillar don't leak into another
])
def test_each_article_gets_its_own_topic(pillar, title, motif):
    assert images.topic_for(CFG, pillar, title)["motif"] == motif


def test_money_article_without_a_rule_needs_its_own_words_not_broad_ones():
    topic = images.topic_for(CFG, "money", "Freelance Writing Jobs for Beginners")
    t = images.terms("Freelance Writing Jobs for Beginners")
    devices = CFG["images"]["photo_must_show"]["money"]
    assert images.relevance(photo("Freelance writer typing on a laptop"), topic, t, devices)[0]
    assert not images.relevance(photo("Business people working on a laptop"), topic, t, devices)[0]


def test_rejected_photos_are_reported_and_a_relevant_one_is_used(data):
    results = {"results": [ov("r1", "Two men signing an agreement next to a laptop"),
                           ov("r2", "Woman compressing a PDF file on her laptop")]}
    get = fake_get([], openverse=results, commons={"query": {}})
    plan = images.build(CFG, make_article(), "pdf", data, search=stock(data, get), get=get)
    assert plan["featured"]["id"] == "r2" and plan["featured"]["relevance"] == "shows pdf"
    assert {"description": "Two men signing an agreement next to a laptop", "reason": "wrong scene (signing)"} \
        in plan["photo_rejections"]
    assert any("rejected photo" in r for r in images.report(plan))


@pytest.mark.parametrize("motif", images.MOTIFS)
def test_every_illustration_renders_small_and_featured_sized(motif):
    data = images.topic_illustration(motif, "pdf")
    assert Image.open(io.BytesIO(data)).size == (1200, 630) and len(data) < 40_000


def test_image_test_downloads_the_featured_image_wordpress_serves(env, monkeypatch):
    wp, tmp, _ = env
    f = write(tmp, json.loads(SAVED.read_text()))
    assert run.main(["image-test", "--article-file", f]) == 0
    (state,) = (tmp / "image_tests").glob("*/state.json")
    served = json.loads(state.read_text())["stages"]["wp_featured_served"]
    assert served["same_as_chosen"] and (state.parent / "wp-featured-served.webp").exists()


def test_served_featured_detects_a_different_file():
    class WP:
        def get_media(self, mid):
            return {"id": mid, "source_url": "https://newearns.com/x.webp", "media_details": {"width": 1, "height": 1}}

        def fetch_file(self, url):
            return b"other"
    served, _ = images.served_featured(WP(), {"featured_media": 7}, b"chosen")
    assert not served["same_as_chosen"]


# ---------------------------------------------------------------- featured image placement
FEAT = {"id": 901, "url": "https://newearns.com/wp-content/uploads/2026/10/compress-pdf-for-email-featured.webp"}


def test_featured_image_opens_the_body_above_the_intro_once():
    plan = {"featured": {"provider": "in-house", "alt_text": "Illustration of a PDF shrinking"}, "inline": []}
    body = "<p>Intro paragraph.</p><h2>Steps</h2><p>More.</p>"
    out = images.insert(body, plan, [], FEAT)
    fig, rest = out.split("</figure>", 1)
    assert rest == body  # nothing else moved; the intro follows the image directly
    assert 'alt="Illustration of a PDF shrinking"' in fig and 'class="wp-image-901"' in fig
    assert 'width="1200" height="630"' in fig and 'fetchpriority="high"' in fig and "lazy" not in fig
    assert images.insert(out, plan, [], FEAT).count("wp-image-901") == 1  # never added twice
    assert images.insert(body, plan, []) == body  # off unless asked for
    post = {"featured_media": 901, "content": {"raw": out}}
    assert images.check_in_post(post, FEAT, [], in_content=True) == []
    assert images.check_in_post({"featured_media": 901, "content": {"raw": body}}, FEAT, [], in_content=True) \
        == ["featured image 901 is in the post content 0 times, expected once"]
    twice = {"featured_media": 901, "content": {"raw": out.replace("<h2>", images.featured_figure(FEAT, plan) + "<h2>")}}
    assert "2 times" in images.check_in_post(twice, FEAT, [], in_content=True)[0]
    late = {"featured_media": 901, "content": {"raw": body + images.featured_figure(FEAT, plan)}}
    assert images.check_in_post(late, FEAT, [], in_content=True) == ["featured image 901 is not above the introduction"]


def test_photo_credit_stays_with_a_featured_photo_in_the_body():
    plan = {"featured": {"provider": "commons", "alt_text": "Laptop showing a PDF", "page_url": "https://commons.wikimedia.org/x",
                         "description": "Laptop", "creator": "A. Person", "license": "cc-by-sa", "license_version": "4.0",
                         "license_url": "https://creativecommons.org/licenses/by-sa/4.0/"}, "inline": []}
    out = images.insert("<p>Intro.</p>", plan, [], FEAT)
    assert out.index("wp-image-901") < out.index("<p>Intro.</p>") < out.index('class="image-credits"')
    assert "CC BY-SA 4.0" in out and "A. Person" in out


def test_featured_image_counted_on_the_rendered_page():
    def page(html):
        class R:
            text = html
            def raise_for_status(self):
                pass
        return lambda url, **kw: R()
    url = FEAT["url"]
    ours = '<img src="https://newearns.com/wp-content/uploads/2026/10/compress-pdf-for-email-featured.webp" ' \
           'srcset="https://newearns.com/wp-content/uploads/2026/10/compress-pdf-for-email-featured-300x158.webp 300w" class="wp-image-901">'
    theme = '<img data-src="https://newearns.com/wp-content/uploads/2026/10/compress-pdf-for-email-featured-1024x538.webp" ' \
            'class="attachment-full wp-post-image">'
    lazy = '<noscript>' + ours + '</noscript>'
    other = '<img src="https://newearns.com/wp-content/uploads/2025/07/cropped-logo-new-earn.png">'
    og = '<meta property="og:image" content="https://newearns.com/wp-content/uploads/2026/10/compress-pdf-for-email-featured.webp">'
    assert images.featured_on_page("p", url, get=page(og + other + ours + lazy)) == 1
    assert images.featured_on_page("p", url, get=page(theme + ours)) == 2
    assert images.featured_on_page("p", url, get=page(og + other)) == 0


def test_live_run_reports_the_featured_image_count_without_unpublishing(env, monkeypatch):
    wp, tmp, _ = env
    monkeypatch.setattr(images.featured_on_page, "__defaults__", (lambda url, **kw: type("R", (), {
        "text": '<img src="https://newearns.com/u/compress-pdf-for-email-featured.webp">' * 2,
        "raise_for_status": lambda self: None})(),))
    assert run.main(["article", "--article-file", write(tmp, make_article())]) == 0
    (post,) = wp.posts.values()
    assert post["status"] == "publish" and post["content"].startswith('<figure class="wp-block-image size-large newearns-featured-image">')
    state = json.loads(next((tmp / "runs").glob("*/state.json")).read_text())["stages"]
    assert state["featured_on_page"] == 2 and state["final"] == "published"
