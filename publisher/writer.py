"""Claude stages: research brief -> article -> independent fact-check.

Each stage is a separate request so the fact-checker does not grade its own
draft from memory: it re-opens the cited sources with web_fetch.
"""
from __future__ import annotations

import json
import os

import anthropic

BETAS = ["server-side-fallback-2026-07-01"]

ARTICLE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "seo_title", "slug", "meta_description", "focus_keyphrase", "excerpt",
                 "content_html", "sources", "product_link_reason", "image_alt_text", "image_headline",
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
        "image_alt_text": {"type": "string"},
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


def _call(cfg: dict, messages: list, *, system: str, tools: list | None = None,
          schema: dict | None = None, max_tokens: int = 32000):
    """One streamed request; resumes server-tool pause_turn; raises on refusal."""
    client = _client()
    kwargs = dict(model=cfg["models"]["writer"], max_tokens=max_tokens, system=system,
                  betas=BETAS, fallbacks="default",
                  output_config={"effort": cfg["models"]["effort"]})
    if tools:
        kwargs["tools"] = tools
    if schema:
        kwargs["output_config"]["format"] = {"type": "json_schema", "schema": schema}
    usage = {"input_tokens": 0, "output_tokens": 0, "web_search_requests": 0}
    for _ in range(6):
        with client.beta.messages.stream(messages=messages, **kwargs) as stream:
            msg = stream.get_final_message()
        usage["input_tokens"] += msg.usage.input_tokens
        usage["output_tokens"] += msg.usage.output_tokens
        stu = getattr(msg.usage, "server_tool_use", None)
        usage["web_search_requests"] += getattr(stu, "web_search_requests", 0) or 0
        if msg.stop_reason == "refusal":
            raise Refused(str(getattr(msg, "stop_details", "")))
        if msg.stop_reason == "pause_turn":
            messages = messages + [{"role": "assistant", "content": msg.content}]
            continue
        if msg.stop_reason == "max_tokens":
            raise RuntimeError("response hit max_tokens; raise the limit")
        text = "".join(b.text for b in msg.content if b.type == "text")
        return text, usage
    raise RuntimeError("too many pause_turn continuations")


def _web_tools(cfg: dict) -> list:
    return [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": cfg["models"]["max_web_searches"]},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": cfg["models"]["max_web_fetches"]},
    ]


def research_brief(cfg: dict, pillar: str, candidates: list[str], existing_titles: list[str],
                   demand_notes: str) -> tuple[str, dict]:
    p = cfg["pillars"][pillar]
    product = cfg["products"].get(p["product"]) if p["product"] else None
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
3. Fetch and read 3-6 authoritative sources (official docs first) and note the
   exact facts you will rely on, each with its URL.
4. Produce a brief with: chosen query, focus keyphrase, search intent,
   unique angle, H2/H3 outline, facts with URLs, internal-link ideas from the
   already-published list (only if genuinely related), and whether the product
   link is justified and at which step.
"""
    return _call(cfg, [{"role": "user", "content": prompt}], system=EDITORIAL_RULES,
                 tools=_web_tools(cfg), max_tokens=24000)


def write_article(cfg: dict, pillar: str, brief: str, internal_posts: list[dict]) -> tuple[dict, dict]:
    p = cfg["pillars"][pillar]
    product = cfg["products"].get(p["product"]) if p["product"] else None
    disclosure = cfg["product_linking"]["disclosure"].format(product=product["name"]) if product else ""
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
- image_headline: 3-7 words for the featured image card. image_alt_text: describes that image.
"""
    text, usage = _call(cfg, [{"role": "user", "content": prompt}], system=EDITORIAL_RULES,
                        schema=ARTICLE_SCHEMA, max_tokens=40000)
    art = json.loads(text)
    art["content_html"] = art["content_html"].replace("<slug>", art["slug"])
    return art, usage


def fact_check(cfg: dict, article: dict, pillar: str) -> tuple[dict, dict]:
    p = cfg["pillars"][pillar]
    product = cfg["products"].get(p["product"]) if p["product"] else None
    source_urls = "\n".join(s["url"] for s in article["sources"])
    step1 = f"""You are an independent fact-checker. You did not write this article.

Source URLs (fetch them):
{source_urls}

ARTICLE HTML
{article["content_html"]}

Allowed product claims: {json.dumps(product["allowed_claims"]) if product else "no product"}

1. List every checkable factual claim (numbers, feature descriptions, steps in
   named tools, legal/policy statements).
2. Fetch the cited sources and, where needed, search for official documentation
   to verify each claim. Mark each claim supported / unsupported / contradicted /
   uncertain with the evidence URL.
3. Judge whether the article satisfies the search intent "{article["search_intent"]}"
   better than a generic answer and whether it adds original value
   (claimed: "{article["unique_value"]}"). Be strict: generic rewrites fail.
4. Check that every product statement matches the allowed claims exactly in substance.
"""
    notes, u1 = _call(cfg, [{"role": "user", "content": step1}], system="Be rigorous and skeptical.",
                      tools=_web_tools(cfg), max_tokens=24000)
    text, u2 = _call(cfg, [{"role": "user", "content": "Convert this fact-check into the JSON schema. "
                            "Do not change any verdicts.\n\n" + notes}],
                     system="Faithful conversion only.", schema=FACTCHECK_SCHEMA, max_tokens=16000)
    usage = {k: u1.get(k, 0) + u2.get(k, 0) for k in u1}
    return json.loads(text), usage


def estimate_cost(usages: list[dict]) -> float:
    # Claude Opus 5.5 list prices, USD (2026-10): $4/MTok in, $20/MTok out, $10 per 1k searches.
    i = sum(u.get("input_tokens", 0) for u in usages)
    o = sum(u.get("output_tokens", 0) for u in usages)
    s = sum(u.get("web_search_requests", 0) for u in usages)
    return round(i / 1e6 * 4 + o / 1e6 * 20 + s / 1000 * 10, 3)


def api_key_present() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))
