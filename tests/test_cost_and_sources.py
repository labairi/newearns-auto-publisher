"""Source extraction, per-run fetch cache, Claude call loop and cost reporting.
No network and no Claude API calls: fetchers and the client are fakes."""
import json
from types import SimpleNamespace as NS

import pytest

from publisher import quality, run, sources, writer

CFG = run.load_cfg()

LEGAL = ("Civil penalties apply only to knowing violations. The rule does not apply to reviews "
         "that a business did not procure, except where it knew they were fake.")
PAGE = f"""<!doctype html><html><head><title>Fake reviews rule | Agency</title>
<meta property="article:published_time" content="2024-08-14">
<script type="application/ld+json">{{"@type":"Article","dateModified":"2024-09-01"}}</script>
<style>.x{{color:red}}</style><script>var tracking = "do not keep me";</script></head>
<body>
<div class="usa-banner">An official website of the United States government</div>
<header><nav><ul><li>Home</li><li>News</li><li>About us</li></ul></nav></header>
<div id="cookie-consent"><p>We use cookies. Accept all cookies?</p></div>
<main>
<h1>Final rule on fake reviews</h1>
<p>The agency announced a final rule banning fake reviews and testimonials on August 14, 2024.</p>
<h2>Scope</h2>
<p>{"Background paragraph about the history of consumer reviews. " * 12}</p>
<p>The rule covers reviews that misrepresent the reviewer's experience with a product.</p>
<p>{LEGAL}</p>
<h2>Other topics</h2>
<p>{"Unrelated paragraph about agency staffing and budgets. " * 15}</p>
<p>{"Another unrelated paragraph about conference schedules. " * 15}</p>
<div class="share-buttons"><p>Share on Facebook</p></div>
</main>
<aside><p>Related: other press releases</p></aside>
<footer><p>Privacy policy | Contact | Accessibility</p></footer>
</body></html>"""


def fetcher_for(pages, calls=None):
    def fetch(url):
        if calls is not None:
            calls.append(url)
        v = pages.get(url)
        if v is None:
            return 404, "text/html", url, ""
        if isinstance(v, Exception):
            return 0, "", url, type(v).__name__
        if isinstance(v, tuple):
            return v
        return 200, "text/html; charset=utf-8", url, v
    return fetch


# ---------------------------------------------------------------- extraction
def test_boilerplate_removed_and_main_content_kept():
    ex = sources.extract(PAGE)
    text = " ".join(b["text"] for b in ex["blocks"])
    assert "announced a final rule" in text and LEGAL in text
    for junk in ("tracking", "color:red", "official website", "Accept all cookies", "About us",
                 "Share on Facebook", "Related: other", "Privacy policy"):
        assert junk not in text, junk
    assert ex["title"] == "Fake reviews rule | Agency"
    assert ex["published"] == "2024-08-14" and ex["modified"] == "2024-09-01"
    assert [b["text"] for b in ex["blocks"] if b["heading"]] == ["Final rule on fake reviews", "Scope", "Other topics"]


def test_page_without_main_falls_back_to_body_minus_boilerplate():
    html = "<html><body><nav>Menu Home</nav>" + "<p>Real body text about invoices and payment terms.</p>" * 20 + \
           "<footer>Footer links</footer></body></html>"
    text = " ".join(b["text"] for b in sources.extract(html)["blocks"])
    assert "invoices" in text and "Menu" not in text and "Footer" not in text


def test_repeated_short_blocks_are_deduplicated():
    html = "<main>" + "<p>Subscribe to our newsletter</p><p>Unique fact number %d about tax deadlines.</p>" * 5 + "</main>"
    blocks = [b["text"] for b in sources.extract(html % (1, 2, 3, 4, 5))["blocks"]]
    assert blocks.count("Subscribe to our newsletter") == 1 and len(blocks) == 6


# ---------------------------------------------------------------- excerpts
def test_excerpt_respects_limit_and_keeps_relevant_evidence_with_qualifiers():
    blocks = sources.extract(PAGE)["blocks"]
    excerpt, complete = sources.select_passages(blocks, "civil penalties knowing violations", max_chars=900)
    assert not complete
    assert LEGAL in excerpt                   # the exception travels with the claim
    assert "## Scope" in excerpt              # with its section heading
    assert "staffing and budgets" not in excerpt
    assert len(excerpt) <= 900 + 100          # limit plus "[...]" markers


def test_small_page_is_returned_whole():
    html = "<main><h2>Limits</h2><p>Attachments can be up to 25 MB, unless you use Drive links.</p></main>"
    excerpt, complete = sources.select_passages(sources.extract(html)["blocks"], "anything", 6000)
    assert complete and "unless you use Drive links" in excerpt


def test_long_single_block_is_cut_at_a_sentence_and_marked():
    blocks = [{"text": "Rule sentence about deposits. " * 400, "heading": False, "section": ""}]
    excerpt, complete = sources.select_passages(blocks, "deposits", 1000)
    assert not complete and "[block continues]" in excerpt and len(excerpt) < 1100


def test_partial_excerpt_tells_claude_how_to_read_more():
    r = sources.SourceReader(None, fetcher_for({"https://agency.example/rule": PAGE}), excerpt_chars=900)
    res = r.read("https://agency.example/rule", "civil penalties")
    assert res["status"] == "ok" and res["complete"] is False
    assert "Scope" in res["sections"] and "narrower focus" in res["note"]
    assert res["published"] == "2024-08-14"


# ---------------------------------------------------------------- fetch once per run
def test_same_url_fetched_once_even_with_fragment_utm_or_slash(tmp_path):
    calls = []
    r = sources.SourceReader(tmp_path, fetcher_for({"https://agency.example/rule": PAGE}, calls))
    for u in ("https://agency.example/rule", "https://agency.example/rule/", "https://agency.example/rule#scope",
              "https://AGENCY.example/rule?utm_source=x"):
        r.read(u, "fake reviews")
    assert calls == ["https://agency.example/rule"] and r.network_fetches == 1


def test_cache_persists_for_resume_and_fact_check(tmp_path):
    calls = []
    fetch = fetcher_for({"https://agency.example/rule": PAGE}, calls)
    sources.SourceReader(tmp_path, fetch).read("https://agency.example/rule", "x")
    later = sources.SourceReader(tmp_path, fetch)  # e.g. the fact-check stage or a resumed run
    assert later.read("https://agency.example/rule", "civil penalties")["status"] == "ok"
    assert len(calls) == 1 and later.network_fetches == 0


def test_read_limit_stops_runaway_tool_use():
    r = sources.SourceReader(None, fetcher_for({"https://a.example/x": PAGE}), max_reads=2)
    r.read("https://a.example/x", "a"); r.read("https://a.example/x", "b")
    assert r.read("https://a.example/x", "c")["status"] == "limit_reached"


# ---------------------------------------------------------------- unreadable pages
@pytest.mark.parametrize("value,reason", [
    (None, "HTTP 404"),
    (ConnectionError(), "ConnectionError"),
    ((200, "application/pdf", "https://a.example/x", "%PDF-1.7"), "not an HTML page"),
])
def test_unavailable_pages_are_not_evidence(value, reason):
    r = sources.SourceReader(None, fetcher_for({"https://a.example/x": value}))
    res = r.read("https://a.example/x", "anything")
    assert res["status"] == "unavailable" and reason in res["note"] and "web_fetch" in res["note"]
    assert "excerpt" not in res and r.verified_urls() == []


def test_too_short_page_is_flagged_and_not_verified():
    r = sources.SourceReader(None, fetcher_for({"https://a.example/js": "<html><body><div id=app>Loading</div></body></html>"}))
    res = r.read("https://a.example/js", "anything")
    assert res["status"] == "too_short" and "web_fetch" in res["note"] and r.verified_urls() == []


def test_non_http_url_rejected_without_fetching():
    calls = []
    r = sources.SourceReader(None, fetcher_for({}, calls))
    assert r.read("file:///etc/passwd", "x")["status"] == "unavailable" and calls == []


# ---------------------------------------------------------------- Claude call loop (fake client)
class FakeStream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


class FakeClient:
    def __init__(self, replies):
        self.replies, self.requests = list(replies), []
        self.beta = NS(messages=NS(stream=self.stream))

    def stream(self, **kw):
        self.requests.append(kw)
        return FakeStream(self.replies.pop(0))


def usage(i, cw, cr, o, s=0, f=0):
    return NS(input_tokens=i, cache_creation_input_tokens=cw, cache_read_input_tokens=cr, output_tokens=o,
              server_tool_use=NS(web_search_requests=s, web_fetch_requests=f))


def msg(stop, content, u):
    return NS(stop_reason=stop, content=content, usage=u, model="claude-opus-5-5")


def test_call_runs_read_source_locally_caches_and_records_usage(monkeypatch):
    replies = [
        msg("tool_use", [NS(type="tool_use", id="t1", name="read_source",
                            input={"url": "https://agency.example/rule", "focus": "civil penalties"})],
            usage(3000, 0, 0, 200, s=2)),
        msg("pause_turn", [NS(type="web_fetch_tool_result",
                              content=NS(type="web_fetch_result", url="https://blocked.example/a"))],
            usage(100, 5000, 3000, 300, f=1)),
        msg("end_turn", [NS(type="text", text="brief")], usage(50, 800, 8000, 1000)),
    ]
    client = FakeClient(replies)
    monkeypatch.setattr(writer, "_client", lambda: client)
    reader = sources.SourceReader(None, fetcher_for({"https://agency.example/rule": PAGE}))
    text, u = writer._call(CFG, [{"role": "user", "content": "go"}], system="s", stage="research",
                           tools=writer._tools(CFG, 8, reader), reader=reader)
    assert text == "brief"
    assert all(r["cache_control"] == {"type": "ephemeral"} for r in client.requests)
    # the tool result went back as a compact excerpt, not the page
    tool_turn = client.requests[1]["messages"][-1]["content"][0]
    body = json.loads(tool_turn["content"])
    assert tool_turn["tool_use_id"] == "t1" and LEGAL in body["excerpt"] and "tracking" not in tool_turn["content"]
    assert u["requests"] == 3 and u["read_source_calls"] == 1
    assert (u["input_tokens"], u["cache_creation_input_tokens"], u["cache_read_input_tokens"], u["output_tokens"]) \
        == (3150, 5800, 11000, 1500)
    assert u["web_search_requests"] == 2 and u["web_fetch_requests"] == 1
    assert u["fetched_urls"] == ["https://blocked.example/a"]
    assert u["cost_usd"] == writer.stage_cost(u)


def test_unknown_tool_gets_an_error_result():
    res = writer._run_tool(NS(type="tool_use", id="x", name="shell", input={"cmd": "ls"}), None)
    assert res["is_error"] and res["tool_use_id"] == "x"


def test_web_fetch_is_capped_fallback_and_read_source_listed_first():
    tools = writer._tools(CFG, 5, sources.SourceReader(None))
    assert tools[0]["name"] == "read_source"
    wf = next(t for t in tools if t["name"] == "web_fetch")
    assert wf["max_uses"] == CFG["models"]["max_web_fetches"] and wf["max_content_tokens"] == CFG["models"]["max_fetch_tokens"]


def test_fact_check_gets_evidence_pack_and_verified_urls(monkeypatch):
    seen = []

    def fake_call(cfg, messages, **kw):
        seen.append((kw["stage"], messages[0]["content"]))
        if kw["stage"] == "fact_check":
            return "notes", {"stage": "fact_check", "model": "claude-opus-5-5", "input_tokens": 10,
                             "fetched_urls": ["https://blocked.example/a"]}
        return json.dumps({"claims": [], "satisfies_search_intent": True, "intent_notes": "",
                           "adds_original_value": True, "value_notes": "", "product_claims_accurate": True,
                           "product_notes": ""}), {"stage": "fact_check_json", "model": "claude-opus-5-5"}
    monkeypatch.setattr(writer, "_call", fake_call)
    art = {"sources": [{"url": "https://agency.example/rule", "title": "Rule", "supports": "civil penalties"},
                       {"url": "https://blocked.example/a", "title": "B", "supports": "x"}],
           "content_html": '<p>Penalties apply to <a href="https://agency.example/rule">knowing violations</a>.</p>',
           "search_intent": "how-to", "unique_value": "checklist"}
    reader = sources.SourceReader(None, fetcher_for({"https://agency.example/rule": PAGE}))
    fc, usages = writer.fact_check(CFG, art, "instagram", reader=reader)
    prompt = seen[0][1]
    assert "EVIDENCE EXTRACTED" in prompt and LEGAL in prompt and "do not trust it blindly" in prompt
    assert '"status": "unavailable"' in prompt          # the blocked source is flagged, not hidden
    assert fc["fetched_urls"] == ["https://agency.example/rule", "https://blocked.example/a"]
    assert [u["stage"] for u in usages] == ["fact_check", "fact_check_json"]


# ---------------------------------------------------------------- cost reporting
def test_stage_cost_prices_cache_tokens():
    u = {"model": "claude-opus-5-5", "input_tokens": 1_000_000, "cache_creation_input_tokens": 1_000_000,
         "cache_read_input_tokens": 1_000_000, "output_tokens": 1_000_000, "web_search_requests": 100}
    assert writer.stage_cost(u) == round(4 + 5 + 0.20 + 20 + 1.0, 3)
    assert writer.stage_cost({**u, "model": "claude-sonnet-5-5"}) == round(2 + 2.5 + 0.20 + 10 + 1.0, 3)


def test_saved_run_cost_reproduces_reported_estimate():
    state = json.loads((run.ROOT / "data/runs/20261020T133931870297Z/state.json").read_text())
    assert abs(writer.estimate_cost(state["stages"]["usages"]) - 4.362) < 0.002


def test_total_is_sum_of_stages_and_table_lists_each():
    us = [{"stage": "research", "model": "claude-opus-5-5", "input_tokens": 20000, "cache_read_input_tokens": 300000,
           "cache_creation_input_tokens": 60000, "output_tokens": 12000, "web_search_requests": 8},
          {"stage": "write", "model": "claude-opus-5-5", "input_tokens": 6000, "output_tokens": 12000}]
    assert writer.estimate_cost(us) == round(sum(writer.stage_cost(u) for u in us), 3)
    rows = writer.cost_table(us)
    assert rows[1].startswith("research |") and rows[2].startswith("write |") and rows[-1].startswith("total")


def test_dry_run_records_per_stage_cost(monkeypatch, tmp_path):
    from tests.test_pipeline import make_article, FakeWP, ok_links
    from publisher import ledger
    for name in ("DATA", "LOG", "RUNS", "LOCK"):
        monkeypatch.setattr(ledger, name, {"DATA": tmp_path, "LOG": tmp_path / "log.jsonl",
                                           "RUNS": tmp_path / "runs", "LOCK": tmp_path / ".lock"}[name])
    monkeypatch.setattr(run.WordPress, "from_env", classmethod(lambda cls, url: FakeWP()))
    monkeypatch.setattr(quality.run_checks, "__defaults__", (ok_links, None))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    art = make_article()
    u = lambda stage: {"stage": stage, "model": "claude-opus-5-5", "input_tokens": 1000, "output_tokens": 1000}
    monkeypatch.setattr(run.writer, "research_brief", lambda *a, **k: ("brief", u("research")))
    monkeypatch.setattr(run.writer, "write_article", lambda *a, **k: (art, u("write")))
    monkeypatch.setattr(run.writer, "fact_check", lambda *a, **k: (art["fact_check"], [u("fact_check"), u("fact_check_json")]))
    assert run.main(["article", "--dry-run"]) == 0
    state = json.loads(next((tmp_path / "runs").glob("*/state.json")).read_text())
    cost = state["stages"]["cost"]
    assert set(cost["by_stage"]) == {"research", "write", "fact_check", "fact_check_json"}
    assert cost["total_usd"] == round(sum(cost["by_stage"].values()), 3) == 0.096


# ---------------------------------------------------------------- safeguards untouched
GATES = {"word_count", "structure_h2", "no_h1_in_body", "headings_unique", "keyphrase_in_title", "keyphrase_early",
         "keyword_density", "headings_not_stuffed", "meta_description_length", "seo_title_length", "slug_format",
         "slug_unused", "not_near_duplicate", "distinct_search_intent", "keyphrase_not_targeted_before",
         "no_fabricated_experience_or_hype", "https_only", "no_broken_links", "internal_links_valid",
         "internal_links_present", "product_links_limited", "product_links_tracked", "ownership_disclosed",
         "product_link_justified", "enough_independent_sources", "sources_cited_in_body", "statistics_have_sources",
         "featured_image_alt", "image_rights", "inline_images_have_alt", "fact_check_claims", "fact_check_intent",
         "fact_check_original_value", "fact_check_product_claims"}


def test_all_34_quality_gates_still_run():
    from tests.test_pipeline import make_article, EXISTING
    art = make_article()
    rep = quality.run_checks(art, CFG, EXISTING, set(), link_checker=ok, fact_check=art["fact_check"])
    assert {n for n, _, _ in rep.results} == GATES and len(rep.results) == 34


def ok(url):
    return True, "200"


def test_thresholds_and_safety_settings_unchanged():
    q = CFG["quality"]
    assert q["min_sources"] == 3 and q["max_duplicate_similarity"] == 0.35 and q["max_title_similarity"] == 0.80
    assert CFG["publishing"]["mode"] == "draft"
    assert all(v is None or isinstance(v, str) for v in (CFG["models"].get("stages") or {}).values())
