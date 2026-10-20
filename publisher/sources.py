"""Source reader: fetch a page once per run, keep its main text, hand Claude excerpts.

Fetched pages used to reach Claude whole through the server-side web_fetch tool,
and every later step of the same request re-read them. This module fetches each
URL once from the runner, strips boilerplate (scripts, menus, cookie notices,
footers), and returns only the passages relevant to what is being checked, with
their neighbours and section headings so qualifications and dates stay attached.

Pages the runner cannot read (blocked, PDF, too short) are reported as such;
Claude then falls back to the server-side web_fetch for that URL.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

UAS = ["Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
       "Mozilla/5.0 (compatible; newearns-research/1.0)"]
MAX_BYTES = 4_000_000
MIN_MAIN_CHARS = 400          # less than this is a stub, a login wall or a JS-only page
DEFAULT_EXCERPT_CHARS = 6000  # about 1,500 tokens per call

SKIP_TAGS = {"script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg", "iframe",
             "button", "template", "select", "dialog", "menu"}
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
BLOCK_TAGS = {"p", "li", "blockquote", "pre", "dt", "dd", "td", "th", "figcaption", "caption"}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
BOILERPLATE = re.compile(
    r"cookie|consent|gdpr|banner|navbar|nav-|-nav|menu|breadcrumb|footer|header|sidebar|newsletter|subscribe|"
    r"share|social|related|recommended|advert|promo|popup|modal|skip-link|skip-to|site-alert|feedback", re.I)
SKIP_ROLES = {"navigation", "banner", "contentinfo", "dialog", "alertdialog", "search", "menu", "menubar"}
QUALIFIER = re.compile(
    r"\b(except|unless|however|but not|does not|do not|not apply|only if|only when|provided that|exempt|"
    r"exclud\w*|limit\w*|effective|as of|until|deadline|note:|important:|caution|warning)\b", re.I)
STOP = set("a an and are as at be by for from has have how i in is it its of on or that the this to was were "
           "what when where which who why will with you your can may must should does do not no".split())
DATE_META = ("article:published_time", "article:modified_time", "og:updated_time", "date", "dc.date",
             "dcterms.modified", "dcterms.date", "last-modified", "citation_publication_date")


def normalize_url(url: str) -> str:
    """Same page, same key: drop #fragments, utm_* tags and a trailing slash."""
    s = urlsplit(url.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(s.query, keep_blank_values=True)
                       if not k.lower().startswith("utm_")])
    path = s.path.rstrip("/") or "/"
    return urlunsplit((s.scheme.lower(), s.netloc.lower(), path, query, ""))


def same_site(a: str, b: str) -> bool:
    """Same host name, treating www.example.com and example.com as one. Nothing looser:
    sub.example.com, example.com.evil.com and user@host tricks are different sites."""
    def host(u: str) -> str:
        h = (urlsplit(u).hostname or "").lower()
        return h[4:] if h.startswith("www.") else h
    return bool(host(a)) and host(a) == host(b)


# ---------------------------------------------------------------- extraction
class _Extractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool, bool]] = []   # (tag, skipping, in_main)
        self.blocks: list[dict] = []
        self.buf: list[str] = []
        self.cur_tag = ""
        self.heading = ""
        self.title = ""
        self._in_title = False
        self._in_ldjson = False
        self._ld = []
        self.meta: dict[str, str] = {}
        self.times: list[str] = []
        self.has_main = False

    @property
    def skipping(self) -> bool:
        return bool(self.stack) and self.stack[-1][1]

    @property
    def in_main(self) -> bool:
        return bool(self.stack) and self.stack[-1][2]

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        self.buf = []
        if not text:
            return
        if self.cur_tag in HEADINGS:
            self.heading = text
            self.blocks.append({"text": text, "heading": True, "section": text, "main": self.in_main})
        else:
            self.blocks.append({"text": text, "heading": False, "section": self.heading, "main": self.in_main})

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key and a.get("content"):
                self.meta.setdefault(key, a["content"])
            return
        if tag == "time" and a.get("datetime"):
            self.times.append(a["datetime"])
        if tag == "title":
            self._in_title = True
        if tag == "script" and "ld+json" in a.get("type", ""):
            self._in_ldjson = True
        if tag in VOID_TAGS:
            if tag == "br":
                self.buf.append(" ")
            return
        marker = " ".join([a.get("id", ""), a.get("class", "")])
        skip = (self.skipping or tag in SKIP_TAGS or a.get("role", "").lower() in SKIP_ROLES
                or a.get("aria-hidden") == "true" or "hidden" in a
                or (tag in ("div", "section", "ul", "ol", "span") and bool(BOILERPLATE.search(marker))))
        main = self.in_main or tag in ("main", "article") or a.get("role") == "main"
        if tag in ("main", "article") or a.get("role") == "main":
            self.has_main = True
        if tag in BLOCK_TAGS or tag in HEADINGS:
            if not self.skipping:
                self._flush()
            self.cur_tag = tag
        self.stack.append((tag, skip, main))

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag == "script":
            self._in_ldjson = False
        if tag in BLOCK_TAGS or tag in HEADINGS:
            if not self.skipping:
                self._flush()
            self.cur_tag = ""
        if any(t == tag for t, _, _ in self.stack):
            while self.stack:
                if self.stack.pop()[0] == tag:
                    break

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._in_ldjson:
            self._ld.append(data)
            return
        if not self.skipping:
            self.buf.append(data)

    def close(self):
        super().close()
        self._flush()


def _ld_dates(chunks: list[str]) -> dict:
    out = {}
    for key in ("datePublished", "dateModified"):
        m = re.search(rf'"{key}"\s*:\s*"([^"]+)"', " ".join(chunks))
        if m:
            out[key] = m.group(1)
    return out


def extract(html: str) -> dict:
    """Main text blocks plus title and dates. Boilerplate never reaches Claude."""
    p = _Extractor()
    p.feed(html)
    p.close()
    blocks = [b for b in p.blocks if b["main"]] if p.has_main else p.blocks
    if p.has_main and sum(len(b["text"]) for b in blocks) < MIN_MAIN_CHARS:
        blocks = p.blocks  # <main> was a shell; fall back to the whole body minus boilerplate
    seen, unique = set(), []
    for b in blocks:  # repeated menus and "share this" lines
        key = b["text"].lower()
        if key in seen and len(key) < 200:
            continue
        seen.add(key)
        unique.append({"text": b["text"], "heading": b["heading"], "section": b["section"]})
    ld = _ld_dates(p._ld)
    published = ld.get("datePublished") or p.meta.get("article:published_time") or p.meta.get("datepublished") \
        or p.meta.get("citation_publication_date") or p.meta.get("dc.date") or p.meta.get("date") \
        or (p.times[0] if p.times else "")
    modified = ld.get("dateModified") or p.meta.get("article:modified_time") or p.meta.get("og:updated_time") \
        or p.meta.get("dcterms.modified") or p.meta.get("datemodified") or ""
    title = p.meta.get("og:title") or re.sub(r"\s+", " ", p.title).strip()
    return {"title": title, "published": published, "modified": modified, "blocks": unique}


# ---------------------------------------------------------------- excerpts
def _terms(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP and len(w) > 2}


def select_passages(blocks: list[dict], focus: str, max_chars: int = DEFAULT_EXCERPT_CHARS) -> tuple[str, bool]:
    """Relevant blocks in page order, each with its heading, the block before and
    after it, and any nearby qualifier ("except", "unless", "effective"...).
    Returns (excerpt, complete) where complete means the whole page fit."""
    full = "\n\n".join(("## " if b["heading"] else "") + b["text"] for b in blocks)
    if len(full) <= max_chars:
        return full, True
    terms = _terms(focus)
    scores = []
    for i, b in enumerate(blocks):
        bt = _terms(b["text"] + " " + (b["section"] or ""))
        hit = len(terms & bt)
        scores.append((hit + (0.5 if hit and QUALIFIER.search(b["text"]) else 0), i))
    chosen: set[int] = set()
    used = 0

    def add(i):
        nonlocal used
        if i in chosen or not 0 <= i < len(blocks):
            return
        chosen.add(i)
        used += len(blocks[i]["text"]) + 4

    for score, i in sorted(scores, key=lambda s: (-s[0], s[1])):
        if score <= 0 or used >= max_chars:
            break
        group = [i, i - 1, i + 1] + [j for j in (i - 2, i + 2) if 0 <= j < len(blocks)
                                     and QUALIFIER.search(blocks[j]["text"])]
        heading = next((j for j in range(i, -1, -1) if blocks[j]["heading"]), None)
        if heading is not None:
            group.append(heading)
        cost = sum(len(blocks[j]["text"]) + 4 for j in set(group) if 0 <= j < len(blocks) and j not in chosen)
        if used and used + cost > max_chars:
            continue
        for j in group:
            add(j)
    if not chosen:  # nothing matched: give the opening of the page
        for i in range(len(blocks)):
            if used >= max_chars:
                break
            add(i)
    out, last = [], -2
    for i in sorted(chosen):
        if i != last + 1:
            out.append("[...]")
        b = blocks[i]
        text = b["text"]
        if len(text) > max_chars:  # one enormous block: cut at a sentence and say so
            cut = text[:max_chars].rsplit(". ", 1)[0]
            text = cut + ". [block continues]"
        out.append(("## " if b["heading"] else "") + text)
        last = i
    if last != len(blocks) - 1:
        out.append("[...]")
    return "\n\n".join(out), False


# ---------------------------------------------------------------- per-run cache
@dataclass
class Page:
    url: str
    status: str                 # ok | too_short | unavailable
    reason: str = ""
    final_url: str = ""
    title: str = ""
    published: str = ""
    modified: str = ""
    blocks: list = field(default_factory=list)

    @property
    def chars(self) -> int:
        return sum(len(b["text"]) for b in self.blocks)


def http_fetch(url: str, timeout: int = 20) -> tuple[int, str, str, str]:
    """(status, content_type, final_url, text). Tries two user agents."""
    last = (0, "", url, "")
    for ua in UAS:
        try:
            r = requests.get(url, timeout=timeout, allow_redirects=True, stream=True,
                             headers={"User-Agent": ua, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
            body = r.raw.read(MAX_BYTES, decode_content=True) if r.status_code < 400 else b""
            enc = r.encoding or "utf-8"
            last = (r.status_code, r.headers.get("Content-Type", ""), r.url, body.decode(enc, "replace"))
            if r.status_code < 400:
                return last
        except requests.RequestException as e:
            last = (0, "", url, type(e).__name__)
    return last


class SourceReader:
    """Fetches each normalized URL at most once per run; pages persist under the
    run directory so --resume and the fact-check reuse them without re-downloading."""

    def __init__(self, directory: Path | None = None, fetcher=http_fetch, max_reads: int = 30,
                 excerpt_chars: int = DEFAULT_EXCERPT_CHARS):
        self.dir = directory
        self.excerpt_chars = excerpt_chars
        self.fetcher = fetcher
        self.max_reads = max_reads
        self.pages: dict[str, Page] = {}
        self.network_fetches = 0
        self.reads = 0
        if directory:
            directory.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path | None:
        return self.dir / (hashlib.sha256(key.encode()).hexdigest()[:20] + ".json") if self.dir else None

    def page(self, url: str) -> Page:
        key = normalize_url(url)
        if key in self.pages:
            return self.pages[key]
        path = self._path(key)
        if path and path.exists():
            self.pages[key] = Page(**json.loads(path.read_text()))
            return self.pages[key]
        self.network_fetches += 1
        code, ctype, final, body = self.fetcher(url)
        if code == 0 or code >= 400:
            pg = Page(url=url, status="unavailable", reason=f"HTTP {code}" if code else (body or "request failed"))
        elif "html" not in ctype.lower() and not body.lstrip().startswith("<"):
            pg = Page(url=url, status="unavailable", reason=f"not an HTML page ({ctype or 'unknown type'})",
                      final_url=final)
        else:
            ex = extract(body)
            pg = Page(url=url, status="ok", final_url=final, title=ex["title"], published=ex["published"],
                      modified=ex["modified"], blocks=ex["blocks"])
            if pg.chars < MIN_MAIN_CHARS:
                pg.status, pg.reason = "too_short", f"only {pg.chars} characters of readable text"
        self.pages[key] = pg
        if path:
            path.write_text(json.dumps(asdict(pg), ensure_ascii=False))
        return pg

    def read(self, url: str, focus: str, max_chars: int | None = None) -> dict:
        """What the read_source tool returns to Claude."""
        max_chars = max_chars or self.excerpt_chars
        self.reads += 1
        if self.reads > self.max_reads:
            return {"url": url, "status": "limit_reached",
                    "note": "read_source limit for this step reached; work with what you have."}
        if not re.match(r"https?://", url or ""):
            return {"url": url, "status": "unavailable", "note": "not an http(s) URL"}
        pg = self.page(url)
        out = {"url": url, "status": pg.status, "title": pg.title, "published": pg.published,
               "modified": pg.modified}
        if pg.final_url and normalize_url(pg.final_url) != normalize_url(url):
            # Say where the text really came from; a redirect to another site is not this source.
            out.update(requested_url=url, final_url=pg.final_url)
            out["redirect_note"] = (
                f"{url} redirected to {pg.final_url}; the text below came from {pg.final_url}. Cite that URL."
                if same_site(url, pg.final_url) else
                f"{url} redirected to a different site, {pg.final_url}. The text below came from that site, not "
                f"from {url}, and does not count as evidence from {url}.")
        if pg.status == "unavailable":
            out["note"] = (f"Could not read this page from here ({pg.reason}). Use web_fetch for this URL if you "
                           "still need it. Do not treat it as evidence until you have read it.")
            return out
        excerpt, complete = select_passages(pg.blocks, focus, max_chars)
        out.update(page_chars=pg.chars, complete=complete, excerpt=excerpt)
        if pg.status == "too_short":
            out["note"] = (f"{pg.reason}; the page may need JavaScript or a login. Use web_fetch for this URL, and "
                           "do not rely on it unless the text you can read states the fact.")
        elif not complete:
            out["sections"] = [b["text"] for b in pg.blocks if b["heading"]][:40]
            out["note"] = ("Excerpt of the passages most relevant to your focus, in page order; [...] marks "
                           "skipped text. If a fact could depend on text outside the excerpt, call read_source "
                           "again with a narrower focus (for example a section name above).")
        return out

    def verified_urls(self) -> list[str]:
        """URLs actually retrieved with readable text this run (counts for the link and evidence
        gates). A page that redirected to another site counts under neither URL; a same-site
        redirect (http/https, www or not, a moved path) counts under the requested and final URL."""
        out = set()
        for p in self.pages.values():
            if p.status == "ok" and same_site(p.url, p.final_url or p.url):
                out.update({p.url, p.final_url or p.url})
        return sorted(out)


READ_SOURCE_TOOL = {
    "name": "read_source",
    "description": (
        "Fetch a web page (once per run; repeat calls are served from cache) and return the passages relevant "
        "to `focus`, with the page title, publication/modified dates when the page states them, and the "
        "headings around each passage. Menus, cookie notices and footers are removed. Use this to read "
        "sources. If it reports status 'unavailable' or 'too_short', use web_fetch for that URL instead."),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["url", "focus"],
        "properties": {
            "url": {"type": "string", "description": "Full https URL to read."},
            "focus": {"type": "string",
                      "description": "The claim, facts or section you need from this page, in a few words."},
        },
    },
    "strict": True,
}
