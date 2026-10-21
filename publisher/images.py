"""Article images at no extra cost.

Featured image: a real, clearly licensed photograph found through free sources whose
APIs return each image's licence (Openverse and Wikimedia Commons, neither needs a
key; Pexels only if a PEXELS_API_KEY is ever added). The licence, its link and, for
CC BY / BY-SA, the creator are checked before anything is downloaded; the post
credits the photo. Image search engines are never used. The branded title card is a
last resort, reported as a fallback.

Inline images: diagrams of the article's own numbered step lists, only where a reader
follows steps; they add no new claims. No AI image generation is used: the workflow
runs on GitHub Actions, which has no image model.

Counts follow target_count(); it is a ceiling, not a quota. Successful searches are
cached in data/image_cache/, and a photo used once is not used for another article.
"""
from __future__ import annotations

import hashlib
import html as htmllib
import io
import json
import os
import re
import textwrap
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

from . import ledger

PALETTES = {
    "pdf": ((178, 34, 34), (255, 255, 255)),
    "instagram": ((131, 58, 180), (255, 255, 255)),
    "money": ((20, 110, 80), (255, 255, 255)),
}
FONT_CANDIDATES = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                   "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf"]
REGULAR_FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/usr/share/fonts/dejavu/DejaVuSans.ttf", "DejaVuSans.ttf"]
WEBP_QUALITY = 80
FEATURED_SIZE = (1200, 630)
USER_AGENT = "newearns-autopublisher/1.0 (+https://newearns.com)"


def _font(size: int, bold: bool = True):
    for path in FONT_CANDIDATES if bold else REGULAR_FONTS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _webp(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=WEBP_QUALITY, method=6)  # no EXIF is written
    return buf.getvalue()


def title_card(headline: str, pillar: str, width: int = 1200, height: int = 630) -> bytes:
    bg, fg = PALETTES.get(pillar, PALETTES["money"])
    img = Image.new("RGB", (width, height), bg)
    d = ImageDraw.Draw(img)
    d.rectangle([0, height - 90, width, height], fill=tuple(max(0, c - 40) for c in bg))
    big = _font(72)
    lines = textwrap.wrap(headline, width=24)[:4]
    y = (height - 90 - len(lines) * 88) // 2
    for line in lines:
        w = d.textlength(line, font=big)
        d.text(((width - w) / 2, y), line, font=big, fill=fg)
        y += 88
    d.text((48, height - 66), "newearns.com", font=_font(36), fill=fg)
    return _webp(img)


def _cap(text: str, n: int = 125) -> str:
    return text if len(text) <= n else text[:n - 3].rsplit(" ", 1)[0].rstrip(",;:") + "..."


def alt_text(headline: str) -> str:
    """Describes the card we actually draw, so it can never drift from the image."""
    return _cap(f'Title card reading "{headline}" on a newearns.com banner')


def slugify(text: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:n].rsplit("-", 1)[0] if len(s) > n and "-" in s[:n] else s[:n]


def target_count(words: int) -> tuple[int, int]:
    """(min, max) images including the featured image. A guideline, not a quota."""
    if words < 900:
        return 1, 2
    if words < 1500:
        return 2, 3
    if words <= 2500:
        return 3, 4
    return 4, 5


# ---------------------------------------------------------------- diagrams
def list_card(heading: str, items: list[str], pillar: str, numbered: bool, width: int = 1200) -> tuple[bytes, int]:
    """A clean summary card of one list: heading band, then numbered steps or check marks."""
    bg, fg = PALETTES.get(pillar, PALETTES["money"])
    head_font, item_font, foot_font = _font(44), _font(32, bold=False), _font(26)
    head_lines = textwrap.wrap(heading, width=40)[:2]
    item_lines = [textwrap.wrap(t, width=56)[:3] for t in items]
    head_h = 60 + 56 * len(head_lines)
    body_h = sum(30 + 42 * len(ls) for ls in item_lines) + 30
    height = head_h + body_h + 70
    img = Image.new("RGB", (width, height), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, width, head_h], fill=bg)
    y = 30
    for line in head_lines:
        d.text((60, y), line, font=head_font, fill=fg)
        y += 56
    y = head_h + 30
    for n, lines in enumerate(item_lines, 1):
        cx, cy = 90, y + 20
        d.ellipse([cx - 24, cy - 24, cx + 24, cy + 24], fill=bg)
        mark = str(n) if numbered else "\u2022"
        mw = d.textlength(mark, font=_font(28))
        d.text((cx - mw / 2, cy - 17), mark, font=_font(28), fill=fg)
        for line in lines:
            d.text((140, y + 2), line, font=item_font, fill=(33, 33, 33))
            y += 42
        y += 30
    d.line([60, height - 60, width - 60, height - 60], fill=(225, 225, 225), width=2)
    d.text((60, height - 46), "newearns.com", font=foot_font, fill=bg)
    return _webp(img), height


TAG = re.compile(r"<(/?)(h2|ol|ul|li)\b[^>]*>", re.I)


def _clean(fragment: str) -> str:
    text = re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()
    return re.sub(r"\s+([,.;:!?)])", r"\1", text)


def _lead(item_html: str) -> str:
    """The short label of a list item: its bold lead-in, else its first sentence."""
    m = re.match(r"\s*<(strong|b)>(.*?)</\1>", item_html, re.S | re.I)
    text = _clean(m.group(2)) if m else re.split(r"(?<=[.!?:])\s", _clean(item_html))[0]
    text = text.rstrip(".:;, ")
    return text if len(text) <= 110 else text[:107].rsplit(" ", 1)[0].rstrip(",;:") + "..."


def top_level_lists(content_html: str) -> list[dict]:
    """Top-level <ol>/<ul> blocks with their offset, the H2 they sit under and their item labels."""
    out, depth, h2, h2_start, cur, li_start, li_depth = [], 0, "", None, None, None, 0
    for m in TAG.finditer(content_html):
        close, tag = m.group(1) == "/", m.group(2).lower()
        if tag == "h2":
            if close and h2_start is not None:
                h2 = _clean(content_html[h2_start:m.start()])
                h2_start = None
            elif not close:
                h2_start = m.end()
        elif tag in ("ol", "ul"):
            if not close:
                if depth == 0:
                    cur = {"tag": tag, "start": m.start(), "heading": h2, "items": []}
                elif depth == 1 and li_start is not None and cur is not None:
                    # A nested list ends the parent item's label.
                    cur["items"].append(_lead(content_html[li_start:m.start()]))
                    li_start = None
                depth += 1
            else:
                depth = max(0, depth - 1)
                if depth == 0 and cur is not None:
                    out.append(cur)
                    cur = None
        elif tag == "li" and depth == 1 and cur is not None:
            if not close:
                li_start = m.end()
            elif li_start is not None:
                cur["items"].append(_lead(content_html[li_start:m.start()]))
                li_start = None
    for i, lst in enumerate(out):
        lst["index"] = i
        lst["items"] = [t for t in lst["items"] if t]
    return out


def pick_lists(lists: list[dict], want_max: int) -> list[dict]:
    """Numbered step lists only: a diagram of real steps helps a reader follow along; a diagram
    of a bulleted list would just repeat it. One per section, never two in neighbouring
    sections, never the FAQ. Fewer than want_max is fine: there is no quota."""
    headings = list(dict.fromkeys(l["heading"] for l in lists))
    chosen: list[dict] = []
    for lst in lists:
        h = lst["heading"].lower()
        if (len(chosen) < want_max and lst["tag"] == "ol" and 3 <= len(lst["items"]) <= 8 and lst["heading"]
                and not h.startswith("faq") and "frequently asked" not in h
                and all(abs(headings.index(c["heading"]) - headings.index(lst["heading"])) > 1 for c in chosen)):
            chosen.append(lst)
    return chosen


# ---------------------------------------------------------------- stock photos
class SearchUnavailable(RuntimeError):
    pass


# Provider licence codes -> ours. Anything not listed (NC, ND, GFDL, fair use, unknown) is never used.
OPENVERSE_LICENSES = {"cc0": "cc0", "pdm": "public-domain", "by": "cc-by", "by-sa": "cc-by-sa"}
LICENSE_NAMES = {"cc0": "CC0", "public-domain": "Public domain", "cc-by": "CC BY", "cc-by-sa": "CC BY-SA",
                 "pexels-license": "Pexels License"}
NEEDS_CREDIT = ("cc-by", "cc-by-sa")
NOT_A_PHOTO = re.compile(r"\b(logo|icon|screenshot|screen ?shot|diagram|chart|graph|map of|illustration|vector|"
                         r"clip ?art|drawing|infographic|poster|cartoon|meme|svg)\b", re.I)
STOPWORDS = set("""a an and are as at be by can do does for from get how in into is it its not of on or so that the
their them then these this to too up use using what when where which who why will with without you your yours
guide step steps explained vs""".split())


def _commons_license(meta: dict) -> tuple[str, str] | None:
    """(our licence code, version) from Commons extmetadata, or None when it is not one we accept."""
    code = (meta.get("License") or "").lower().strip()
    short = (meta.get("LicenseShortName") or "").lower()
    if code == "cc0" or short.startswith("cc0"):
        return "cc0", "1.0"
    if code == "pd" or code.startswith("pd-") or short.startswith("public domain"):
        return "public-domain", ""
    m = re.fullmatch(r"cc-(by|by-sa)-(\d\.\d)(?:-[a-z]+)?", code)
    return ({"by": "cc-by", "by-sa": "cc-by-sa"}[m.group(1)], m.group(2)) if m else None


class StockSearch:
    """Free stock photo search: licence data comes from the provider. Successful searches are
    cached on disk; a failed one is retried once, never cached, and reported."""

    def __init__(self, cache_dir: Path, *, pexels_key: str | None = None, cache_days: int = 30,
                 licenses=("cc0", "pdm", "by", "by-sa"), sources=("stocksnap", "nappy", "flickr", "wikimedia"),
                 get=requests.get, sleep=time.sleep, attempts: int = 2, timeout=(10, 30)):
        self.cache_dir, self.pexels_key, self.ttl = Path(cache_dir), pexels_key, cache_days * 86400
        self.licenses, self.sources, self.get, self.sleep = list(licenses), list(sources), get, sleep
        self.attempts, self.timeout = attempts, timeout
        self.requests = 0  # live API calls made (cache hits excluded)
        self.log: list[dict] = []  # one line per search, for the run report

    def providers(self) -> list[str]:
        return (["pexels"] if self.pexels_key else []) + ["openverse", "commons"]

    def search(self, provider: str, query: str) -> list[dict]:
        key = hashlib.sha1(f"{provider}|{query.lower().strip()}|{','.join(self.licenses)}|{','.join(self.sources)}"
                           .encode()).hexdigest()[:16]
        path = self.cache_dir / "search" / f"{provider}-{key}.json"
        if path.exists():
            cached = json.loads(path.read_text())
            if time.time() - cached.get("fetched_at", 0) < self.ttl:
                self.log.append({"provider": provider, "query": query, "result": f"{len(cached['results'])} (cached)"})
                return cached["results"]
        try:
            results = {"pexels": self._pexels, "openverse": self._openverse, "commons": self._commons}[provider](query)
        except SearchUnavailable as e:
            self.log.append({"provider": provider, "query": query, "result": f"failed: {e}"})
            raise
        self.log.append({"provider": provider, "query": query, "result": str(len(results))})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"provider": provider, "query": query, "fetched_at": int(time.time()),
                                    "results": results}, indent=1, ensure_ascii=False))
        return results

    def _fetch(self, url: str, **kw):
        """GET with one retry on a timeout, a dropped connection, 429 or 5xx."""
        why, headers = "", {"User-Agent": USER_AGENT, **kw.pop("headers", {})}
        for attempt in range(1, self.attempts + 1):
            self.requests += 1
            try:
                r = self.get(url, timeout=self.timeout, headers=headers, **kw)
            except requests.RequestException as e:
                why = type(e).__name__
            else:
                if r.status_code == 200:
                    return r
                why = f"HTTP {r.status_code}"
                if r.status_code != 429 and r.status_code < 500:
                    break
            if attempt < self.attempts:
                self.sleep(2 * attempt)
        raise SearchUnavailable(why)

    def _openverse(self, query: str) -> list[dict]:
        r = self._fetch("https://api.openverse.org/v1/images/", params={
            "q": query, "license": ",".join(self.licenses), "category": "photograph",
            "source": ",".join(self.sources), "mature": "false", "page_size": 20})
        out = []
        for x in r.json().get("results", []):
            lic = OPENVERSE_LICENSES.get((x.get("license") or "").lower())
            if not lic or (x.get("license") or "").lower() not in self.licenses:
                continue  # never trust a licence we did not ask for
            out.append({"provider": "openverse", "id": str(x.get("id")), "description": x.get("title") or "",
                        "text": "", "tags": [t.get("name", "") for t in x.get("tags") or []],
                        "image_url": x.get("url"), "page_url": x.get("foreign_landing_url"),
                        "creator": x.get("creator") or "", "creator_url": x.get("creator_url") or "",
                        "license": lic, "license_version": x.get("license_version") or "",
                        "license_url": x.get("license_url") or "", "source": x.get("source") or "",
                        "width": x.get("width") or 0, "height": x.get("height") or 0})
        return out

    def _commons(self, query: str) -> list[dict]:
        """Wikimedia Commons, read directly: no key, and each file's licence is in its metadata."""
        r = self._fetch("https://commons.wikimedia.org/w/api.php", params={
            "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6, "gsrlimit": 20,
            "gsrsearch": f"{query} filetype:bitmap", "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1600, "iiextmetadatalanguage": "en",
            "iiextmetadatafilter": "License|LicenseShortName|LicenseUrl|Artist|Restrictions|ImageDescription|"
                                   "ObjectName|NonFree|Categories"})
        out = []
        for page in ((r.json().get("query") or {}).get("pages") or {}).values():
            info = (page.get("imageinfo") or [{}])[0]
            meta = {k: (v or {}).get("value", "") for k, v in (info.get("extmetadata") or {}).items()}
            lic = _commons_license(meta)
            if (not lic or info.get("mime") != "image/jpeg" or meta.get("Restrictions")
                    or str(meta.get("NonFree", "")).lower() == "true"):
                continue  # unclear or restricted (personality rights, trademarks): not downloaded
            artist = str(meta.get("Artist", ""))
            link = re.search(r'href="(https://[^"]+)"', artist) or re.search(r'href="//([^"]+)"', artist)
            title = re.sub(r"\.(jpe?g)$", "", page.get("title", "").removeprefix("File:"), flags=re.I)
            out.append({"provider": "commons", "id": str(page.get("pageid")),
                        "description": _clean(str(meta.get("ObjectName") or "")) or title.replace("_", " "),
                        "text": _clean(str(meta.get("ImageDescription", "")))[:500] + " " + title.replace("_", " "),
                        "tags": [t for t in str(meta.get("Categories", "")).split("|") if t][:15],
                        "image_url": info.get("thumburl") or info.get("url"), "page_url": info.get("descriptionurl"),
                        "creator": _clean(artist)[:120],
                        "creator_url": ("https://" + link.group(1)) if link and not link.group(1).startswith("https")
                        else (link.group(1) if link else ""),
                        "license": lic[0], "license_version": lic[1], "license_url": meta.get("LicenseUrl", ""),
                        "source": "wikimedia", "width": info.get("width") or 0, "height": info.get("height") or 0})
        return out

    def _pexels(self, query: str) -> list[dict]:
        r = self._fetch("https://api.pexels.com/v1/search", headers={"Authorization": self.pexels_key},
                        params={"query": query, "orientation": "landscape", "per_page": 20})
        return [{"provider": "pexels", "id": str(x.get("id")), "description": x.get("alt") or "", "text": "",
                 "tags": [], "image_url": (x.get("src") or {}).get("large2x") or (x.get("src") or {}).get("original"),
                 "page_url": x.get("url"), "creator": x.get("photographer") or "",
                 "creator_url": x.get("photographer_url") or "", "license": "pexels-license",
                 "license_version": "", "license_url": "https://www.pexels.com/license/", "source": "pexels",
                 "width": x.get("width") or 0, "height": x.get("height") or 0}
                for x in r.json().get("photos", [])]


def licence_clear(c: dict) -> bool:
    """Checked before anything is downloaded: a licence we accept, where to read it, and for
    CC BY / BY-SA the creator we must credit."""
    lic = c.get("license")
    if lic not in LICENSE_NAMES or not str(c.get("page_url") or "").startswith("https://"):
        return False
    if lic in NEEDS_CREDIT:
        return bool(c.get("creator") and str(c.get("license_url") or "").startswith("https://"))
    return lic == "public-domain" or str(c.get("license_url") or "").startswith("https://")


def terms(*texts: str) -> set[str]:
    words = re.findall(r"[a-z]+", " ".join(t or "" for t in texts).lower())
    out = {w for w in words if len(w) >= 4 and w not in STOPWORDS}
    return out | {w[:-1] for w in out if w.endswith("s")}


def score(c: dict, article_terms: set[str], keywords: list[str]) -> int:
    """0 = not usable. Otherwise higher is better: words of this article count three times as
    much as the pillar's scene words, so the photo fits the article, not just the topic."""
    desc = c.get("description", "")
    text = " ".join([desc, c.get("text", ""), *c.get("tags", [])]).lower()
    if (c.get("width", 0) < FEATURED_SIZE[0] or c.get("height", 0) < FEATURED_SIZE[1]
            or c["width"] < c["height"] or not str(c.get("image_url", "")).startswith("https://")
            or len(re.findall(r"[a-zA-Z]{2,}", desc)) < 2 or re.search(r"\b(img|dsc|dscn|p)[_ -]?\d{3,}", desc, re.I)
            or NOT_A_PHOTO.search(text)):
        return 0
    found = terms(text)
    hits = len(article_terms & found)
    kw = sum(1 for k in keywords if re.search(rf"\b{re.escape(k.lower())}\b", text))
    total = 3 * hits + kw
    return total if total >= 2 else 0


def _used_path() -> Path:
    return ledger.DATA / "image_cache" / "used.json"


def used_photos() -> dict:
    p = _used_path()
    return json.loads(p.read_text()) if p.exists() else {}


def mark_used(plan: dict, slug: str) -> None:
    f = plan.get("featured") or {}
    if f.get("provider") in ("pexels", "openverse", "commons"):
        used = used_photos()
        used[f"{f['provider']}:{f['id']}"] = slug
        _used_path().parent.mkdir(parents=True, exist_ok=True)
        _used_path().write_text(json.dumps(used, indent=1, sort_keys=True))


def cover(data: bytes, size=FEATURED_SIZE) -> bytes:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = size
    scale = max(w / img.width, h / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    left, top = (img.width - w) // 2, (img.height - h) // 2
    return _webp(img.crop((left, top, left + w, top + h)))


def download(url: str, get=requests.get, limit: int = 15_000_000) -> bytes:
    r = get(url, timeout=(10, 60), headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    if not r.headers.get("Content-Type", "image/").startswith("image/") or len(r.content) > limit:
        raise ValueError(f"not an image or too large: {url}")
    return r.content


def _alt_from(c: dict) -> str:
    text = re.sub(r"\([^)]*\)|\[[^]]*\]", "", c["description"]).replace("_", " ")
    text = re.sub(r"\s+", " ", re.sub(r"[-_ ]\d+$", "", text)).strip(" .-,")
    return _cap(text[:1].upper() + text[1:])


def featured_photo(cfg: dict, pillar: str, queries: list[str], article_terms: set[str], search: StockSearch,
                   say=print, get=requests.get) -> tuple[dict, bytes] | None:
    """Best-scoring photo with a clear licence, provider by provider. A search that fails twice
    is skipped; two failed searches in a row mark that provider as down for this run."""
    ic = cfg.get("images") or {}
    keywords = (ic.get("photo_keywords") or {}).get(pillar, [])
    used = used_photos()
    for provider in search.providers():
        failures, ranked = 0, []
        for n, q in enumerate(queries[: ic.get("max_queries", 3)]):
            try:
                results = search.search(provider, q)
            except SearchUnavailable as e:
                say(f"Images: {provider} search for {q!r} failed ({e}).")
                failures += 1
                if failures >= 2:
                    say(f"Images: {provider} looks unavailable; trying the next source.")
                    break
                continue
            failures = 0
            for c in results:
                if f"{provider}:{c['id']}" in used or not licence_clear(c):
                    continue  # licence unclear: never downloaded
                s = score(c, article_terms, keywords)
                if s:
                    ranked.append((-s, n, c, q))
        seen = set()
        for _, _, c, q in sorted(ranked, key=lambda t: t[:2])[:4]:
            if c["id"] in seen:
                continue
            seen.add(c["id"])
            try:
                return dict(c, query=q), cover(download(c["image_url"], get=get))
            except Exception as e:
                say(f"Images: could not download {c['page_url']} ({str(e)[:120]}).")
    return None


# ---------------------------------------------------------------- plan
def build(cfg: dict, article: dict, pillar: str, out_dir: Path, *, query: str | None = None,
          search: StockSearch | None = None, say=print, get=requests.get) -> dict:
    """Choose, draw or download every image for one article and save them in out_dir.
    Returns the plan stored in the run checkpoint; the bytes are the files beside it."""
    ic = cfg.get("images") or {}
    words = len(_clean(article["content_html"]).split())
    lo, hi = target_count(words)
    slug = article["slug"]
    plan = {"words": words, "target": [lo, hi], "stock_api_calls": 0, "photo_searches": [], "notes": []}

    featured = None
    if ic.get("stock_photos", True):
        search = search or StockSearch(ledger.DATA / "image_cache", pexels_key=os.environ.get("PEXELS_API_KEY"),
                                       cache_days=ic.get("cache_days", 30),
                                       licenses=ic.get("stock_licenses", ("cc0", "pdm", "by", "by-sa")),
                                       sources=ic.get("openverse_sources", ("stocksnap", "nappy", "flickr", "wikimedia")))
        queries = list(dict.fromkeys(q for q in [query, article.get("focus_keyphrase"),
                                                 *(ic.get("photo_queries") or {}).get(pillar, [])] if q))
        article_terms = terms(query or "", article.get("focus_keyphrase", ""), article.get("title", ""))
        before, logged = search.requests, len(search.log)
        try:
            featured = featured_photo(cfg, pillar, queries, article_terms, search, say=say, get=get)
        except Exception as e:  # never let an image problem stop the article
            say(f"Images: stock photo search failed ({str(e)[:120]}).")
        plan["stock_api_calls"] = search.requests - before
        plan["photo_searches"] = search.log[logged:]
    if featured:
        c, data = featured
        plan["featured"] = {k: c[k] for k in ("provider", "id", "description", "page_url", "creator", "creator_url",
                                              "license", "license_version", "license_url", "source", "query")}
        plan["featured"].update(alt_text=_alt_from(c), caption="", filename=f"{slug}-featured.webp")
    else:
        headline = article.get("image_headline") or article["title"]
        data = title_card(headline, pillar)
        plan["featured"] = {"provider": "in-house", "license": "generated-in-house", "alt_text": alt_text(headline),
                            "caption": "", "filename": f"{slug}-featured.webp", "fallback": True}
        plan["notes"].append("FALLBACK: no suitable, clearly licensed photo was found, so the featured image is "
                             "the branded title card. Replace it in WP Admin if you prefer a photo.")
        say("Images: no suitable licensed photo found; using the title card as a last resort.")
    (out_dir / "featured.webp").write_bytes(data)
    plan["featured"]["file"] = "featured.webp"
    plan["featured"]["bytes"] = len(data)

    inline = []
    for n, lst in enumerate(pick_lists(top_level_lists(article["content_html"]),
                                       min(hi - 1, ic.get("max_inline", 2))), 1):
        heading = lst["heading"]
        data, height = list_card(heading, lst["items"], pillar, numbered=True)
        (out_dir / f"inline-{n}.webp").write_bytes(data)
        inline.append({"provider": "in-house", "license": "generated-in-house", "kind": "steps",
                       "list_index": lst["index"], "heading": heading, "file": f"inline-{n}.webp",
                       "filename": f"{slug}-steps-{slugify(heading, 40)}.webp", "width": 1200, "height": height,
                       "bytes": len(data),
                       "alt_text": _cap(f'Diagram of the {len(lst["items"])} steps in "{heading}": '
                                        + "; ".join(lst["items"])),
                       "caption": f"At a glance: {heading}"})
    plan["inline"] = inline
    if 1 + len(inline) < lo:
        plan["notes"].append(f"{1 + len(inline)} image(s), below the {lo}-{hi} guideline: no other step-by-step "
                             "section would gain from a diagram, and decorative images are not added")
    return plan


# ---------------------------------------------------------------- insertion and checks
def figure(src: str, media_id: int, img: dict) -> str:
    e = htmllib.escape
    size = f' width="{img["width"]}" height="{img["height"]}"' if img.get("width") else ""
    cap = f'<figcaption class="wp-element-caption">{e(img["caption"])}</figcaption>' if img.get("caption") else ""
    return (f'<figure class="wp-block-image size-large"><img src="{e(src)}" alt="{e(img["alt_text"])}"{size} '
            f'class="wp-image-{media_id}" loading="lazy" decoding="async"/>{cap}</figure>')


def credit_line(plan: dict) -> str:
    """Attribution for the featured photo, as its licence asks (and as a courtesy where it doesn't)."""
    f = plan.get("featured") or {}
    if f.get("provider") not in ("pexels", "openverse", "commons"):
        return ""
    e = htmllib.escape
    by = f' by <a href="{e(f["creator_url"])}">{e(f["creator"])}</a>' if f.get("creator_url", "").startswith("https://") \
        else (f" by {e(f['creator'])}" if f.get("creator") else "")
    if f["provider"] == "pexels":
        lic = 'on <a href="https://www.pexels.com/">Pexels</a>'
    else:
        name = LICENSE_NAMES[f["license"]] + (f" {f['license_version']}" if f.get("license_version")
                                               and f["license"] != "public-domain" else "")
        lic = (f'(<a href="{e(f["license_url"])}">{e(name)}</a>)' if f.get("license_url") else f"({e(name)})")
        lic += ", via Wikimedia Commons" if f["provider"] == "commons" else ", via Openverse"
    same = " This version is available under the same licence." if f["license"] == "cc-by-sa" else ""
    return (f'<p class="image-credits"><small>Featured image: <a href="{e(f["page_url"])}">{e(f["description"][:80])}</a>'
            f"{by} {lic}; cropped and converted to WebP.{same}</small></p>")


def insert(content_html: str, plan: dict, uploaded: list[dict]) -> str:
    """Each diagram goes just before the list it summarises; the credit line goes last."""
    starts = {l["index"]: l["start"] for l in top_level_lists(content_html)}
    out = content_html
    for img, media in sorted(zip(plan.get("inline", []), uploaded), key=lambda p: -starts[p[0]["list_index"]]):
        at = starts[img["list_index"]]
        out = out[:at] + figure(media["url"], media["id"], img) + out[at:]
    return out + credit_line(plan)


def check_in_post(post: dict, featured: dict, uploaded: list[dict], get=requests.get) -> list[str]:
    """Problems with the images in the saved post: featured image set, every diagram in the
    stored content, and every file served as an image."""
    problems = []
    if post.get("featured_media") != featured["id"]:
        problems.append(f"featured image is {post.get('featured_media')}, expected media {featured['id']}")
    content = (post.get("content") or {}).get("raw") or (post.get("content") or {}).get("rendered") or ""
    for m in uploaded:
        if f"wp-image-{m['id']}" not in content or m["url"] not in content:
            problems.append(f"image {m['id']} is not in the post content")
    for m in [featured, *uploaded]:
        try:
            r = get(m["url"], timeout=30, headers={"User-Agent": USER_AGENT}, stream=True)
            ok = r.status_code == 200 and r.headers.get("Content-Type", "").startswith("image/")
            r.close()
        except requests.RequestException as e:
            ok, r = False, e
        if not ok:
            problems.append(f"{m['url']} does not load as an image ({getattr(r, 'status_code', r)})")
    return problems


def report(plan: dict) -> list[str]:
    f = plan["featured"]
    if f["provider"] == "in-house":
        src = "FALLBACK title card (no suitable licensed photo)"
    else:
        name = LICENSE_NAMES[f["license"]] + (f" {f['license_version']}" if f.get("license_version") else "")
        src = f"{f['provider']} photo by {f.get('creator') or 'unknown'}, {name}, {f.get('page_url')} (query {f.get('query')!r})"
    rows = [f"{plan['words']} words; guideline {plan['target'][0]}-{plan['target'][1]} images; "
            f"using {1 + len(plan['inline'])}. Image cost: $0 (free stock API calls this run: {plan['stock_api_calls']}).",
            f"- featured: {src}; alt: {f['alt_text']!r}; {f['bytes'] // 1024} KB"]
    rows += [f"- steps diagram before the list under {i['heading']!r}; {i['bytes'] // 1024} KB" for i in plan["inline"]]
    rows += [f"- search: {s['provider']} {s['query']!r} -> {s['result']}" for s in plan.get("photo_searches", [])]
    return rows + [f"- note: {n}" for n in plan["notes"]]
