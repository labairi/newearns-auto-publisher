"""Fact-check verdicts, citations, internal links, the explained report and edit-only revalidation."""
import copy
import json

import pytest

from publisher import quality, run, writer
from tests.test_pipeline import CFG, EXISTING, SITE, SOURCE_URLS, checks, env, make_article, write  # noqa: F401

PRODUCT = "https://private-pdf-editor.com/?utm_source=newearns&utm_medium=referral&utm_campaign=compress-pdf-for-email"
AGENCY = f"{SITE}/how-to-start-your-digital-marketing-agency-in-2025/"


def names(rep):
    return {f.split(":")[0] for f in rep.failures}


def with_claims(*claims):
    a = make_article()
    a["fact_check"]["claims"] += list(claims)
    return a


# ------------------------------------------------------------ fact-check verdicts
def test_unsupported_claim_holds_and_is_not_called_false():
    a = with_claims({"claim": "Outlook.com's 25 MB limit is per file", "verdict": "unsupported",
                     "evidence": "page says only 'attachment size limit for files is 25 MB'",
                     "suggested_fix": "Say '25 MB attachment limit', as Microsoft does"})
    rep = checks(a)
    assert "fact_check_claims" in names(rep)
    detail = dict((n, d) for n, ok, d in rep.results)["fact_check_claims"]
    assert "no supporting evidence found (not shown to be false)" in detail
    assert "contradicted" not in detail and "fix: Say '25 MB attachment limit'" in detail


def test_contradicted_unsupported_and_uncertain_are_reported_separately():
    a = with_claims({"claim": "Gmail allows 50 MB", "verdict": "contradicted", "evidence": "says 25 MB"},
                    {"claim": "Mail Drop works on iOS 9.2 or later", "verdict": "unsupported", "evidence": ""},
                    {"claim": "Yahoo allows 25 MB", "verdict": "uncertain", "evidence": "page did not load"})
    detail = dict((n, d) for n, ok, d in checks(a).results)["fact_check_claims"]
    assert "1 contradicted by a source (likely wrong): Gmail allows 50 MB" in detail
    assert "1 no supporting evidence found (not shown to be false): Mail Drop" in detail
    assert "1 could not be checked (source unreadable or partial): Yahoo" in detail


def test_claim_without_a_verdict_is_never_treated_as_supported():
    a = with_claims({"claim": "Acrobat needs a subscription", "evidence": ""})
    assert "fact_check_claims" in names(checks(a))


def detail_of(rep, gate):
    return dict((n, d) for n, ok, d in rep.results)[gate]


def test_empty_claim_list_fails():
    a = make_article()
    a["fact_check"]["claims"] = []
    rep = checks(a)
    assert "fact_check_claims" in names(rep)
    assert detail_of(rep, "fact_check_claims") == "fact-check listed no claims"


def test_supported_claim_whose_source_was_not_fetched_fails():
    a = with_claims({"claim": "Outlook.com allows 20 MB", "verdict": "supported", "evidence": "search snippet",
                     "source_url": "https://support.microsoft.com/not-read-by-the-pipeline"})
    rep = checks(a)
    assert "fact_check_claims" in names(rep)
    detail = detail_of(rep, "fact_check_claims")
    assert "1 marked supported, but its source_url is missing or was not read by the pipeline: Outlook.com" in detail
    assert any("Source page not read" in line for line in rep.explained)


def test_supported_claim_without_source_url_fails():
    a = with_claims({"claim": "Yahoo allows 25 MB", "verdict": "supported", "evidence": "x"})
    assert "fact_check_claims" in names(checks(a))


def test_supported_claim_with_fetched_source_passes_source_evidence_check():
    # Same URL as fetched, written differently (utm tag, fragment, trailing slash): normalized match.
    a = with_claims({"claim": "Outlook lists its limits", "verdict": "supported", "evidence": "x",
                     "source_url": "https://support.microsoft.com/outlook-limits/?utm_source=x#limits"})
    rep = checks(a)
    assert rep.passed, rep.failures


def test_unread_source_does_not_turn_an_unsupported_claim_into_another_verdict():
    a = with_claims({"claim": "Mail Drop needs iOS 9.2", "verdict": "unsupported", "evidence": "",
                     "source_url": "https://support.apple.com/unread"})
    detail = detail_of(checks(a), "fact_check_claims")
    assert detail.startswith("1 no supporting evidence found (not shown to be false)") and "source_url" not in detail


def test_fact_check_prompts_define_verdicts_and_ask_for_fixes_and_source_relevance(monkeypatch):
    seen = []
    def fake_call(cfg, messages, **kw):
        seen.append(messages[0]["content"])
        if kw["stage"] == "fact_check":
            return "notes", {"stage": "fact_check"}
        return json.dumps({"claims": [], "sources": [], "satisfies_search_intent": True, "intent_notes": "",
                           "adds_original_value": True, "value_notes": "", "product_claims_accurate": True,
                           "product_notes": ""}), {"stage": "fact_check_json"}
    monkeypatch.setattr(writer, "_call", fake_call)
    writer.fact_check(CFG, make_article(), "pdf")
    check, convert = seen
    assert "Only this verdict means\n     the claim is false" in check and "not that the claim is false" in check
    assert "suggested_fix" in check and "relevant to, and actually supports" in check
    assert "use 'uncertain'" in convert and "never upgrade a claim to 'supported'" in convert
    claim = writer.FACTCHECK_SCHEMA["properties"]["claims"]["items"]
    assert "suggested_fix" in claim["required"] and "sources" in writer.FACTCHECK_SCHEMA["required"]
    assert "source_url" in claim["required"] and "A search-result snippet is not a page you read" in check
    assert "never invent one" in convert


# ------------------------------------------------------------ the fact-check must match the text
def test_fact_check_of_earlier_text_does_not_count():
    a = make_article()
    a["content_html"] = a["content_html"].replace("25 MB", "50 MB")  # edited after the fact-check
    rep = checks(a)
    assert "fact_check_current" in names(rep)
    assert "changed after the fact-check" in dict((n, d) for n, ok, d in rep.results)["fact_check_current"]


def test_fact_check_without_fingerprint_does_not_count():
    a = make_article()
    del a["fact_check"]["content_sha256"]
    assert "fact_check_current" in names(checks(a))


def test_manually_edited_article_with_old_fact_check_goes_to_review_not_live(env):
    wp, tmp, _ = env
    a = make_article()
    a["content_html"] += "<p>Outlook.com also accepts 34 MB attachments.</p>"
    assert run.main(["article", "--article-file", write(tmp, a)]) == 0
    assert [p["status"] for p in wp.posts.values()] == ["pending"]
    assert ("status", "publish") not in wp.calls


# ------------------------------------------------------------ sources and citations
def test_unreadable_source_is_flagged_not_treated_as_verified():
    a = make_article()
    a["fact_check"]["fetched_urls"] = list(SOURCE_URLS[:2])
    rep = checks(a)
    assert "sources_verified" in names(rep)
    assert "optimize could not be read" in dict((n, d) for n, ok, d in rep.results)["sources_verified"]


def test_irrelevant_source_is_flagged():
    a = make_article()
    a["fact_check"]["sources"][1] = {"url": SOURCE_URLS[1], "relevant": False,
                                     "notes": "page is about calendars, not attachment limits"}
    rep = checks(a)
    assert "sources_verified" in names(rep)
    assert "about calendars" in dict((n, d) for n, ok, d in rep.results)["sources_verified"]


def test_source_missing_from_relevance_review_is_flagged():
    a = make_article()
    a["fact_check"]["sources"] = a["fact_check"]["sources"][:2]
    assert "sources_verified" in names(checks(a))


def test_product_page_needs_no_relevance_review():
    a = make_article()
    a["sources"] = a["sources"] + [{"url": "https://private-pdf-editor.com/", "title": "PDF", "supports": "tool"}]
    assert checks(a).passed, checks(a).failures


def test_missing_citation_for_according_to_statement_fails_with_fix():
    a = make_article()
    a["content_html"] += "<p>According to Adobe, optimizing is only in Acrobat Pro.</p>"
    rep = checks(a)
    assert "statistics_have_sources" in names(rep)
    assert any("same paragraph" in line for line in rep.explained if line.startswith("statistics_have_sources"))


# ------------------------------------------------------------ internal links
def without_internal_link(note=""):
    a = make_article(internal_links_note=note)
    a["content_html"] = a["content_html"].replace(
        f'<p>For agencies sending proposals, see <a href="{AGENCY}">how to start an agency</a>.</p>', "")
    a["fact_check"]["content_sha256"] = quality.content_fingerprint(a["content_html"])
    return a


def test_no_relevant_internal_post_passes_when_the_article_says_so():
    a = without_internal_link("No published New Earns post covers PDFs or email attachments.")
    rep = checks(a)
    assert rep.passed, rep.failures
    detail = dict((n, d) for n, ok, d in rep.results)["internal_links_present"]
    assert detail.startswith("0 internal links; no relevant published post: No published")


def test_missing_internal_link_without_explanation_fails_with_fix():
    rep = checks(without_internal_link())
    assert "internal_links_present" in names(rep)
    assert any("never invent one" in line for line in rep.explained)


def test_product_link_is_not_an_internal_link():
    a = without_internal_link()
    assert PRODUCT in a["content_html"]
    assert "0 internal links" in dict((n, d) for n, ok, d in checks(a).results)["internal_links_present"]


@pytest.mark.parametrize("url", [f"{SITE}/how-to-compress-pdfs-planned-post/",          # planned, not published
                                 "https://www.newearns.com/made-up-post/",                 # invented, www host
                                 f"{SITE}/?p=3695"])                                       # a draft by id
def test_unpublished_or_invented_internal_url_fails(url):
    a = make_article()
    a["content_html"] += f'<p>See <a href="{url}">this guide</a>.</p>'
    assert "internal_links_valid" in names(checks(a))


def test_www_link_to_a_published_post_is_valid():
    a = make_article()
    a["content_html"] += f'<p>See <a href="{AGENCY.replace("https://", "https://www.")}#start">this</a>.</p>'
    a["fact_check"]["content_sha256"] = quality.content_fingerprint(a["content_html"])
    assert checks(a).passed, checks(a).failures


def test_internal_links_are_link_checked():
    rep = checks(make_article(), links=lambda u: (False, "404") if "newearns.com" in u else (True, "200"))
    assert "no_broken_links" in names(rep)


def test_site_without_published_posts_does_not_require_a_link():
    a = without_internal_link()
    rep = quality.run_checks(a, CFG, [], set(), link_checker=lambda u: (True, "200"), fact_check=a["fact_check"])
    assert rep.passed, rep.failures


def test_writer_is_told_to_use_only_listed_posts_and_explain_when_none_fit(monkeypatch):
    seen = {}
    def fake_call(cfg, messages, **kw):
        seen["prompt"] = messages[0]["content"]
        return json.dumps({k: "x" for k in writer.ARTICLE_SCHEMA["required"]} | {"sources": [],
                                                                                   "content_html": "<p>x</p>"}), {}
    monkeypatch.setattr(writer, "_call", fake_call)
    writer.write_article(CFG, "pdf", "brief", [{"title": "Agency", "link": AGENCY}])
    p = seen["prompt"]
    assert AGENCY in p and "Never link any other New Earns URL" in p and "internal_links_note" in p
    assert "internal_links_note" in writer.ARTICLE_SCHEMA["required"]


# ------------------------------------------------------------ the report
def test_every_failure_comes_with_a_practical_fix():
    a = make_article()
    a["content_html"] += "<p>About 73% of freelancers email PDFs daily. In my experience it works.</p>"
    rep = checks(a)
    assert rep.failures and len(rep.explained) == len(rep.failures)
    assert all("\n    Fix: " in line and "see the detail above" not in line for line in rep.explained)
    saved = [r for r in rep.as_dict() if not r["passed"]]
    assert saved and all(r["fix"] for r in saved)
    assert all("fix" not in r for r in rep.as_dict() if r["passed"])


# ------------------------------------------------------------ edit-only revalidation
def test_refresh_fact_check_revalidates_an_edited_article_without_generating(env, monkeypatch):
    wp, tmp, _ = env
    monkeypatch.setattr(writer, "api_key_problem", lambda: None)
    calls = []
    monkeypatch.setattr(writer, "research_brief", lambda *a, **k: calls.append("research"))
    monkeypatch.setattr(writer, "write_article", lambda *a, **k: calls.append("write"))
    def fresh(cfg, art, pillar, reader=None):
        calls.append("fact_check")
        fc = copy.deepcopy(make_article()["fact_check"])
        fc["content_sha256"] = quality.content_fingerprint(art["content_html"])
        return fc, [{"stage": "fact_check"}, {"stage": "fact_check_json"}]
    monkeypatch.setattr(writer, "fact_check", fresh)
    edited = make_article()
    edited["content_html"] = edited["content_html"].replace("readable", "easy to read")  # a manual correction
    f = write(tmp, edited)
    assert run.main(["article", "--article-file", f, "--dry-run"]) == 4   # stale fact-check: held
    assert run.main(["article", "--article-file", f, "--dry-run", "--refresh-fact-check"]) == 0
    assert calls == ["fact_check"] and wp.posts == {} and run.ledger.read_log() == []
    saved = next((tmp / "runs").glob("*/article.json"))
    assert run.main(["article", "--article-file", str(saved), "--dry-run"]) == 0  # no further API call
    assert calls == ["fact_check"]


@pytest.mark.parametrize("extra", [[], ["--dry-run"]])
def test_refresh_fact_check_only_for_saved_articles_in_dry_run(env, monkeypatch, extra):
    wp, tmp, _ = env
    monkeypatch.setattr(writer, "fact_check", lambda *a, **k: pytest.fail("must not run"))
    args = ["article", "--refresh-fact-check", *extra]
    if not extra:
        args += ["--article-file", write(tmp, make_article())]   # without --dry-run
    assert run.main(args) == 7 and wp.posts == {}


def test_held_run_state_file_can_be_rechecked_without_api_calls(env, monkeypatch, tmp_path):
    wp, tmp, _ = env
    monkeypatch.setattr(writer, "fact_check", lambda *a, **k: pytest.fail("must not run"))
    a = make_article()
    fc = a.pop("fact_check")
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"run_id": "x", "stages": {"pillar": "pdf", "article": a, "fact_check": fc}}))
    assert run.main(["article", "--article-file", str(state), "--dry-run"]) == 0
    assert wp.posts == {}


def test_recheck_passes_refresh_flag_only_to_recheck_and_keeps_second_key():
    y = __import__("yaml").safe_load((run.ROOT / ".github/workflows/publish.yml").read_text())
    inputs = y[True]["workflow_dispatch"]["inputs"]
    assert inputs["refresh_fact_check"]["type"] == "boolean" and inputs["refresh_fact_check"]["default"] is False
    script = next(s for s in y["jobs"]["run"]["steps"] if s.get("name") == "Run")["run"]
    lines = {l.strip().split(")")[0]: l for l in script.splitlines() if ")" in l and "python" in l}
    assert '"${refresh[@]}"' in lines["recheck"] and "--dry-run" in lines["recheck"]
    assert all("refresh" not in lines[c] for c in ("article", "article-dry-run", "check-setup"))
    assert "refresh" not in y["jobs"]["run"]["env"]["ALLOW_PUBLISH"]
