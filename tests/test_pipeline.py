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
        "internal_links_note": "",
        "fact_check": {"claims": [{"claim": "Gmail 25 MB", "verdict": "supported", "evidence": "x",
                                   "source_url": "https://support.google.com/mail/answer/6584"}],
                       "sources": [{"url": u, "relevant": True, "notes": ""} for u in SOURCE_URLS],
                       "fetched_urls": list(SOURCE_URLS),
                       "satisfies_search_intent": True, "intent_notes": "", "adds_original_value": True,
                       "value_notes": "", "product_claims_accurate": True, "product_notes": ""},
    }
    a.update(over)
    if "fact_check" not in over:  # the fact-check looked at exactly this text
        a["fact_check"]["content_sha256"] = quality.content_fingerprint(a["content_html"])
    return a


SOURCE_URLS = ("https://support.google.com/mail/answer/6584", "https://support.microsoft.com/outlook-limits",
               "https://helpx.adobe.com/acrobat/optimize")


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
    a = make_article()
    a["fact_check"]["fetched_urls"] = [u for u in SOURCE_URLS if "adobe" not in u]  # nobody could read it
    rep = checks(a, links=lambda u: (False, "404") if "adobe" in u else (True, "200"))
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
                 "content": {"rendered": p["content"]}, "aioseo_meta_data": {}, "date_gmt": p.get("date_gmt")}
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
def test_shipped_config_publishes_only_with_second_key_and_daily_spacing():
    # mode is "publish", but it only takes effect when the workflow sets ALLOW_PUBLISH=true
    # (see test_publish_mode_without_allow_publish_saves_draft and the workflow test below).
    assert CFG["publishing"]["mode"] == "publish"
    assert CFG["publishing"]["min_hours_between_posts"] >= 20


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


def test_only_one_daily_schedule_and_only_in_publish_workflow():
    # Replaces the old "no schedules" test: one daily cron, nowhere else (see tests/test_schedule.py).
    for wf in (run.ROOT / ".github" / "workflows").glob("*.yml"):
        y = __import__("yaml").safe_load(wf.read_text())
        triggers = y.get("on", y.get(True)) or {}  # PyYAML parses bare `on` as True
        if wf.name == "publish.yml":
            assert triggers["schedule"] == [{"cron": "0 11 * * *"}, {"cron": "30 11 * * *"}]
            assert "workflow_dispatch" in triggers
        else:
            assert "schedule" not in triggers, wf.name


def test_publish_workflow_second_key_needs_article_on_main_and_ticked_box():
    y = __import__("yaml").safe_load((run.ROOT / ".github/workflows/publish.yml").read_text())
    inputs = y[True]["workflow_dispatch"]["inputs"]
    assert inputs["allow_publish"] == {"description": inputs["allow_publish"]["description"],
                                       "type": "boolean", "default": False}
    assert y["jobs"]["run"]["env"]["ALLOW_PUBLISH"] == (
        "${{ (github.event_name == 'schedule' || (inputs.command == 'article' && inputs.allow_publish))"
        " && github.ref == 'refs/heads/main' && 'true' || 'false' }}")
    assert "ALLOW_PUBLISH" not in str(y["jobs"]["run"]["steps"])  # nothing else can turn it on


def test_publish_workflow_passes_calendar_topic_only_to_article_runs():
    y = __import__("yaml").safe_load((run.ROOT / ".github/workflows/publish.yml").read_text())
    inputs = y[True]["workflow_dispatch"]["inputs"]
    assert inputs["pillar"]["options"] == ["none", *CFG["pillars"]]
    script = next(s for s in y["jobs"]["run"]["steps"] if s.get("name") == "Run")["run"]
    lines = {l.strip().split(")")[0]: l for l in script.splitlines() if ")" in l and "python" in l}
    assert '"${plan[@]}"' in lines["article"] and '"${plan[@]}"' in lines["article-dry-run"]
    assert "plan" not in lines["recheck"] and "plan" not in lines["check-setup"]


# ------------------------------------------------------------ calendar topics
def test_calendar_entries_are_valid_and_unique():
    cal = CFG["calendar"]
    assert cal and all(e["pillar"] in CFG["pillars"] and e["topic"] and e["query"] for e in cal)
    assert len({e["topic"].lower() for e in cal}) == len(cal)


@pytest.mark.parametrize("extra", [
    ["--pillar", "pdf"],                                            # topic missing
    ["--topic", "How to Compress a PDF for Email"],                 # pillar missing
    ["--pillar", "crypto", "--topic", "How to Compress a PDF for Email"],
    ["--pillar", "instagram", "--topic", "How to Compress a PDF for Email"],  # wrong pillar
    ["--pillar", "pdf", "--topic", "Best PDF tools 2026"],          # not in the calendar
])
def test_invalid_calendar_topic_refused_before_any_work(env, monkeypatch, extra):
    wp, tmp, _ = env
    monkeypatch.setattr(run.writer, "research_brief", lambda *a, **k: pytest.fail("generated"))
    assert run.main(["article", "--dry-run", *extra]) == 7
    assert run.main(["article", *extra]) == 7
    assert wp.posts == {} and wp.calls == []


def test_live_publishing_of_generated_article_needs_calendar_topic(env, monkeypatch):
    wp, tmp, _ = env
    monkeypatch.setattr(run.writer, "research_brief", lambda *a, **k: pytest.fail("generated"))
    assert run.main(["article"]) == 7
    assert wp.posts == {} and wp.calls == []


def test_calendar_topic_cannot_be_combined_with_article_file(env):
    wp, tmp, _ = env
    f = write(tmp, make_article())
    assert run.main(["article", "--article-file", f, "--pillar", "pdf",
                     "--topic", "How to Compress a PDF for Email"]) == 7
    assert wp.posts == {}


def _fake_generation(monkeypatch, seen):
    monkeypatch.setattr(run.writer, "api_key_problem", lambda: None)
    def brief(cfg, pillar, cands, titles, notes, reader=None):
        seen.update(pillar=pillar, candidates=cands)
        return "brief", {"stage": "research"}
    monkeypatch.setattr(run.writer, "research_brief", brief)
    monkeypatch.setattr(run.writer, "write_article", lambda *a, **k: (make_article(), {"stage": "write"}))
    monkeypatch.setattr(run.writer, "fact_check",
                        lambda cfg, art, pillar, reader=None: (make_article()["fact_check"], [{"stage": "fact_check"}]))


def test_calendar_topic_drives_generation_and_publishes_after_gates(env, monkeypatch):
    wp, tmp, _ = env
    seen = {}
    _fake_generation(monkeypatch, seen)
    assert run.main(["article", "--pillar", "pdf", "--topic", "how to compress a pdf for email"]) == 0
    assert seen == {"pillar": "pdf",
                    "candidates": ["How to Compress a PDF for Email (primary query: compress pdf for email)"]}
    assert [p["status"] for p in wp.posts.values()] == ["publish"]
    # The daily spacing rule still blocks a second live article the same day.
    assert run.main(["article", "--pillar", "pdf", "--topic", "How to Split a PDF into Separate Files"]) == 0
    assert len(wp.posts) == 1


def test_calendar_topic_article_that_fails_gates_is_not_published(env, monkeypatch):
    wp, tmp, _ = env
    _fake_generation(monkeypatch, {})
    bad = make_article()
    bad["content_html"] += "<p>About 73% of freelancers email PDFs daily.</p>"
    monkeypatch.setattr(run.writer, "write_article", lambda *a, **k: (bad, {"stage": "write"}))
    assert run.main(["article", "--pillar", "pdf", "--topic", "How to Compress a PDF for Email"]) == 0
    assert [p["status"] for p in wp.posts.values()] == ["pending"]
    assert ("status", "publish") not in wp.calls


def test_no_credentials_in_repo():
    import re
    pattern = re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}|\b[a-zA-Z0-9]{4}( [a-zA-Z0-9]{4}){5}\b|BEGIN (RSA )?PRIVATE KEY")
    for f in run.ROOT.rglob("*"):
        if f.is_file() and ".git" not in f.parts and f.suffix in (".py", ".yml", ".yaml", ".md", ".txt", ".json", ""):
            assert not pattern.search(f.read_text(errors="ignore")), f


@pytest.mark.parametrize("value,ok", [
    ("sk-ant-api03-abc", True),
    ("", False),
    ("curl https://api.anthropic.com/v1/messages \\\n  --header x-api-key: sk-ant-x", False),
    ("sk-ant-abc\n", False),
    ("abc123", False),
])
def test_api_key_validation(monkeypatch, value, ok):
    from publisher import writer
    monkeypatch.setenv("ANTHROPIC_API_KEY", value)
    assert (writer.api_key_problem() is None) is ok


# ------------------------------------------------------------ fixes from dry run 20261020T133931870297Z
class _Resp:
    def __init__(self, code):
        self.status_code = code


def test_link_check_retries_get_when_head_404s(monkeypatch):
    monkeypatch.setattr(quality.requests, "head", lambda *a, **k: _Resp(404))
    monkeypatch.setattr(quality.requests, "get", lambda *a, **k: _Resp(200))
    assert quality.check_link("https://www.ftc.gov/x") == (True, "200")


def test_link_check_still_fails_when_get_404s(monkeypatch):
    monkeypatch.setattr(quality.requests, "head", lambda *a, **k: _Resp(404))
    monkeypatch.setattr(quality.requests, "get", lambda *a, **k: _Resp(404))
    assert quality.check_link("https://example.com/gone") == (False, "404")


def test_alt_text_describes_card_and_fits_gate():
    from publisher import images
    alt = images.alt_text("Vet Influencers Before You Pay")
    assert "Vet Influencers Before You Pay" in alt and 10 <= len(alt) <= 125
    assert len(images.alt_text("x" * 200)) <= 125


def test_dry_run_uses_generated_alt_text(env):
    wp, tmp, _ = env
    art = make_article(image_alt_text="A very long invented description " * 10)
    run.main(["article", "--article-file", write(tmp, art), "--dry-run"])
    from publisher import ledger as lg
    state = json.loads(next((lg.RUNS).glob("*/state.json")).read_text())
    assert not [c for c in state["stages"]["checks"] if c["check"] == "featured_image_alt" and not c["passed"]]


def test_writer_prompt_requires_distinct_sources_and_lists_banned_words(monkeypatch):
    from publisher import writer
    seen = {}
    def fake_call(cfg, messages, **kw):
        seen["prompt"] = messages[0]["content"]
        return json.dumps({k: "x" for k in writer.ARTICLE_SCHEMA["required"]} | {"sources": [], "content_html": "<p>x</p>"}), {}
    monkeypatch.setattr(writer, "_call", fake_call)
    writer.write_article(CFG, "instagram", "brief", [])
    p = seen["prompt"]
    assert f"at least {CFG['quality']['min_sources']} different websites" in p
    assert '"testimonial"' in p and "image_alt_text" not in p


def test_revised_vetting_article_passes_offline_gates_but_needs_a_pipeline_fact_check():
    path = run.ROOT / "data/revisions/vet-instagram-influencer-before-paying.json"
    art = json.loads(path.read_text())
    from publisher import images
    art["image_alt_text"] = images.alt_text(art["image_headline"])
    art["featured_image"] = {"alt_text": art["image_alt_text"], "license": "generated-in-house"}
    rep = quality.run_checks(art, CFG, EXISTING, set(), link_checker=ok_links, fact_check=art["fact_check"])
    # Its fact-check was written by hand: it can't be tied to this exact text, has no per-source
    # relevance review and no per-claim source_url, so only a refreshed pipeline fact-check clears it.
    assert {f.split(":")[0] for f in rep.failures} == {"fact_check_claims", "fact_check_current",
                                                       "sources_verified"}, rep.failures


def test_link_check_tries_second_user_agent(monkeypatch):
    def head(url, headers, **k):
        return _Resp(400 if "compatible" in headers["User-Agent"] else 200)
    monkeypatch.setattr(quality.requests, "head", head)
    monkeypatch.setattr(quality.requests, "get", lambda url, headers, **k: head(url, headers))
    assert quality.check_link("https://help.instagram.com/1")[0]


def test_link_blocked_for_runner_but_fetched_by_fact_check_passes():
    art = make_article()
    blocked = "https://support.google.com/mail/answer/6584"
    links = lambda u: (False, "404") if u == blocked else (True, "200")
    assert blocked in art["fact_check"]["fetched_urls"]
    rep = quality.run_checks(art, CFG, EXISTING, set(), link_checker=links, fact_check=art["fact_check"])
    assert rep.passed, rep.failures
    fc = dict(art["fact_check"], fetched_urls=[u for u in SOURCE_URLS if u != blocked])
    rep = quality.run_checks(art, CFG, EXISTING, set(), link_checker=links, fact_check=fc)
    assert any("no_broken_links" in f for f in rep.failures)


def test_fetched_urls_only_counts_successful_fetches():
    from types import SimpleNamespace as NS
    from publisher import writer
    content = [NS(type="web_fetch_tool_result", content=NS(type="web_fetch_result", url="https://ok.example/a")),
               NS(type="web_fetch_tool_result", content=NS(type="web_fetch_tool_error", error_code="url_not_accessible")),
               NS(type="text", text="x")]
    assert writer._fetched_urls(content) == ["https://ok.example/a"]
