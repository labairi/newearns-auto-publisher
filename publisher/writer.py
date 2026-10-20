"""Claude stages: research brief -> article -> independent fact-check.

Each stage is a separate request so the fact-checker does not grade its own
draft from memory: it re-reads the cited sources (local read_source excerpts,
with server-side web_fetch as the fallback for pages the runner cannot open).
"""
from __future__ import annotations

import json
import os

import anthropic

from . import quality, sources

BETAS = ["server-side-fallback-2026-07-01"]

ARTICLE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "seo_title", "slug", "meta_description", "focus_keyphrase", "excerpt",
                 "content_html", "sources", "product_link_reason", "image_headline",
                 "search_intent", "unique_value"],
    "properties": {
        "title": {"type": "string"},
        "seo_title": {"type": "string"},
        "slug": {"type": "string"},
        "meta_description": {"type": "string"},
        "focus_keyphrase": {"type": "string"},
        "excerpt": {"type": "string"},
        "content_html": {"type": "string"},
        "sources": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["url", "title", "supports"],
            "properties": {"url": {"type": "string"}, "title": {"type": "string"}, "supports": {"type": "string"}}}},
        "product_link_reason": {"type": "string"},
        "image_headline": {"type": "string"},
        "search_intent": {"type": "string"},
        "unique_value": {"type": "string"},
    },
}

FACTCHECK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["claims", "satisfies_search_intent", "intent_notes", "adds_original_value", "value_notes",
                 "product_claims_accurate", "product_notes"],
    "properties": {
        "claims": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["claim", "verdict", "evidence"],
            "properties": {
                "claim": {"type": "string"},
                "verdict": {"type": "string", "enum": ["supported", "unsupported", "contradicted", "uncertain"]},
                "evidence": {"type": "string"}}}},
        "satisfies_search_intent": {"type": "boolean"},
        "intent_notes": {"type": "string"},
        "adds_original_value": {"type": "boolean"},
        "value_notes": {"type": "string"},
        "product_claims_accurate": {"type": "boolean"},
        "product_notes": {"type": "string"},
    },
}

EDITORIAL_RULES = """\
New Earns (newearns.com) is a blog about earning money online: freelancing,
side businesses, creator income, online tools. Readers are freelancers, small
business owners, creators and people starting online work.

Non-negotiable editorial rules:
- Never invent statistics, studies, quotes, case studies, customers or testimonials.
- Never claim first-hand testing or personal experience. Write as an editorial
  guide: "here is how to do it", not "I tried it".
- Every number, statistic or research claim links to the source it came from,
  inside the same paragraph or list item. Prefer official documentation
  (Adobe, Microsoft, Google, Meta/Instagram Help Center, FTC, ISO, government)
  and well-known publications. No AI-chat utm parameters on links.
- Step-by-step instructions must be accurate for the current version of the
  tool you describe; if a UI may differ, say so.
- Product mentions are optional. Link a product only where it directly solves
  the step the reader is on, at most twice, and only make claims from the
  allowed-claims list. Never disparage competitors; comparisons must be fair
  and sourced.
- No keyword stuffing: the focus keyphrase appears naturally (title, intro,
  once or twice more). Headings describe the section, not the keyword.
- Plain, specific, helpful English. No filler intros, no "in today's digital world".
"""


class Refused(RuntimeError):
    pass


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(max_retries=4, timeout=900)


# List prices, USD per million tokens, from platform.claude.com/docs/en/about-claude/pricing
# (checked 2026-10-20): (base input, 5-minute cache write, 1-hour cache write, cache read, output).
# Includes the models the server-side fallback ("default") can hand a request to.
PRICES = {
    "claude-opus-5-5": (4.0, 5.0, 8.0, 0.20, 20.0),
    "claude-opus-5": (5.0, 6.25, 10.0, 0.50, 25.0),
    "claude-opus-4-8": (5.0, 6.25, 10.0, 0.50, 25.0),
    "claude-sonnet-5-5": (2.0, 2.50, 4.0, 0.10, 10.0),
    "claude-sonnet-5": (2.0, 2.50, 4.0, 0.20, 10.0),
    "claude-fable-5-1": (10.0, 12.50, 20.0, 0.25, 50.0),
}
# A model missing from the table is priced at the highest rates above, so the estimate errs high.
UNKNOWN_PRICE = tuple(max(p[i] for p in PRICES.values()) for i in range(5))
SEARCH_USD = 10 / 1000  # web search: $10 per 1,000 searches; web fetch has no per-call fee
TOKEN_KEYS = ("input_tokens", "cache_write_5m_tokens", "cache_write_1h_tokens", "cache_read_input_tokens",
              "output_tokens")
USAGE_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens",
              "web_search_requests", "web_fetch_requests", "requests", "read_source_calls")


def stage_model(cfg: dict, stage: str) -> str:
    """Per-stage model override (models.stages.<stage>), else the writer model."""
    return (cfg["models"].get("stages") or {}).get(stage) or cfg["models"]["writer"]


def _call(cfg: dict, messages: list, *, system: str, stage: str, tools: list | None = None,
          schema: dict | None = None, max_tokens: int = 32000, reader: sources.SourceReader | None = None,
          max_rounds: int = 40):
    """One streamed exchange. Runs the read_source tool locally, resumes server-tool
    pause_turn, raises on refusal. Prompt caching is on, so each round re-reads the
    growing conversation at the cache-read price instead of the full input price."""
    client = _client()
    model = stage_model(cfg, stage)
    kwargs = dict(model=model, max_tokens=max_tokens, system=system, betas=BETAS, fallbacks="default",
                  output_config={"effort": cfg["models"]["effort"]}, cache_control={"type": "ephemeral"})
    if tools:
        kwargs["tools"] = tools
    if schema:
        kwargs["output_config"]["format"] = {"type": "json_schema", "schema": schema}
    usage = {"stage": stage, "model": model, **{k: 0 for k in USAGE_KEYS}}
    served_by: set[str] = set()
    fetched: list[str] = []
    for _ in range(max_rounds):
        with client.beta.messages.stream(messages=messages, **kwargs) as stream:
            msg = stream.get_final_message()
        served = getattr(msg, "model", None) or model
        _add_usage(usage, msg.usage, model, served)
        served_by.add(served)
        fetched.extend(_fetched_urls(msg.content))
        if msg.stop_reason == "refusal":
            raise Refused(str(getattr(msg, "stop_details", "")))
        if msg.stop_reason == "pause_turn":
            messages = messages + [{"role": "assistant", "content": msg.content}]
            continue
        if msg.stop_reason == "tool_use":
            results = [_run_tool(b, reader) for b in msg.content if getattr(b, "type", "") == "tool_use"]
            usage["read_source_calls"] += len(results)
            messages = messages + [{"role": "assistant", "content": msg.content},
                                   {"role": "user", "content": results}]
            continue
        if msg.stop_reason == "max_tokens":
            raise RuntimeError("response hit max_tokens; raise the limit")
        text = "".join(b.text for b in msg.content if b.type == "text")
        if fetched:
            usage["fetched_urls"] = sorted(set(fetched))
        usage["served_by"] = sorted(served_by)
        usage["cost_usd"] = stage_cost(usage)
        return text, usage
    raise RuntimeError(f"{stage}: too many tool rounds")


def _add_usage(usage: dict, u, requested: str, served: str) -> None:
    """Add one API response's usage, bucketed by the model that ran each attempt.

    With server-side fallback, top-level usage covers only the attempt that produced
    the message; usage.iterations is the per-attempt record. So: iterations when
    present, otherwise top-level, never both. input_tokens excludes cache reads and
    writes (total input = input + cache writes + cache reads), so nothing overlaps."""
    usage["requests"] += 1
    attempts = list(getattr(u, "iterations", None) or [])
    if attempts:
        for it in attempts:
            model = getattr(it, "model", None) or (requested if getattr(it, "type", "") == "message" else served)
            _add_tokens(usage, model, it)
    else:
        _add_tokens(usage, served, u)
    stu = getattr(u, "server_tool_use", None)
    usage["web_search_requests"] += getattr(stu, "web_search_requests", 0) or 0
    usage["web_fetch_requests"] += getattr(stu, "web_fetch_requests", 0) or 0


def _add_tokens(usage: dict, model: str, src) -> None:
    writes = getattr(src, "cache_creation_input_tokens", 0) or 0
    split = getattr(src, "cache_creation", None)
    w1h = min(writes, getattr(split, "ephemeral_1h_input_tokens", 0) or 0) if split else 0
    t = {"input_tokens": getattr(src, "input_tokens", 0) or 0,
         "cache_write_5m_tokens": writes - w1h,  # without a breakdown, writes are 5-minute (our TTL)
         "cache_write_1h_tokens": w1h,
         "cache_read_input_tokens": getattr(src, "cache_read_input_tokens", 0) or 0,
         "output_tokens": getattr(src, "output_tokens", 0) or 0}
    bucket = usage.setdefault("by_model", {}).setdefault(model, {k: 0 for k in TOKEN_KEYS})
    for k, v in t.items():
        bucket[k] += v
    usage["input_tokens"] += t["input_tokens"]
    usage["cache_creation_input_tokens"] += writes
    usage["cache_read_input_tokens"] += t["cache_read_input_tokens"]
    usage["output_tokens"] += t["output_tokens"]


def _run_tool(block, reader: sources.SourceReader | None) -> dict:
    inp = block.input if isinstance(block.input, dict) else {}
    if block.name == "read_source" and reader is not None and isinstance(inp.get("url"), str):
        res = reader.read(inp["url"], str(inp.get("focus", "")))
        return {"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(res, ensure_ascii=False)}
    return {"type": "tool_result", "tool_use_id": block.id, "is_error": True,
            "content": f"tool {block.name!r} is not available or got invalid input"}


def _fetched_urls(content) -> list[str]:
    """URLs that web_fetch actually retrieved (errors excluded), from Anthropic's network."""
    out = []
    for b in content:
        if getattr(b, "type", "") == "web_fetch_tool_result":
            res = getattr(b, "content", None)
            if getattr(res, "type", "") == "web_fetch_result" and getattr(res, "url", None):
                out.append(res.url)
    return out


def _tools(cfg: dict, searches: int, reader: sources.SourceReader | None) -> list:
    """read_source first; server-side web_fetch stays as a capped fallback for pages
    the runner cannot read (some sites block GitHub's IP ranges)."""
    m = cfg["models"]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": searches},
             {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": m["max_web_fetches"],
              "max_content_tokens": m["max_fetch_tokens"]}]
    return ([sources.READ_SOURCE_TOOL] if reader is not None else []) + tools


READING_RULES = """How to read sources:
- Read pages with read_source. It returns the passages relevant to your focus, with headings,
  neighbouring text and dates. If an excerpt is partial and a fact could depend on text outside
  it (an exception, a date, a scope limit), call read_source again with a narrower focus.
- Use web_fetch only for a URL that read_source reports as unavailable or too_short.
- Never treat a page you could not read as evidence.
"""


def research_brief(cfg: dict, pillar: str, candidates: list[str], existing_titles: list[str],
                   demand_notes: str, reader: sources.SourceReader | None = None) -> tuple[str, dict]:
    p = cfg["pillars"][pillar]
    product = cfg["products"].get(p["product"]) if p["product"] else None
    min_sources = cfg["quality"]["min_sources"]
    prompt = f"""You are planning ONE article for New Earns.

Pillar: {pillar}
Candidate topics (pick one, or a closely related query with clearer demand):
{json.dumps(candidates, indent=1)}

Search-demand data we have (may be empty; never invent volumes):
{demand_notes or "none available"}

Already published on New Earns (do not overlap with these intents):
{json.dumps(existing_titles, indent=1)}

{"Relevant product (mention only if it genuinely helps): " + json.dumps(product) if product else "No product for this pillar."}

Do this:
1. Search the web for the strongest candidate query. Look at what currently ranks
   to determine the real search intent (how-to, comparison, definition...).
2. Pick the topic whose intent New Earns can satisfy better than current results,
   and explain the specific added value (clearer steps, a missing scenario,
   a decision table, troubleshooting...).
3. Read 3-6 authoritative sources (official docs first) from at least
   {min_sources} different websites, and note the exact facts you will rely on,
   each with its URL and the source's own wording for legal or policy points.
4. Produce a brief with: chosen query, focus keyphrase, search intent,
   unique angle, H2/H3 outline, facts with URLs, internal-link ideas from the
   already-published list (only if genuinely related), and whether the product
   link is justified and at which step.
5. End the brief with SOURCE NOTES: for each source, its URL, title, the date the
   page states (if any), and the exact sentences that support each fact, including
   any exception or condition attached to them. The writer works only from this
   brief, so it must be complete but should not copy whole pages.

{READING_RULES}"""
    return _call(cfg, [{"role": "user", "content": prompt}], system=EDITORIAL_RULES, stage="research",
                 tools=_tools(cfg, cfg["models"]["max_web_searches"], reader), reader=reader,
                 max_tokens=24000)


def write_article(cfg: dict, pillar: str, brief: str, internal_posts: list[dict]) -> tuple[dict, dict]:
    p = cfg["pillars"][pillar]
    product = cfg["products"].get(p["product"]) if p["product"] else None
    disclosure = cfg["product_linking"]["disclosure"].format(product=product["name"]) if product else ""
    min_sources = cfg["quality"]["min_sources"]
    banned = ", ".join(f'"{b}"' for b in cfg["quality"]["banned_phrases"])
    prompt = f"""Write the article from this research brief.

BRIEF
{brief}

Internal New Earns posts you may link to (exact URLs only, only if relevant):
{json.dumps([{"title": x["title"], "url": x["link"]} for x in internal_posts], indent=1)}

Product details (allowed claims only): {json.dumps(product) if product else "none"}
If you link the product: append this query string to its URL:
?{cfg["product_linking"]["utm"].format(slug="<slug>")}  (replace <slug> with your slug)
and add this sentence in an <p><em> block right after the first product mention:
"{disclosure}"
If no product link is justified, leave product_link_reason empty and add no product link.

Output rules:
- content_html: WordPress block-compatible HTML using <h2>, <h3>, <p>, <ul>/<ol>, <table>,
  <a href>. No <h1>, no inline styles, no scripts, no images.
- {cfg["quality"]["min_words"]}-{cfg["quality"]["max_words"] - 400} words. Practical, specific, with
  concrete examples and steps; end with a short FAQ (<h2>FAQ</h2> + <h3> questions) built from real
  questions found in research.
- External links: https only, from the brief's sources; every statistic linked in the same paragraph.
- seo_title <= 60 characters; meta_description 120-160 characters; slug lowercase-hyphenated,
  2-10 words, no dates.
- sources: every external source you linked, with what it supports.
- image_headline: 3-7 words for the featured image card.
- Sources: link sources from at least {min_sources} different websites (the brief's sources).
- State every sourced claim no more strongly than the source does. For legal or policy
  points, quote or closely paraphrase the source; do not generalise it to other situations.
- Do not use these words anywhere in the article, including in quoted titles or link text:
  {banned}
"""
    text, usage = _call(cfg, [{"role": "user", "content": prompt}], system=EDITORIAL_RULES, stage="write",
                        schema=ARTICLE_SCHEMA, max_tokens=40000)
    art = json.loads(text)
    art["content_html"] = art["content_html"].replace("<slug>", art["slug"])
    return art, usage


def evidence_pack(article: dict, reader: sources.SourceReader) -> list[dict]:
    """For each cited source: the passages relevant to the article text that links to it.
    Built by our own fetcher from the live page, not by the writer."""
    doc = quality.parse(article["content_html"])
    pack = []
    for s in article.get("sources", []):
        key = sources.normalize_url(s["url"])
        citing = [b for b, links in doc.blocks if any(sources.normalize_url(l) == key for l in links)]
        focus = " ".join([s.get("supports", "")] + citing)[:2000]
        pack.append(reader.read(s["url"], focus))
    return pack


def fact_check(cfg: dict, article: dict, pillar: str,
               reader: sources.SourceReader | None = None) -> tuple[dict, list[dict]]:
    p = cfg["pillars"][pillar]
    product = cfg["products"].get(p["product"]) if p["product"] else None
    source_urls = "\n".join(s["url"] for s in article["sources"])
    if reader is not None:
        evidence = ("EVIDENCE EXTRACTED FROM THE CITED PAGES (fetched by the pipeline from the live pages, "
                    "not by the writer; check it, do not trust it blindly)\n"
                    + json.dumps(evidence_pack(article, reader), ensure_ascii=False, indent=1))
        how = ("Use the evidence above first. Call read_source again with a narrower focus when an excerpt "
               "is partial and the claim could depend on missing text, and web_fetch for any source marked "
               "unavailable or too_short. A claim is supported only if text you actually read states it.")
    else:
        evidence, how = "", "Fetch the cited sources."
    step1 = f"""You are an independent fact-checker. You did not write this article.

Source URLs:
{source_urls}

{evidence}

ARTICLE HTML
{article["content_html"]}

Allowed product claims: {json.dumps(product["allowed_claims"]) if product else "no product"}

1. List every checkable factual claim (numbers, feature descriptions, steps in
   named tools, legal/policy statements).
2. {how} Where needed, search for official documentation to verify a claim.
   Mark each claim supported / unsupported / contradicted / uncertain with the
   evidence URL. If a page was cut off or you could not read the relevant part,
   mark the claim uncertain rather than inferring.
3. Judge whether the article satisfies the search intent "{article["search_intent"]}"
   better than a generic answer and whether it adds original value
   (claimed: "{article["unique_value"]}"). Be strict: generic rewrites fail.
4. Check that every product statement matches the allowed claims exactly in substance.

{READING_RULES if reader is not None else ""}"""
    notes, u1 = _call(cfg, [{"role": "user", "content": step1}], system="Be rigorous and skeptical.",
                      stage="fact_check", tools=_tools(cfg, cfg["models"]["factcheck_web_searches"], reader),
                      reader=reader, max_tokens=24000)
    text, u2 = _call(cfg, [{"role": "user", "content": "Convert this fact-check into the JSON schema. "
                            "Do not change any verdicts.\n\n" + notes}],
                     system="Faithful conversion only.", stage="fact_check_json", schema=FACTCHECK_SCHEMA,
                     max_tokens=16000)
    fc = json.loads(text)
    verified = reader.verified_urls() if reader is not None else []
    fc["fetched_urls"] = sorted(set(u1.get("fetched_urls", [])) | set(verified))
    return fc, [u1, u2]


def _tokens_usd(model: str, t: dict) -> float:
    p_in, p_w5, p_w1, p_read, p_out = PRICES.get(model, UNKNOWN_PRICE)
    return (t.get("input_tokens", 0) * p_in + t.get("cache_write_5m_tokens", 0) * p_w5
            + t.get("cache_write_1h_tokens", 0) * p_w1 + t.get("cache_read_input_tokens", 0) * p_read
            + t.get("output_tokens", 0) * p_out) / 1e6


def _buckets(u: dict) -> dict:
    """Per-model token buckets; usage saved before buckets existed is one bucket of 5-minute writes."""
    if "by_model" in u:
        return u["by_model"]
    return {u.get("model") or "claude-opus-5-5": {
        "input_tokens": u.get("input_tokens", 0), "cache_write_5m_tokens": u.get("cache_creation_input_tokens", 0),
        "cache_write_1h_tokens": 0, "cache_read_input_tokens": u.get("cache_read_input_tokens", 0),
        "output_tokens": u.get("output_tokens", 0)}}


def stage_cost(u: dict) -> float:
    """Best-available estimate for one stage at list prices, rounded to 0.1 cent."""
    usd = sum(_tokens_usd(m, t) for m, t in _buckets(u).items())
    return round(usd + u.get("web_search_requests", 0) * SEARCH_USD, 3)


def estimate_cost(usages: list[dict]) -> float:
    """Total = sum of the rounded stage costs, so it always matches the table."""
    return round(sum(stage_cost(u) for u in usages), 3)


def unpriced_models(usages: list[dict]) -> list[str]:
    return sorted({m for u in usages for m in _buckets(u) if m not in PRICES})


def cost_table(usages: list[dict]) -> list[str]:
    rows = ["stage | requests | input | cache write | cache read | output | searches | est. USD"]
    for n, u in enumerate(usages, 1):
        rows.append(f"{u.get('stage', f'stage {n}')} | {u.get('requests', '-')} | {u.get('input_tokens', 0):,} | "
                    f"{u.get('cache_creation_input_tokens', 0):,} | {u.get('cache_read_input_tokens', 0):,} | "
                    f"{u.get('output_tokens', 0):,} | {u.get('web_search_requests', 0)} | {stage_cost(u):.3f}")
    rows.append(f"total | | | | | | | {estimate_cost(usages):.3f}")
    if unpriced_models(usages):
        rows.append(f"note: no list price on file for {', '.join(unpriced_models(usages))}; "
                    "estimated at the highest known rates")
    return rows


def api_key_present() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def api_key_problem() -> str | None:
    """Catches common paste mistakes without ever printing the key."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return "missing"
    if key != key.strip() or any(c.isspace() for c in key):
        return "contains spaces or line breaks (paste only the key itself, e.g. sk-ant-...)"
    if not key.startswith("sk-ant-"):
        return "does not look like an API key (it should start with sk-ant-)"
    return None
