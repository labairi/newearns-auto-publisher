"""Deterministic quality gates. An article publishes only if every check passes.

Each check returns (name, passed, detail). `hard` failures block publishing;
there are no soft passes: anything failing goes to the review queue.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from html.parser import HTMLParser
from urllib.parse import urlparse

import requests

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

    def as_dict(self):
        return [{"check": n, "passed": ok, "detail": d} for n, ok, d in self.results]


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

    # internal links must point at real, published New Earns posts
    live = {p["link"].rstrip("/") for p in existing_posts if p.get("link")}
    internal = [h for h in hrefs if urlparse(h).netloc == site]
    bad_internal = [h for h in internal if h.split("#")[0].split("?")[0].rstrip("/") not in live]
    rep.add("internal_links_valid", not bad_internal, "; ".join(bad_internal))
    rep.add("internal_links_present", len(internal) >= (1 if live else 0), f"{len(internal)} internal links")

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
    rep.add("image_rights", img.get("license") in ("generated-in-house", "pexels-license", "own-screenshot"),
            img.get("license", "none"))
    rep.add("inline_images_have_alt", all(i.get("alt") for i in doc.images), f"{len(doc.images)} inline images")

    # 11. independent fact-check pass (LLM, run before these checks)
    if fact_check is not None:
        bad = [c for c in fact_check.get("claims", []) if c["verdict"] != "supported"]
        rep.add("fact_check_claims", not bad, " | ".join(f"{c['verdict']}: {c['claim'][:80]}" for c in bad[:4]))
        rep.add("fact_check_intent", fact_check.get("satisfies_search_intent") is True, fact_check.get("intent_notes", ""))
        rep.add("fact_check_original_value", fact_check.get("adds_original_value") is True, fact_check.get("value_notes", ""))
        rep.add("fact_check_product_claims", fact_check.get("product_claims_accurate") is True,
                fact_check.get("product_notes", ""))
    else:
        rep.add("fact_check_ran", False, "fact-check stage did not run")
    return rep
