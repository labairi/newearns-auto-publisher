import copy
import json

import pytest

from publisher import ledger, quality, run

CFG = run.load_cfg()
SITE = "https://newearns.com"

EXISTING = [{
    "id": 3683, "slug": "how-to-start-your-digital-marketing-agency-in-2025",
    "link": f"{SITE}/how-to-start-your-digital-marketing-agency-in-2025/",
    "title": "How to Start Your Digital Marketing Agency in 2025",
    "text": "Starting a digital marketing agency means choosing a niche, building a portfolio and finding clients.",
    "focus_keyphrase": "digital marketing agency",
}]

FILLER = [
    "Large scanned pages are usually the reason a file is too big, because each page is stored as a full image.",
    "Before you change anything, open the document properties and note the current size and page count.",
    "If the file contains photos, lowering image resolution to around screen quality often shrinks it the most.",
    "Fonts that are embedded in full can add weight, so subsetting them keeps only the characters you use.",
    "Removing unused objects such as hidden layers, thumbnails and old form data also trims the file.",
    "Always keep the original and compare both versions side by side at normal zoom before sending.",
    "Text should stay sharp after compression, so zoom into a paragraph and a table to confirm legibility.",
    "When a client needs to print the document, choose a gentler setting than you would for on-screen reading.",
    "Name the smaller file clearly so the recipient knows which version is final.",
    "If the attachment is still too large, splitting the document into two parts is a reasonable fallback.",
    "Many mail providers suggest sharing a link to cloud storage for very large documents instead.",
    "Check whether the recipient's organisation blocks certain file-sharing services before choosing that route.",
]


def make_article(**over):
    paras = []
    for i in range(8):
        body = " ".join(FILLER[(i + j) % len(FILLER)] for j in range(9))
        paras.append(f"<h2>Section {i + 1}: step {['one','two','three','four','five','six','seven','eight'][i]}</h2><p>{body}</p>")
    html = (
        "<p>This guide shows how to compress a PDF for email so it stays readable and fits under attachment limits.</p>"
        f"<p>Gmail lets you attach files up to 25 MB, according to "
        f"<a href=\"https://support.google.com/mail/answer/6584\">Google's Gmail help</a>.</p>"
        f"<p>Microsoft documents its own limits on <a href=\"https://support.microsoft.com/outlook-limits\">Outlook support</a> "
        f"and Adobe explains optimisation on <a href=\"https://helpx.adobe.com/acrobat/optimize\">Adobe Help</a>.</p>"
        + "".join(paras)
        + f"<p>For agencies sending proposals, see <a href=\"{SITE}/how-to-start-your-digital-marketing-agency-in-2025/\">how to start an agency</a>.</p>"
        "<p>You can use the <a href=\"https://private-pdf-editor.com/?utm_source=newearns&utm_medium=referral&utm_campaign=compress-pdf-for-email\">Private PDF Editor compressor</a> in your browser.</p>"
        "<p><em>Disclosure: Private PDF Editor is built by the team behind New Earns. We mention it where it fits the task; the steps in this guide also work with other tools.</em></p>"
        "<h2>FAQ</h2><h3>Will compression blur text?</h3><p>Usually not when text is real text rather than a scan.</p>"
    )
    a = {
        "pillar": "pdf",
        "title": "How to Compress a PDF for Email Without Losing Quality",
        "seo_title": "Compress a PDF for Email Without Losing Quality",
        "slug": "compress-pdf-for-email",
        "meta_description": "Step-by-step ways to compress a PDF for email: shrink scanned pages, images and fonts while keeping text sharp, plus what to do when it is still too big.",
        "focus_keyphrase": "compress a PDF for email",
        "excerpt": "Shrink a PDF for email without blurring it.",
        "content_html": html,
        "sources": [
            {"url": "https://support.google.com/mail/answer/6584", "title": "Gmail limits", "supports": "25 MB"},
            {"url": "https://support.microsoft.com/outlook-limits", "title": "Outlook", "supports": "limits"},
            {"url": "https://helpx.adobe.com/acrobat/optimize", "title": "Adobe", "supports": "optimisation"},
        ],
        "product_link_reason": "The compress step can be done with the browser tool.",
        "image_alt_text": "Title card reading Compress a PDF for Email",
        "image_headline": "Compress a PDF for Email",
        "search_intent": "how-to",
        "unique_value": "decision table for print vs screen",
        "featured_image": {"alt_text": "Title card reading Compress a PDF for Email", "license": "generated-in-house"},
        "fact_check": {"claims": [{"claim": "Gmail 25 MB", "verdict": "supported", "evidence": "x"}],
                       "satisfies_search_intent": True, "intent_notes": "", "adds_original_value": True,
                       "value_notes": "", "product_claims_accurate": True, "product_notes": ""},
    }
    a.update(over)
    return a


def ok_links(url):
    return True, "200"


def checks(article, **kw):
    return quality.run_checks(article, CFG, EXISTING, {p["slug"] for p in EXISTING},
                              link_checker=kw.get("links", ok_links), fact_check=article["fact_check"])


def test_good_article_passes():
    rep = checks(make_article())
    assert rep.passed, rep.failures


def test_keyword_stuffing_fails():
    a = make_article()
    a["content_html"] += "<p>" + " ".join(["compress a pdf for email"] * 30) + "</p>"
    assert any(f.startswith("keyword_density") for f in checks(a).failures)


def test_fabricated_experience_fails():
    a = make_article()
    a["content_html"] += "<p>In my experience this works every time.</p>"
    assert any("no_fabricated_experience" in f for f in checks(a).failures)


def test_unsourced_statistic_fails():
    a = make_article()
    a["content_html"] += "<p>About 73% of freelancers email PDFs daily.</p>"
    assert any("statistics_have_sources" in f for f in checks(a).failures)


def test_broken_link_fails():
    rep = checks(make_article(), links=lambda u: (False, "404") if "adobe" in u else (True, "200"))
    assert any("no_broken_links" in f for f in rep.failures)


def test_duplicate_intent_fails():
    a = make_article(title="How to Start Your Digital Marketing Agency in 2026")
    assert any("distinct_search_intent" in f for f in checks(a).failures)


def test_untracked_or_undisclosed_product_link_fails():
    a = make_article()
    a["content_html"] = a["content_html"].replace("?utm_source=newearns&utm_medium=referral&utm_campaign=compress-pdf-for-email", "")
    a["content_html"] = a["content_html"].replace("built by the team behind New Earns", "great")
    f = checks(a).failures
    assert any("product_links_tracked" in x for x in f) and any("ownership_disclosed" in x for x in f)


def test_fact_check_uncertain_holds():
    a = make_article()
    a["fact_check"] = copy.deepcopy(a["fact_check"])
    a["fact_check"]["claims"].append({"claim": "Outlook allows 50 MB", "verdict": "uncertain", "evidence": ""})
    assert any("fact_check_claims" in f for f in checks(a).failures)


def test_fake_internal_link_fails():
    a = make_article()
    a["content_html"] += f'<p><a href="{SITE}/made-up-post/">x</a></p>'
    assert any("internal_links_valid" in f for f in checks(a).failures)


# ------------------------------------------------------------ end-to-end with a fake WordPress
class FakeWP:
    def __init__(self):
        self.posts, self.media, self.calls = {}, [], []
        self.next_id = 5000

    def all_posts(self, **kw):
        return [{"id": p["id"], "slug": p["slug"], "link": p["link"], "title": {"rendered": p["title"]},
                 "content": {"rendered": p["text"]}, "aioseo_meta_data": {}} for p in EXISTING] + \
               [{"id": p["id"], "slug": p["slug"], "link": p["link"], "title": {"rendered": p["title"]},
                 "content": {"rendered": p["content"]}, "aioseo_meta_data": {}}
                for p in self.posts.values() if p["status"] == "publish"]

    def categories(self):
        return {1: "passive-income-ideas", 30: "makemoneyonline", 34: "ai-future-trends"}

    def find_by_slug(self, slug):
        for p in self.posts.values():
            if p["slug"] == slug:
                return {"id": p["id"], "slug": slug, "status": p["status"], "link": p["link"]}
        return None

    def upload_media(self, *a, **k):
        self.media.append(a[0])
        return {"id": 900 + len(self.media)}

    def create_post(self, **k):
        self.calls.append(("create", k["status"]))
        pid = self.next_id = self.next_id + 1
        self.posts[pid] = {"id": pid, "slug": k["slug"], "status": k["status"], "title": k["title"],
                           "content": k["content_html"], "link": f"{SITE}/{k['slug']}/", "cat": k["category_id"],
                           "aioseo": (k["seo_title"], k["meta_description"])}
        return dict(self.posts[pid])

    def set_status(self, pid, status):
        self.calls.append(("status", status))
        self.posts[pid]["status"] = status
        return dict(self.posts[pid])


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "DATA", tmp_path)
    monkeypatch.setattr(ledger, "LOG", tmp_path / "publication_log.jsonl")
    monkeypatch.setattr(ledger, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(ledger, "LOCK", tmp_path / ".run.lock")
    wp = FakeWP()
    monkeypatch.setattr(run.WordPress, "from_env", classmethod(lambda cls, url: wp))
    monkeypatch.setattr(run, "public_url_ok", lambda url, t: (True, "ok"))
    monkeypatch.setattr(run.time, "sleep", lambda s: None)
    monkeypatch.setattr(quality, "check_link", ok_links)
    monkeypatch.setattr(quality.run_checks, "__defaults__", (ok_links, None))
    cfg = copy.deepcopy(CFG)
    cfg["publishing"]["mode"] = "publish"
    monkeypatch.setenv("ALLOW_PUBLISH", "true")
    monkeypatch.setattr(run, "load_cfg", lambda: cfg)
    return wp, tmp_path, cfg


def write(tmp_path, art):
    p = tmp_path / "article.json"
    p.write_text(json.dumps(art))
    return str(p)


def test_end_to_end_publish_then_no_duplicate(env):
    wp, tmp, _ = env
    f = write(tmp, make_article())
    assert run.main(["article", "--article-file", f]) == 0
    assert [p["status"] for p in wp.posts.values()] == ["publish"]
    post = next(iter(wp.posts.values()))
    assert post["cat"] == 30  # PDF pillar falls back to Make Money Online until a new category exists
    log = ledger.read_log()
    assert log[-1]["status"] == "published" and log[-1]["url"].endswith("/compress-pdf-for-email/")
    assert log[-1]["product_links"] and log[-1]["sources"]
    # Second run with the same article: blocked by spacing rule, then by slug checks when forced.
    assert run.main(["article", "--article-file", f]) == 0
    assert run.main(["article", "--article-file", f, "--force"]) == 6
    assert len(wp.posts) == 1


def test_failed_article_goes_to_review_queue(env):
    wp, tmp, _ = env
    bad = make_article()
    bad["content_html"] += "<p>About 73% of freelancers email PDFs daily.</p>"
    assert run.main(["article", "--article-file", write(tmp, bad)]) == 0
    assert [p["status"] for p in wp.posts.values()] == ["pending"]
    assert ledger.read_log()[-1]["status"] == "held_for_review"


def test_dry_run_never_writes(env):
    wp, tmp, _ = env
    assert run.main(["article", "--article-file", write(tmp, make_article()), "--dry-run"]) == 0
    assert wp.posts == {} and ledger.read_log() == []


def test_verification_failure_unpublishes(env, monkeypatch):
    wp, tmp, _ = env
    monkeypatch.setattr(run, "public_url_ok", lambda url, t: (False, "HTTP 404"))
    assert run.main(["article", "--article-file", write(tmp, make_article())]) == 5
    assert [p["status"] for p in wp.posts.values()] == ["pending"]
    assert ledger.read_log()[-1]["status"] == "failed_verification"


def test_pillar_rotation():
    cfg = copy.deepcopy(CFG)
    assert run.choose_pillar(cfg) in ("pdf", "instagram")


# ------------------------------------------------------------ safety switches
def test_shipped_config_defaults_to_draft():
    assert CFG["publishing"]["mode"] == "draft"


def test_publish_mode_without_allow_publish_saves_draft(env, monkeypatch):
    wp, tmp, _ = env
    monkeypatch.delenv("ALLOW_PUBLISH")
    assert run.main(["article", "--article-file", write(tmp, make_article())]) == 0
    assert [p["status"] for p in wp.posts.values()] == ["draft"]
    assert ("status", "publish") not in wp.calls
    assert ledger.read_log()[-1]["status"] == "draft"


def test_review_mode_holds_even_passing_articles(env):
    wp, tmp, cfg = env
    cfg["publishing"]["mode"] = "review"
    assert run.main(["article", "--article-file", write(tmp, make_article())]) == 0
    assert [p["status"] for p in wp.posts.values()] == ["pending"]


def test_unknown_mode_rejected(env):
    wp, tmp, cfg = env
    cfg["publishing"]["mode"] = "live"
    with pytest.raises(ValueError):
        run.main(["article", "--article-file", write(tmp, make_article())])
    assert wp.posts == {}


def test_base_url_env_override(monkeypatch):
    monkeypatch.setenv("WP_BASE_URL", "https://staging.example.com/")
    assert run.load_cfg()["site"]["base_url"] == "https://staging.example.com"


def test_no_scheduled_workflows():
    for wf in (run.ROOT / ".github" / "workflows").glob("*.yml"):
        y = __import__("yaml").safe_load(wf.read_text())
        triggers = y.get("on", y.get(True))  # PyYAML parses bare `on` as True
        assert "schedule" not in (triggers or {}), wf.name


def test_publish_workflow_keeps_second_key_off():
    y = __import__("yaml").safe_load((run.ROOT / ".github/workflows/publish.yml").read_text())
    assert y["jobs"]["run"]["env"]["ALLOW_PUBLISH"] == "false"


def test_no_credentials_in_repo():
    import re
    pattern = re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}|[a-zA-Z0-9]{4}( [a-zA-Z0-9]{4}){5}|BEGIN (RSA )?PRIVATE KEY")
    for f in run.ROOT.rglob("*"):
        if f.is_file() and ".git" not in f.parts and f.suffix in (".py", ".yml", ".yaml", ".md", ".txt", ".json", ""):
            assert not pattern.search(f.read_text(errors="ignore")), f
