"""TEMPORARY (removed before review): render PR #12's featured-image placement on live NewEarns
articles. Loads each published article as a visitor, inserts the exact figure images.featured_figure()
builds at the top of the post body, and measures desktop and mobile. Read-only: public pages and
the public REST API, no credentials, no writes."""
import json
import sys
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from publisher import images  # noqa: E402

SITE = "https://newearns.com"
SLUGS = ["instagram-follower-location", "compress-pdf-for-email"]
VIEWPORTS = {"desktop": {"width": 1440, "height": 900}, "mobile": {"width": 390, "height": 844}}
OUT = Path("render-check-out")
OUT.mkdir(exist_ok=True)

MEASURE = """(stem) => {
  const box = el => { if (!el) return null; const r = el.getBoundingClientRect();
    return {top: Math.round(r.top + scrollY), bottom: Math.round(r.bottom + scrollY), left: Math.round(r.left),
            width: Math.round(r.width), height: Math.round(r.height)}; };
  const content = document.querySelector('.entry-content');
  const firstP = content && content.querySelector(':scope > p');
  const fig = document.querySelector('figure.newearns-featured-image');
  const img = fig && fig.querySelector('img');
  const shows = [...document.querySelectorAll('img')].filter(i =>
      [i.currentSrc, i.src, i.getAttribute('data-src') || '', i.srcset || ''].join(' ').includes('/' + stem));
  const between = [];
  if (fig) { let n = fig.closest('article'); }
  return {
    title: box(document.querySelector('h1.entry-title, .entry-header h1, h1')),
    entry_meta: box(document.querySelector('.entry-meta')),
    content: box(content), first_p: box(firstP),
    first_p_text: firstP ? firstP.innerText.slice(0, 80) : null,
    first_content_child: content && content.firstElementChild ? content.firstElementChild.tagName + '.' + content.firstElementChild.className : null,
    figure: box(fig),
    img: img ? {...box(img), natural: [img.naturalWidth, img.naturalHeight], complete: img.complete,
                alt: img.alt, loading: img.loading, fetchpriority: img.getAttribute('fetchpriority')} : null,
    featured_imgs_visible: shows.filter(i => i.getBoundingClientRect().width > 0).length,
    wp_post_image: document.querySelectorAll('img.wp-post-image').length,
    header: box(document.querySelector('.site-header')), sidebar: box(document.querySelector('#right-sidebar, .sidebar')),
    horizontal_overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    page_width: [document.documentElement.scrollWidth, document.documentElement.clientWidth],
    widest: [...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > document.documentElement.clientWidth + 1)
      .slice(0, 3).map(e => e.tagName + '.' + (e.className || '') + ' right=' + Math.round(e.getBoundingClientRect().right)),
  };
}"""

report = {}
with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome")
    for slug in SLUGS:
        post = requests.get(f"{SITE}/wp-json/wp/v2/posts", params={"slug": slug, "_fields": "id,link,featured_media"},
                            timeout=30).json()[0]
        m = requests.get(f"{SITE}/wp-json/wp/v2/media/{post['featured_media']}",
                         params={"_fields": "id,source_url,alt_text"}, timeout=30).json()
        media = {"id": m["id"], "url": m["source_url"]}
        fig = images.featured_figure(media, {"featured": {"alt_text": m["alt_text"]}})
        stem = m["source_url"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
        for vp, size in VIEWPORTS.items():
            page = browser.new_page(viewport=size, locale="en-US")
            page.goto(post["link"], wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(1000)
            before = page.evaluate(MEASURE, stem)
            live_count = images.featured_on_page(post["link"], media["url"],
                                                 get=lambda u, **k: type("R", (), {"text": page.content(),
                                                                                   "raise_for_status": lambda s: None})())
            page.evaluate("(html) => document.querySelector('.entry-content').insertAdjacentHTML('afterbegin', html)", fig)
            page.wait_for_function("() => { const i = document.querySelector('figure.newearns-featured-image img');"
                                   " return i && i.complete && i.naturalWidth > 0; }", timeout=30000)
            page.wait_for_timeout(500)
            after = page.evaluate(MEASURE, stem)
            after_count = images.featured_on_page(post["link"], media["url"],
                                                  get=lambda u, **k: type("R", (), {"text": page.content(),
                                                                                    "raise_for_status": lambda s: None})())
            page.screenshot(path=str(OUT / f"{slug}-{vp}.png"), full_page=False)
            page.close()
            t, f, fp, img = after["title"], after["figure"], after["first_p"], after["img"]
            checks = {
                "figure_below_title": f["top"] >= t["bottom"],
                "figure_above_intro": f["bottom"] <= fp["top"],
                "intro_is_first_paragraph": after["first_p_text"] == before["first_p_text"],
                "figure_first_in_body": after["first_content_child"].startswith("FIGURE.wp-block-image"),
                "shown_once": after["featured_imgs_visible"] == 1 and after_count == 1,
                "theme_prints_none_before": before["featured_imgs_visible"] == 0 and before["wp_post_image"] == 0
                                           and live_count == 0,
                "fits_column": img["width"] <= after["content"]["width"] and f["left"] + f["width"] <= size["width"],
                "no_new_overflow": after["page_width"] == before["page_width"],
                "aspect_kept": abs(img["height"] / img["width"] - 630 / 1200) < 0.01,
                "alt_text": img["alt"] == m["alt_text"] and len(img["alt"]) >= 10,
                "not_lazy": img["loading"] != "lazy" and img["fetchpriority"] == "high",
                "header_unchanged": before["header"] == after["header"],
                "title_unchanged": before["title"] == after["title"],
                "content_width_unchanged": before["content"]["width"] == after["content"]["width"],
                "sidebar_unchanged_except_shift": (before["sidebar"] or {}).get("width") == (after["sidebar"] or {}).get("width"),
            }
            report[f"{slug} {vp}"] = {"checks": checks, "all_pass": all(checks.values()), "before": before, "after": after,
                                      "featured_on_page_before": live_count, "featured_on_page_after": after_count,
                                      "media": media, "figure_html": fig}
    browser.close()

(OUT / "report.json").write_text(json.dumps(report, indent=2))
for k, v in report.items():
    print(f"=== {k}: {'ALL PASS' if v['all_pass'] else 'FAIL'}")
    for c, ok in v["checks"].items():
        print(f"  {'ok  ' if ok else 'FAIL'} {c}")
    a = v["after"]
    print(f"  title {a['title']} | meta {a['entry_meta']} | figure {a['figure']} | intro {a['first_p']}")
    print(f"  img {a['img']}")
    print(f"  page width before {v['before']['page_width']} after {a['page_width']}; overflowing before: {v['before']['widest']}")
print("RESULT:", "ALL PASS" if all(v["all_pass"] for v in report.values()) else "FAILURES")
