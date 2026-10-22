"""Deterministic quality gates. An article publishes only if every check passes.

Each check returns (name, passed, detail). `hard` failures block publishing;
there are no soft passes: anything failing goes to the review queue. Every
failure in the report comes with a practical correction (FIXES below).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from html.parser import HTMLParser
from urllib.parse import urlparse

import requests

from .sources import normalize_url

IMAGE_LICENSES = ("generated-in-house", "pexels-license", "own-screenshot", "cc0", "public-domain", "cc-by",
                  "cc-by-sa")
WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
STAT_HINT = re.compile(r"\d+(?:\.\d+)?\s?%|\b(according to|study|survey|research (shows|found)|report(s|ed)? that|statistic)", re.I)


# ---------------------------------------------------------------- parsing
class _Doc(HTMLParser):
    BLOCKS = {"p", "li", "td", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text_parts, self.headings, self.links, self.blocks = [], [], [], []
        self._tag_stack, self._buf, self._block_links = [], [], []
        self.images = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self._tag_stack.append(tag)
        if tag == "a" and a.get("href"):
            self.links.append({"href": a["href"], "rel": a.get("rel", "")})
            self._block_links.append(a["href"])
        if tag == "img":
            self.images.append(a)
        if tag in self.BLOCKS or re.fullmatch(r"h[1-6]", tag):
            self._buf, self._block_links = [], []

    def handle_endtag(self, tag):
        if re.fullmatch(r"h[1-6]", tag):
            self.headings.append((tag, "".join(self._buf).strip()))
        elif tag in self.BLOCKS:
            self.blocks.append(("".join(self._buf).strip(), list(self._block_links)))
        if self._tag_stack and self._tag_stack[-1] == tag:
            self._tag_stack.pop()

    def handle_data(self, data):
        self.text_parts.append(data)
        self._buf.append(data)

    @property
    def text(self) -> str:
        return " ".join(self.text_parts)


def parse(html: str) -> _Doc:
    d = _Doc()
    d.feed(html)
    return d


def words(text: str) -> list[str]:
    return WORD.findall(text.lower())


def shingles(text: str, n: int = 5) -> set[tuple]:
    w = words(text)
    return {tuple(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def title_similarity(a: str, b: str) -> float:
    na, nb = " ".join(sorted(set(words(a)))), " ".join(sorted(set(words(b))))
    return SequenceMatcher(None, na, nb).ratio()


# ---------------------------------------------------------------- checks
# What to do about each failure. Shown next to the failure in the run output and saved
# with the checks, so a held article can be corrected by hand and revalidated.
FIXES = {
    "word_count": "Expand thin sections with specific, sourced steps, or cut repetition, to land inside the word range.",
    "structure_h2": "Split the body into at least 4 H2 sections, each answering one part of the query.",
    "no_h1_in_body": "Change body <h1> tags to <h2>; the theme already prints the title as the H1.",
    "headings_unique": "Rename duplicate H2s so each one describes its own section.",
    "keyphrase_in_title": "Use every word of the focus keyphrase in the title, or pick a keyphrase that matches the title.",
    "keyphrase_early": "Use the exact focus keyphrase once in the first 150 words.",
    "keyword_density": "Replace some repeats of the keyphrase with natural wording.",
    "headings_not_stuffed": "Rewrite headings to describe their section instead of repeating the keyphrase.",
    "meta_description_length": "Rewrite the meta description to fit the configured length (120-160 characters).",
    "seo_title_length": "Shorten the SEO title to the configured maximum (60 characters).",
    "slug_format": "Use 2-10 lowercase words joined by hyphens, no dates, at most 75 characters.",
    "slug_unused": "Choose another slug; an existing post is never overwritten. Expected when re-checking an update "
                   "to an existing post.",
    "not_near_duplicate": "Rewrite in new wording, or improve the existing post instead of publishing a near copy.",
    "distinct_search_intent": "Choose a different angle, or improve the existing post that already covers this intent.",
    "keyphrase_not_targeted_before": "Choose another focus keyphrase, or improve the post that already targets it.",
    "no_fabricated_experience_or_hype": "Remove the flagged wording; describe steps editorially, without hype or "
                                        "first-hand claims.",
    "https_only": "Replace http:// links with their https:// version, or remove them.",
    "no_broken_links": "Replace each broken link with a working official page that states the same fact, or remove "
                       "the claim it supported.",
    "internal_links_valid": "Link only to published New Earns posts; remove any made-up, draft or planned URL.",
    "internal_links_present": "Link a genuinely related published New Earns post. If none is relevant, add no link and "
                              "say why in internal_links_note; never invent one.",
    "product_links_limited": "Remove product links beyond the configured maximum.",
    "product_links_tracked": "Add the configured UTM query string to each product link.",
    "ownership_disclosed": "Add the ownership disclosure sentence right after the first product mention.",
    "product_link_justified": "Say in product_link_reason which reader step the product solves, or remove the link.",
    "enough_independent_sources": "Cite official or primary sources from more different websites.",
    "sources_cited_in_body": "Link each listed source in the paragraph that relies on it, or drop it from the list.",
    "statistics_have_sources": "Link the source inside the same paragraph or list item as the number or "
                               "'according to' statement, or remove the statement.",
    "sources_verified": "Replace each unreadable or irrelevant source with a page that states the fact, or remove the "
                        "claims that relied on it; then re-run the fact-check only.",
    "featured_image_alt": "Write alt text of 10-125 characters describing the image.",
    "image_rights": "Use only an in-house, Pexels-licensed, CC0/public-domain or credited CC BY / BY-SA image, or your own.",
    "inline_images_have_alt": "Give every inline image alt text.",
    "fact_check_claims": "Contradicted: correct the claim to what the source says. No evidence found: cite an official "
                         "source that states it, narrow it to what a source does say, or remove it. Could not be "
                         "checked: verify it against a readable source or remove it. Source page not read: cite a page "
                         "the pipeline can open that states the claim, or remove the claim. No claims listed: the "
                         "fact-check did not check anything. Then re-run the fact-check only.",
    "fact_check_intent": "Rework the article so it answers the query more completely than a generic result.",
    "fact_check_original_value": "Add something current results lack (decision table, troubleshooting, a missing "
                                 "case), sourced.",
    "fact_check_product_claims": "Rewrite product statements to match allowed_claims in config.yaml, or remove them.",
    "fact_check_current": "The fact-check is not tied to this exact text. Re-run the fact-check only (recheck with "
                          "refresh_fact_check); no new article is generated.",
    "fact_check_ran": "Run the fact-check stage before this article can be published.",
}

VERDICT_LABELS = {
    "contradicted": "contradicted by a source (likely wrong)",
    "unsupported": "no supporting evidence found (not shown to be false)",
    "uncertain": "could not be checked (source unreadable or partial)",
    "source_not_read": "marked supported, but its source_url is missing or was not read by the pipeline",
}


def content_fingerprint(html: str) -> str:
    """Identifies the exact article text a fact-check looked at."""
    return hashlib.sha256(html.encode("utf-8")).hexdigest()


def _claims_detail(bad: list[dict]) -> str:
    parts = []
    for verdict in [*VERDICT_LABELS, *sorted({c.get("verdict", "") for c in bad} - set(VERDICT_LABELS))]:
        group = [c for c in bad if c.get("verdict", "") == verdict]
        if group:
            items = [c["claim"][:90] + (f" (fix: {c['suggested_fix'][:120]})" if c.get("suggested_fix") else "")
                     for c in group[:4]]
            parts.append(f"{len(group)} {VERDICT_LABELS.get(verdict, verdict or 'no verdict')}: " + " | ".join(items))
    return " || ".join(parts)


def _page_key(url: str) -> str:
    """newearns.com and www.newearns.com, with or without query, fragment or trailing slash, are one page."""
    u = urlparse(url)
    host = u.netloc.lower()
    return (host[4:] if host.startswith("www.") else host) + (u.path.rstrip("/") or "/")


@dataclass
class Report:
    results: list[tuple[str, bool, str]] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = ""):
        self.results.append((name, bool(ok), detail))

    @property
    def passed(self) -> bool:
        return all(ok for _, ok, _ in self.results)

    @property
    def failures(self) -> list[str]:
        return [f"{n}: {d}" for n, ok, d in self.results if not ok]

    @property
    def explained(self) -> list[str]:
        """Each failure with its practical correction."""
        return [f"{n}: {d}\n    Fix: {FIXES.get(n, 'see the detail above')}" for n, ok, d in self.results if not ok]

    def as_dict(self):
        return [{"check": n, "passed": ok, "detail": d, **({} if ok else {"fix": FIXES.get(n, "")})}
                for n, ok, d in self.results]


UAS = ["Mozilla/5.0 (compatible; newearns-linkcheck/1.0)",
       "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"]


def check_link(url: str, timeout: int = 20) -> tuple[bool, str]:
    """HEAD then GET, with two user agents; sites differ in which ones they reject."""
    code = "error"
    for ua in UAS:
        headers = {"User-Agent": ua, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
        try:
            r = requests.head(url, allow_redirects=True, timeout=timeout, headers=headers)
            if r.status_code >= 400:  # HEAD is unreliable; only a failed GET counts
                r = requests.get(url, allow_redirects=True, timeout=timeout, headers=headers, stream=True)
            if r.status_code < 400:
                return True, str(r.status_code)
            code = str(r.status_code)
        except requests.RequestException as e:
            code = type(e).__name__
    return False, code


def run_checks(article: dict, cfg: dict, existing_posts: list[dict], known_slugs: set[str],
               link_checker=check_link, fact_check: dict | None = None) -> Report:
    q, rep = cfg["quality"], Report()
    site = urlparse(cfg["site"]["base_url"]).netloc
    doc = parse(article["content_html"])
    text = doc.text
    w = words(text)
    kp = article["focus_keyphrase"].lower().strip()

    # 1. length (thin / bloated content)
    rep.add("word_count", q["min_words"] <= len(w) <= q["max_words"], f"{len(w)} words")

    # 2. structure
    h2 = [h for t, h in doc.headings if t == "h2"]
    rep.add("structure_h2", len(h2) >= 4, f"{len(h2)} H2 sections")
    rep.add("no_h1_in_body", not any(t == "h1" for t, _ in doc.headings), "theme renders the H1")
    rep.add("headings_unique", len(set(map(str.lower, h2))) == len(h2), "duplicate H2 text")

    # 3. keyphrase use, stuffing
    kp_words = words(kp)
    occurrences = len(re.findall(r"\b" + r"\s+".join(map(re.escape, kp_words)) + r"\b", " ".join(w)))
    density = occurrences * len(kp_words) / max(1, len(w))
    rep.add("keyphrase_in_title", all(x in words(article["title"]) for x in kp_words), article["title"])
    rep.add("keyphrase_early", any(x in " ".join(w[:150]) for x in [" ".join(kp_words)]), "first 150 words")
    rep.add("keyword_density", density <= q["max_keyword_density"], f"{density:.2%} ({occurrences}x)")
    stuffed_h = sum(" ".join(kp_words) in " ".join(words(h)) for h in h2)
    rep.add("headings_not_stuffed", stuffed_h <= max(1, len(h2) // 3), f"{stuffed_h}/{len(h2)} H2 repeat keyphrase")

    # 4. metadata
    lo, hi = q["meta_description_length"]
    md = article["meta_description"]
    rep.add("meta_description_length", lo <= len(md) <= hi, f"{len(md)} chars")
    rep.add("seo_title_length", len(article["seo_title"]) <= q["seo_title_max_length"], f"{len(article['seo_title'])} chars")
    slug = article["slug"]
    rep.add("slug_format", bool(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+){1,9}", slug)) and len(slug) <= 75, slug)

    # 5. duplication / cannibalisation
    rep.add("slug_unused", slug not in known_slugs, slug)
    sh = shingles(text)
    worst_body = max((jaccard(sh, shingles(p.get("text", ""))) for p in existing_posts), default=0.0)
    rep.add("not_near_duplicate", worst_body <= q["max_duplicate_similarity"], f"max 5-gram overlap {worst_body:.2f}")
    worst_title, worst_t = 0.0, ""
    for p in existing_posts:
        s = title_similarity(article["title"], p.get("title", ""))
        if s > worst_title:
            worst_title, worst_t = s, p.get("title", "")
    rep.add("distinct_search_intent", worst_title <= q["max_title_similarity"],
            f"closest existing title {worst_title:.2f}: {worst_t}")
    kp_clash = [p["title"] for p in existing_posts if p.get("focus_keyphrase", "").lower() == kp]
    rep.add("keyphrase_not_targeted_before", not kp_clash, "; ".join(kp_clash))

    # 6. banned phrases (fabricated experience, testimonials, hype)
    low = text.lower()
    hits = [b for b in q["banned_phrases"] if b in low]
    rep.add("no_fabricated_experience_or_hype", not hits, ", ".join(hits))

    # 7. links
    hrefs = [l["href"] for l in doc.links]
    rep.add("https_only", all(h.startswith("https://") or h.startswith("#") for h in hrefs) or not q["require_https_links"],
            ", ".join(h for h in hrefs if not h.startswith(("https://", "#"))))
    # Some sites (ftc.gov) refuse requests from GitHub's datacenter IPs. A link that
    # failed here still counts as working if the fact-check's web_fetch retrieved it.
    fetched_ok = {u.rstrip("/") for u in (fact_check or {}).get("fetched_urls", [])}
    broken, via_fetch = [], []
    for h in sorted({h for h in hrefs if h.startswith("http")}):
        ok, code = link_checker(h)
        if not ok and h.rstrip("/") in fetched_ok:
            via_fetch.append(h)
        elif not ok:
            broken.append(f"{h} ({code})")
    rep.add("no_broken_links", not broken,
            "; ".join(broken) or (f"{len(via_fetch)} confirmed by fact-check fetch" if via_fetch else ""))

    # internal links must point at real, published New Earns posts (they are also link-checked
    # above). Product sites are not internal links. When no published post is relevant, the
    # article says so in internal_links_note instead of forcing or inventing a link.
    live = {_page_key(p["link"]) for p in existing_posts if p.get("link")}
    bare = site[4:] if site.startswith("www.") else site
    internal = [h for h in hrefs if urlparse(h).netloc.lower() in (bare, "www." + bare)]
    bad_internal = [h for h in internal if _page_key(h) not in live]
    rep.add("internal_links_valid", not bad_internal, "; ".join(bad_internal))
    note = (article.get("internal_links_note") or "").strip()
    if internal:
        rep.add("internal_links_present", True, f"{len(internal)} internal links")
    elif not live:
        rep.add("internal_links_present", True, "0 internal links; no published posts to link to")
    else:
        rep.add("internal_links_present", bool(note),
                f"0 internal links; no relevant published post: {note}" if note
                else "0 internal links and no internal_links_note saying why none is relevant")

    # 8. product links: few, tracked, disclosed, only real products
    products = cfg["products"]
    prod_hosts = {urlparse(p["url"]).netloc: k for k, p in products.items()}
    prod_links = [h for h in hrefs if urlparse(h).netloc in prod_hosts]
    rep.add("product_links_limited", len(prod_links) <= cfg["product_linking"]["max_product_links_per_article"],
            f"{len(prod_links)} product links")
    rep.add("product_links_tracked", all("utm_source=newearns" in h for h in prod_links), "missing UTM")
    if prod_links:
        rep.add("ownership_disclosed", "built by the team behind new earns" in low, "disclosure missing")
        rep.add("product_link_justified", bool(article.get("product_link_reason", "").strip()), "no reason given")

    # 9. sources: statistics and research claims must be linked in the same block
    ext_sources = {urlparse(s["url"]).netloc for s in article.get("sources", [])
                   if urlparse(s["url"]).netloc not in (site, *prod_hosts)}
    rep.add("enough_independent_sources", len(ext_sources) >= q["min_sources"], f"{len(ext_sources)} domains")
    cited_in_body = [s["url"] for s in article.get("sources", []) if s["url"] in hrefs]
    rep.add("sources_cited_in_body", len(cited_in_body) >= min(q["min_sources"], len(article.get("sources", []))),
            f"{len(cited_in_body)} of {len(article.get('sources', []))} sources linked in text")
    unsourced = [b[:90] for b, links in doc.blocks
                 if STAT_HINT.search(b) and not any(urlparse(l).netloc not in (site, *prod_hosts, "") for l in links)]
    rep.add("statistics_have_sources", not unsourced, " | ".join(unsourced[:3]))

    # 10. images
    img = article.get("featured_image") or {}
    alt = img.get("alt_text", "")
    rep.add("featured_image_alt", 10 <= len(alt) <= 125, f"{len(alt)} chars")
    # Every image: a licence recorded from its source; CC BY / BY-SA also need the creator and licence link we credit.
    planned = article.get("inline_images") or []
    bad = [i.get("license", "none") for i in [img, *planned]
           if i.get("license") not in IMAGE_LICENSES
           or (i.get("license") in ("cc-by", "cc-by-sa") and not (i.get("creator") and i.get("license_url")))]
    rep.add("image_rights", not bad, ", ".join(bad) or img.get("license", "none"))
    rep.add("inline_images_have_alt", all(i.get("alt") for i in doc.images)
            and all(10 <= len(i.get("alt_text", "")) <= 125 for i in planned),
            f"{len(doc.images) + len(planned)} inline images")

    # 11. independent fact-check pass (LLM, run before these checks)
    if fact_check is not None:
        claims = fact_check.get("claims") or []
        # "supported" counts only with evidence from a page the pipeline itself read; a search
        # snippet, an unreadable page or a URL nobody fetched is not evidence.
        read = {normalize_url(u) for u in fact_check.get("fetched_urls", [])}
        bad = [c if c.get("verdict") != "supported" else dict(c, verdict="source_not_read")
               for c in claims
               if c.get("verdict") != "supported" or normalize_url(c.get("source_url") or "") not in read]
        rep.add("fact_check_claims", bool(claims) and not bad,
                _claims_detail(bad) if claims else "fact-check listed no claims")
        rep.add("fact_check_intent", fact_check.get("satisfies_search_intent") is True, fact_check.get("intent_notes", ""))
        rep.add("fact_check_original_value", fact_check.get("adds_original_value") is True, fact_check.get("value_notes", ""))
        rep.add("fact_check_product_claims", fact_check.get("product_claims_accurate") is True,
                fact_check.get("product_notes", ""))
        # The verdicts above only count for the exact text that was checked.
        fp = fact_check.get("content_sha256")
        rep.add("fact_check_current", fp == content_fingerprint(article["content_html"]),
                "" if fp == content_fingerprint(article["content_html"]) else
                "article text changed after the fact-check" if fp else
                "fact-check has no content fingerprint, so it may not match this text")
        # Every cited source must have been read by the pipeline and judged relevant to what it
        # is cited for. A source that could not be read verifies nothing.
        fetched = {normalize_url(u) for u in fact_check.get("fetched_urls", [])}
        reviewed = {normalize_url(s.get("url", "")): s for s in fact_check.get("sources") or []}
        problems = []
        for s in article.get("sources", []):
            if urlparse(s["url"]).netloc in (site, *prod_hosts):
                continue
            key, r = normalize_url(s["url"]), reviewed.get(normalize_url(s["url"]))
            if key not in fetched:
                problems.append(f"{s['url']} could not be read, so nothing it is cited for is verified")
            elif r is None:
                problems.append(f"{s['url']} was not reviewed by the fact-check")
            elif r.get("relevant") is not True:
                problems.append(f"{s['url']} does not support what it is cited for ({r.get('notes', '')[:100]})")
        rep.add("sources_verified", not problems, "; ".join(problems[:4]) + (f" (+{len(problems) - 4} more)"
                                                                               if len(problems) > 4 else ""))
    else:
        rep.add("fact_check_ran", False, "fact-check stage did not run")
    return rep
