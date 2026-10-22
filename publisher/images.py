"""Article images at no extra cost.

Featured image: a real, clearly licensed photograph found through free sources whose
APIs return each image's licence (Openverse and Wikimedia Commons, neither needs a
key; Pexels only if a PEXELS_API_KEY is ever added). The licence, its link and, for
CC BY / BY-SA, the creator are checked before anything is downloaded; the post
credits the photo. Image search engines are never used. A photo must match the article's
topic (relevance()): the device the task is done on, the topic's subject words, and no
wrong scene such as signing, meetings, books or paperwork. Without one, the featured image
is an in-house illustration of the topic, reported as a fallback; the branded title card
only if that cannot be drawn.

Inline images: only where they add something. A real screenshot of our own product's
page, captured live at run time and dated, where the article links it (never a
third-party app screen, never an imitation of one). A bar chart of a table's figures,
drawn here, where a table compares quantities. No AI image generation is used: the
workflow runs on GitHub Actions, which has no image model.

Counts follow target_count(); it is a ceiling, not a quota. Successful searches are
cached in data/image_cache/, and a photo used once is not used for another article.
"""
from __future__ import annotations

import hashlib
import html as htmllib
import io
import json
import math
import os
import re
import textwrap
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

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


# ---------------------------------------------------------------- topic illustrations
# Used for the featured image when no photo passes the relevance check. Each motif shows the
# article's task (a file getting smaller, a locked file, two files becoming one...) on a plain
# laptop or phone. It is clearly a drawing: no app interface, brand or made-up figures.
MOTIFS = ("compress", "lock", "merge", "split", "convert", "edit", "sign", "pdf", "location", "phone", "earnings")
MOTIF_ALT = {
    "compress": "Illustration of a laptop screen where a large PDF file shrinks into a smaller one",
    "lock": "Illustration of a laptop screen showing a PDF file protected by a padlock",
    "merge": "Illustration of a laptop screen where two PDF files combine into one",
    "split": "Illustration of a laptop screen where one PDF file splits into two",
    "convert": "Illustration of a laptop screen where a PDF file turns into an editable document",
    "edit": "Illustration of a laptop screen showing a PDF page being edited with a pencil",
    "sign": "Illustration of a laptop screen showing a PDF page with a handwritten signature",
    "pdf": "Illustration of a laptop screen showing a PDF file",
    "location": "Illustration of a smartphone showing a map with location pins",
    "phone": "Illustration of a smartphone showing a grid of photos",
    "earnings": "Illustration of a laptop screen showing a rising bar chart and a coin",
}


def _tint(rgb, t):
    """rgb mixed with white: t=0 is rgb, t=1 is white."""
    return tuple(round(c + (255 - c) * t) for c in rgb)


def _doc(d, x, y, w, k, accent, label="PDF", lines=4):
    """A file icon with a folded corner, top-left at (x, y), width w (height 1.3 w)."""
    h, f = w * 1.3, w * 0.28
    d.polygon([(x * k, y * k), ((x + w - f) * k, y * k), ((x + w) * k, (y + f) * k), ((x + w) * k, (y + h) * k),
               (x * k, (y + h) * k)], fill=(255, 255, 255), outline=(206, 211, 219), width=max(2, int(w / 40)) * k)
    d.polygon([((x + w - f) * k, y * k), ((x + w - f) * k, (y + f) * k), ((x + w) * k, (y + f) * k)],
              fill=(226, 230, 236))
    tag_h = w * 0.26
    d.rounded_rectangle([(x - w * 0.12) * k, (y + h * 0.18) * k, (x + w * 0.62) * k, (y + h * 0.18 + tag_h) * k],
                        radius=int(w * 0.05) * k, fill=accent)
    if label:
        fnt = _font(int(w * 0.17) * k)
        tw = d.textlength(label, font=fnt)
        d.text(((x + w * 0.25) * k - tw / 2, (y + h * 0.18 + tag_h * 0.18) * k), label, font=fnt, fill=(255, 255, 255))
    ly = y + h * 0.18 + tag_h + w * 0.14
    for i in range(lines):
        if ly + w * 0.05 > y + h - w * 0.1:
            break
        d.rounded_rectangle([(x + w * 0.14) * k, ly * k, (x + w * (0.86 if i % 3 != 2 else 0.6)) * k,
                             (ly + w * 0.05) * k], radius=int(w * 0.025) * k, fill=(214, 219, 226))
        ly += w * 0.12
    return h


def _arrow(d, x1, x2, y, k, color, width=10):
    d.line([x1 * k, y * k, (x2 - 18) * k, y * k], fill=color, width=width * k)
    d.polygon([((x2 - 30) * k, (y - 20) * k), (x2 * k, y * k), ((x2 - 30) * k, (y + 20) * k)], fill=color)


def _padlock(d, cx, cy, s, k, color):
    d.arc([(cx - s * 0.32) * k, (cy - s * 0.85) * k, (cx + s * 0.32) * k, (cy - s * 0.05) * k], 180, 360,
          fill=color, width=int(s * 0.12) * k)
    d.line([(cx - s * 0.32 + s * 0.06) * k, (cy - s * 0.45) * k, (cx - s * 0.32 + s * 0.06) * k, cy * k],
           fill=color, width=int(s * 0.12) * k)
    d.line([(cx + s * 0.32 - s * 0.06) * k, (cy - s * 0.45) * k, (cx + s * 0.32 - s * 0.06) * k, cy * k],
           fill=color, width=int(s * 0.12) * k)
    d.rounded_rectangle([(cx - s * 0.5) * k, (cy - s * 0.1) * k, (cx + s * 0.5) * k, (cy + s * 0.65) * k],
                        radius=int(s * 0.12) * k, fill=color)
    d.ellipse([(cx - s * 0.08) * k, (cy + s * 0.12) * k, (cx + s * 0.08) * k, (cy + s * 0.28) * k], fill=(255, 255, 255))
    d.rectangle([(cx - s * 0.03) * k, (cy + s * 0.22) * k, (cx + s * 0.03) * k, (cy + s * 0.42) * k], fill=(255, 255, 255))


def _pin(d, cx, cy, s, k, color):
    d.ellipse([(cx - s / 2) * k, (cy - s) * k, (cx + s / 2) * k, cy * k], fill=color)
    d.polygon([((cx - s * 0.42) * k, (cy - s * 0.32) * k), ((cx + s * 0.42) * k, (cy - s * 0.32) * k),
               (cx * k, (cy + s * 0.55) * k)], fill=color)
    d.ellipse([(cx - s * 0.18) * k, (cy - s * 0.68) * k, (cx + s * 0.18) * k, (cy - s * 0.32) * k], fill=(255, 255, 255))


def _laptop(img, d, k, screen_box):
    """A plain laptop whose screen is screen_box (x0, y0, x1, y1); returns nothing, draws in place."""
    x0, y0, x1, y1 = screen_box
    bez = 18
    shadow = Image.new("L", img.size, 0)
    ImageDraw.Draw(shadow).rounded_rectangle([(x0 - 130) * k, (y1 + 30) * k, (x1 + 130) * k, (y1 + 70) * k],
                                             radius=30 * k, fill=70)
    img.paste((0, 0, 0), mask=shadow.filter(ImageFilter.GaussianBlur(18 * k)))
    d.rounded_rectangle([(x0 - bez) * k, (y0 - bez) * k, (x1 + bez) * k, (y1 + bez) * k], radius=22 * k,
                        fill=(38, 41, 48))
    d.rectangle([x0 * k, y0 * k, x1 * k, y1 * k], fill=(250, 251, 253))
    d.ellipse([((x0 + x1) / 2 - 4) * k, (y0 - 12) * k, ((x0 + x1) / 2 + 4) * k, (y0 - 4) * k], fill=(70, 74, 82))
    d.polygon([((x0 - 120) * k, (y1 + bez) * k), ((x1 + 120) * k, (y1 + bez) * k), ((x1 + 150) * k, (y1 + bez + 34) * k),
               ((x0 - 150) * k, (y1 + bez + 34) * k)], fill=(196, 201, 209))
    d.rounded_rectangle([((x0 + x1) / 2 - 70) * k, (y1 + bez) * k, ((x0 + x1) / 2 + 70) * k, (y1 + bez + 10) * k],
                        radius=5 * k, fill=(170, 176, 186))


def _phone(img, d, k, box):
    x0, y0, x1, y1 = box
    shadow = Image.new("L", img.size, 0)
    ImageDraw.Draw(shadow).rounded_rectangle([(x0 + 24) * k, (y0 + 34) * k, (x1 + 24) * k, (y1 + 34) * k],
                                             radius=60 * k, fill=80)
    img.paste((0, 0, 0), mask=shadow.filter(ImageFilter.GaussianBlur(20 * k)))
    d.rounded_rectangle([x0 * k, y0 * k, x1 * k, y1 * k], radius=52 * k, fill=(38, 41, 48))
    d.rounded_rectangle([(x0 + 14) * k, (y0 + 14) * k, (x1 - 14) * k, (y1 - 14) * k], radius=40 * k,
                        fill=(250, 251, 253))
    d.rounded_rectangle([((x0 + x1) / 2 - 40) * k, (y0 + 26) * k, ((x0 + x1) / 2 + 40) * k, (y0 + 44) * k],
                        radius=9 * k, fill=(38, 41, 48))
    return x0 + 14, y0 + 56, x1 - 14, y1 - 14


def topic_illustration(motif: str, pillar: str, width: int = 1200, height: int = 630) -> bytes:
    """Featured illustration for one task, drawn at 2x and scaled down."""
    k = 2
    accent = PALETTES.get(pillar, PALETTES["money"])[0]
    img = Image.new("RGB", (width * k, height * k), _tint(accent, 0.93))
    d = ImageDraw.Draw(img)
    for i in range(height * k):  # soft vertical gradient
        d.line([0, i, width * k, i], fill=_tint(accent, 0.95 - 0.1 * i / (height * k)))
    d.ellipse([-160 * k, 380 * k, 420 * k, 900 * k], fill=_tint(accent, 0.82))
    d.ellipse([930 * k, -180 * k, 1380 * k, 260 * k], fill=_tint(accent, 0.86))
    green, grey = (22, 140, 92), (150, 156, 166)
    if motif in ("location", "phone"):
        sx0, sy0, sx1, sy1 = _phone(img, d, k, (470, 40, 730, 600))
        if motif == "location":
            d.rectangle([sx0 * k, sy0 * k, sx1 * k, sy1 * k], fill=(232, 240, 233))
            for (ax, ay, bx, by, w) in [(sx0, 220, sx1, 160, 14), (sx0, 400, sx1, 470, 10), (560, sy0, 620, sy1, 12),
                                        (sx0, 300, 640, 330, 8)]:
                d.line([ax * k, ay * k, bx * k, by * k], fill=(255, 255, 255), width=w * k)
            d.ellipse([640 * k, 360 * k, 700 * k, 420 * k], fill=(198, 226, 205))
            for cx, cy, s in [(545, 250, 64), (655, 330, 46), (580, 470, 40)]:
                _pin(d, cx, cy, s, k, accent)
        else:
            cw = (sx1 - sx0 - 24) / 3
            for r in range(5):
                for c in range(3):
                    x, y = sx0 + 6 + c * (cw + 6), sy0 + 6 + r * (cw + 6)
                    if y + cw < sy1:
                        d.rectangle([x * k, y * k, (x + cw) * k, (y + cw) * k],
                                    fill=_tint(accent, 0.35 + 0.12 * ((r + c) % 4)))
        return _webp(img.resize((width, height), Image.LANCZOS))

    sx0, sy0, sx1, sy1 = 300, 70, 900, 450
    _laptop(img, d, k, (sx0, sy0, sx1, sy1))
    cy = (sy0 + sy1) / 2
    if motif == "compress":
        _doc(d, 360, 105, 200, k, accent, lines=6)
        _arrow(d, 600, 700, cy, k, green)
        _doc(d, 738, 210, 110, k, accent, lines=3)
        # file-size bars, no figures: long under the big file, short under the small one
        d.rounded_rectangle([360 * k, 405 * k, 560 * k, 419 * k], radius=7 * k, fill=_tint(accent, 0.15))
        d.rounded_rectangle([738 * k, 405 * k, 778 * k, 419 * k], radius=7 * k, fill=green)
        d.rounded_rectangle([778 * k, 405 * k, 848 * k, 419 * k], radius=7 * k, fill=(226, 230, 236))
        for dy in (-1, 1):  # squeeze marks above and below the small file
            d.line([780 * k, (cy + dy * 100) * k, 806 * k, (cy + dy * 76) * k], fill=green, width=6 * k)
            d.line([832 * k, (cy + dy * 100) * k, 806 * k, (cy + dy * 76) * k], fill=green, width=6 * k)
    elif motif == "lock":
        _doc(d, 470, 110, 200, k, accent, lines=6)
        d.ellipse([620 * k, 255 * k, 770 * k, 405 * k], fill=(255, 255, 255), outline=(206, 211, 219), width=3 * k)
        _padlock(d, 695, 335, 90, k, (38, 41, 48))
    elif motif == "merge":
        _doc(d, 350, 105, 120, k, accent, lines=4)
        _doc(d, 350, 285, 120, k, _tint(accent, 0.3), lines=3)
        _arrow(d, 510, 640, cy, k, green)
        _doc(d, 680, 145, 170, k, accent, lines=6)
    elif motif == "split":
        _doc(d, 350, 145, 170, k, accent, lines=6)
        _arrow(d, 560, 690, cy, k, green)
        _doc(d, 730, 105, 120, k, accent, lines=4)
        _doc(d, 730, 285, 120, k, _tint(accent, 0.3), lines=3)
    elif motif == "convert":
        _doc(d, 360, 135, 170, k, accent, lines=6)
        _arrow(d, 570, 690, cy, k, green)
        _doc(d, 720, 135, 170, k, (43, 87, 154), label="DOC", lines=6)
    elif motif == "edit":
        _doc(d, 470, 105, 220, k, accent, lines=7)
        d.line([700 * k, 230 * k, 610 * k, 390 * k], fill=(240, 180, 40), width=26 * k)
        d.polygon([(598 * k, 380 * k), (622 * k, 396 * k), (598 * k, 418 * k)], fill=(38, 41, 48))
    elif motif == "sign":
        _doc(d, 480, 100, 220, k, accent, lines=5)
        pts = [(520 + t * 1.6 + 14 * math.sin(t / 9), 382 - 22 * math.sin(t / 7) * math.cos(t / 23))
               for t in range(0, 101)]
        d.line([(x * k, y * k) for x, y in pts], fill=(30, 60, 140), width=4 * k, joint="curve")
        d.line([510 * k, 412 * k, 690 * k, 412 * k], fill=grey, width=3 * k)
    elif motif == "earnings":
        base = sy1 - 50
        for i, h in enumerate((70, 110, 95, 160, 215)):
            x = 380 + i * 82
            d.rounded_rectangle([x * k, (base - h) * k, (x + 56) * k, base * k], radius=8 * k,
                                fill=_tint(accent, 0.5 - 0.1 * i))
        d.line([360 * k, base * k, 800 * k, base * k], fill=grey, width=3 * k)
        d.ellipse([770 * k, 110 * k, 870 * k, 210 * k], fill=(240, 180, 40))
        d.ellipse([786 * k, 126 * k, 854 * k, 194 * k], outline=(255, 255, 255), width=5 * k)
    else:
        _doc(d, 495, 100, 210, k, accent, lines=7)
    return _webp(img.resize((width, height), Image.LANCZOS))


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


# ---------------------------------------------------------------- inline visuals
def _clean(fragment: str) -> str:
    text = re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()
    return re.sub(r"\s+([,.;:!?)])", r"\1", text)


SIZE_UNITS = {"kb": 1 / 1024, "mb": 1, "gb": 1024, "tb": 1024 * 1024}
VALUE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(KB|MB|GB|TB|%|percent\b|minutes?\b|hours?\b|days?\b|seconds?\b)",
                   re.I)


def _value(cell: str) -> tuple[float, str, str] | None:
    """(number, unit family, text as written) for the first quantity in a cell."""
    m = VALUE.search(cell)
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2).lower()
    if unit in SIZE_UNITS:
        return n * SIZE_UNITS[unit], "MB", f"{m.group(1)} {m.group(2).upper()}"
    unit = {"percent": "%"}.get(unit, unit.rstrip("s"))
    return n, unit, m.group(0)


def tables(content_html: str) -> list[dict]:
    """Every <table>: where it ends, the H2 above it, its header and its rows as plain text."""
    out = []
    for i, m in enumerate(re.finditer(r"<table\b.*?</table>", content_html, re.S | re.I)):
        h2s = re.findall(r"<h2[^>]*>(.*?)</h2>", content_html[:m.start()], re.S | re.I)
        rows = [[_clean(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", r, re.S | re.I)]
                for r in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(0), re.S | re.I)]
        head = rows[0] if rows and re.search(r"<th\b", m.group(0), re.I) else []
        out.append({"index": i, "end": m.end(), "heading": _clean(h2s[-1]) if h2s else "",
                    "header": head, "rows": rows[1:] if head else rows})
    return out


def chart_data(table: dict) -> dict | None:
    """A column worth charting: 3-8 rows, almost every row has a quantity, all in one unit
    family. The bars show only what the table already states."""
    rows, head = table["rows"], table["header"]
    if not head or not 3 <= len(rows) <= 8:
        return None
    for col in range(1, len(head)):
        vals = [(_clean(r[0]), _value(r[col])) for r in rows if len(r) > col]
        found = [(label, v) for label, v in vals if v]
        if len(found) >= 3 and len(found) >= 0.75 * len(rows) and len({v[1] for _, v in found}) == 1:
            return {"title": table["heading"] or head[col], "label_header": head[0], "value_header": head[col],
                    "unit": found[0][1][1], "bars": [{"label": label, "value": v[0], "text": v[2]} for label, v in found]}
    return None


def bar_chart(data: dict, pillar: str, width: int = 1200) -> tuple[bytes, int]:
    """A horizontal bar chart drawn at 2x and scaled down, so text and bars are smooth."""
    k = 2
    accent = PALETTES.get(pillar, PALETTES["money"])[0]
    track, ink, muted = (238, 240, 243), (28, 30, 34), (102, 108, 118)
    title_f, sub_f, label_f, value_f, foot_f = (_font(38 * k), _font(24 * k, bold=False), _font(23 * k, bold=False),
                                                _font(23 * k), _font(19 * k, bold=False))
    title = textwrap.wrap(data["title"], width=48)[:2]
    labels = []
    for b in data["bars"]:
        lines = textwrap.wrap(b["label"], width=32)
        labels.append(lines if len(lines) <= 3 else lines[:2] + [textwrap.shorten(" ".join(lines[2:]), 29, placeholder="...")])
    row_h = [max(64, 30 * len(ls) + 22) for ls in labels]
    top = 48 + 48 * len(title) + 52
    height = top + sum(row_h) + 120
    img = Image.new("RGB", (width * k, height * k), (255, 255, 255))
    d = ImageDraw.Draw(img)
    y = 48
    for line in title:
        d.text((56 * k, y * k), line, font=title_f, fill=ink)
        y += 48
    d.text((56 * k, (y + 8) * k), f"{data['value_header']} by {data['label_header'].lower()}", font=sub_f, fill=muted)
    left, right = 470, width - 150
    values = sorted(b["value"] for b in data["bars"])
    # One value far above the rest would flatten every other bar: cut that bar short, with a
    # visible break, and say so under the chart.
    broken = len(values) > 2 and values[-1] > 3 * values[-2] > 0
    top_value = (values[-2] * 1.25 if broken else values[-1]) or 1
    y = top
    for bar, lines, h in zip(data["bars"], labels, row_h):
        ty = y + (h - 30 * len(lines)) / 2
        for line in lines:
            d.text((56 * k, ty * k), line, font=label_f, fill=ink)
            ty += 30
        by = y + (h - 30) / 2
        d.rounded_rectangle([left * k, by * k, right * k, (by + 30) * k], radius=8 * k, fill=track)
        end = left + max(12, (right - left) * min(bar["value"], top_value) / top_value)
        d.rounded_rectangle([left * k, by * k, end * k, (by + 30) * k], radius=8 * k, fill=accent)
        if bar["value"] > top_value:
            for dx in (-34, -22):
                d.polygon([((end + dx) * k, (by + 34) * k), ((end + dx + 6) * k, (by + 34) * k),
                           ((end + dx + 16) * k, (by - 4) * k), ((end + dx + 10) * k, (by - 4) * k)],
                          fill=(255, 255, 255))
        d.text(((end + 12) * k, (by + 2) * k), bar["text"], font=value_f, fill=ink)
        y += h
    d.line([56 * k, (height - 88) * k, (width - 56) * k, (height - 88) * k], fill=track, width=2 * k)
    note = "Figures from the table in this article; each row links to its source."
    if broken:
        note = "Longest bar shortened to fit (see its value). " + note.replace("; each row", ". Each row")
    d.text((56 * k, (height - 72) * k), note, font=foot_f, fill=muted)
    d.text((56 * k, (height - 42) * k), "newearns.com", font=_font(19 * k), fill=accent)
    return _webp(img.resize((width, height), Image.LANCZOS)), height


def product_mentions(cfg: dict, content_html: str) -> list[dict]:
    """First link to each of our own products, with the page it points to (query string dropped)."""
    out, seen = [], set()
    for key, prod in (cfg.get("products") or {}).items():
        host = prod["url"].split("/")[2]
        m = re.search(rf'href="(https://{re.escape(host)}[^"?#]*)[^"]*"', content_html)
        if m and host not in seen:
            seen.add(host)
            out.append({"product": key, "name": prod["name"], "url": m.group(1), "at": m.start()})
    return sorted(out, key=lambda x: x["at"])


def screenshot(url: str, width: int = 1280, height: int = 800) -> tuple[bytes, str]:
    """The live page as a visitor sees it now (top of the page), and its title. Uses the
    runner's Chrome through Playwright; raises if the page doesn't load."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser, why = None, ""
        tries = [{"executable_path": os.environ["CHROME_PATH"]}] if os.environ.get("CHROME_PATH") else []
        for kw in tries + [{"channel": "chrome"}, {}]:
            try:
                browser = p.chromium.launch(**kw)
                break
            except Exception as e:  # no Chrome installed: try Playwright's own Chromium
                why = str(e)[:200]
        if browser is None:
            raise RuntimeError(f"no browser: {why}")
        try:
            page = browser.new_page(viewport={"width": width, "height": height}, locale="en-US")
            resp = page.goto(url, wait_until="networkidle", timeout=45000)
            if not resp or resp.status >= 400:
                raise RuntimeError(f"{url} returned {resp.status if resp else 'nothing'}")
            page.wait_for_timeout(1500)
            return page.screenshot(full_page=False), page.title()
        finally:
            browser.close()


def framed(png: bytes, width: int = 1200) -> tuple[bytes, int]:
    """Scale a screenshot to article width with a hairline border, so it reads as a screen."""
    img = Image.open(io.BytesIO(png)).convert("RGB")
    img = img.resize((width - 2, round(img.height * (width - 2) / img.width)), Image.LANCZOS)
    out = Image.new("RGB", (width, img.height + 2), (214, 218, 224))
    out.paste(img, (1, 1))
    return _webp(out), out.height


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
guide step steps explained vs new all any way own may one two off out see now our has had was why but per via
yes top""".split())


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
                                   "ObjectName|NonFree|Categories|DateTimeOriginal"})
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
                        "source": "wikimedia", "width": info.get("width") or 0, "height": info.get("height") or 0,
                        "year": int(y.group(0)) if (y := re.search(r"\b(19|20)\d\d\b",
                                                                   _clean(str(meta.get("DateTimeOriginal", ""))))) else 0})
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
    out = {w for w in words if len(w) >= 3 and w not in STOPWORDS}
    return out | {w[:-1] for w in out if w.endswith("s") and not w.endswith("ss")}


MIN_SCORE = 5
OFF_TOPIC = re.compile(r"\b(ceremony|president|minister|treaty|summit|parliament|military|army|burn(ed|t)|battery|"
                       r"heat pipe|hard drive|museum|historic(al)?|vintage|antique|protest|funeral)\b", re.I)
# Scenes that look "office-like" but are about something else: rejected unless the article's
# topic is that thing (a topic rule's `allow` list, e.g. signing for an e-signature article).
WRONG_SCENE = re.compile(r"\b(sign(s|ed|ing)?|signatures?|contracts?|agreements?|deals?|meetings?|conferences?|"
                         r"handshakes?|shaking hands|interviews?|books?|bookshelf|library|libraries|paperwork|"
                         r"newspapers?|letters?|envelopes?|notary|lawyers?|court|classroom|lecture|negotiations?|"
                         r"delegation|signatories)\b", re.I)
# Paper on a desk is a wrong scene too, unless the photo is of a document on a screen.
PAPER = re.compile(r"\b(papers?|documents?|files? folders?|binders?|printouts?)\b", re.I)
ON_SCREEN = re.compile(r"\b(pdf|on (the |a )?(screen|monitor|display|laptop|tablet)|digital)\b", re.I)
# Article words too broad to show that a photo is about this article.
BROAD = set("""business businesses document documents paper papers file files office work working online free best
easy quick fast ways tips simple make money people person computer laptop phone smartphone""".split())


def topic_for(cfg: dict, pillar: str, *texts: str) -> dict:
    """The first `images.topics` rule whose pattern matches the article's title, keyphrase or
    query; otherwise the pillar's default (motif only, no subject words)."""
    ic = cfg.get("images") or {}
    text = " ".join(t or "" for t in texts).lower()
    for rule in ic.get("topics") or []:
        if rule.get("pillars") and pillar not in rule["pillars"]:
            continue
        if re.search(rule["match"], text, re.I):
            return rule
    return {"motif": (ic.get("pillar_motif") or {}).get(pillar, "pdf")}


def _has(word: str, text: str) -> bool:
    return re.search(rf"\b{re.escape(word.lower())}\b", text) is not None


def relevance(c: dict, topic: dict, article_terms: set[str], must_show: list[str]) -> tuple[bool, str]:
    """Does the photo show this article's subject? (ok, why). It must not be a wrong scene
    (signing, meetings, books, paperwork...); its own title must name a device the task is done
    on (laptop, phone...), and it must mention the topic's subject words, or, with no topic rule,
    a specific word of the article rather than a broad one like "document" or "business"."""
    desc = c.get("description", "").lower()
    text = " ".join([desc, c.get("text", ""), *c.get("tags", [])]).lower()
    allow = {w.lower() for w in topic.get("allow") or []}
    wrong = [m.group(0).lower() for m in WRONG_SCENE.finditer(text) if m.group(0).lower() not in allow]
    paper = PAPER.search(text)
    if not wrong and paper and paper.group(0).lower() not in allow and not ON_SCREEN.search(text):
        wrong = [paper.group(0).lower()]
    paper = PAPER.search(text)
    if not wrong and paper and paper.group(0).lower() not in allow and not ON_SCREEN.search(text):
        wrong = [paper.group(0).lower()]
    if wrong:
        return False, f"wrong scene ({wrong[0]})"
    if must_show and not any(_has(w, desc) for w in must_show):
        return False, "no " + "/".join(must_show[:3]) + " in its title"
    subject = topic.get("subject") or sorted(article_terms - BROAD)
    hit = next((w for w in subject if _has(w, text)), None)
    if not hit:
        return False, "nothing about " + ", ".join(subject[:4])
    return True, f"shows {hit}"


def score(c: dict, article_terms: set[str], keywords: list[str]) -> int:
    """0 = not usable. A word counts double in the photo's own title and once in its description
    or tags; words of this article count three times as much as the pillar's scene words. It
    takes about two scene words in the title (a person and a laptop, say) to pass, so a photo of
    a single object, an old document or a political ceremony does not."""
    desc = c.get("description", "")
    rest = " ".join([c.get("text", ""), *c.get("tags", [])]).lower()
    text = f"{desc.lower()} {rest}"
    if (c.get("width", 0) < FEATURED_SIZE[0] or c.get("height", 0) < FEATURED_SIZE[1]
            or c["width"] < c["height"] or not str(c.get("image_url", "")).startswith("https://")
            or len(re.findall(r"[a-zA-Z]{2,}", desc)) < 2 or re.search(r"\b(img|dsc|dscn|p)[_ -]?\d{3,}", desc, re.I)
            or NOT_A_PHOTO.search(text) or OFF_TOPIC.search(text) or 0 < c.get("year", 0) < 2008):
        return 0
    in_title = terms(desc)
    total = sum(3 * (2 if t in in_title else 1) for t in (article_terms - BROAD) & terms(text))
    total += sum(2 if _has(k, desc.lower()) else 1 for k in keywords if _has(k, text))
    return total if total >= MIN_SCORE else 0


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
                   say=print, get=requests.get, topic: dict | None = None,
                   rejected: list | None = None) -> tuple[dict, bytes] | None:
    """Best-scoring photo with a clear licence that passes the relevance check, provider by
    provider. A search that fails twice is skipped; two failed searches in a row mark that
    provider as down for this run. Rejected candidates are listed in `rejected` with the reason."""
    ic = cfg.get("images") or {}
    keywords = (ic.get("photo_keywords") or {}).get(pillar, [])
    must_show = (ic.get("photo_must_show") or {}).get(pillar, [])
    topic = topic or {}
    rejected = rejected if rejected is not None else []
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
                ok, why = relevance(c, topic, article_terms, must_show)
                if not ok:
                    if len(rejected) < 12:
                        rejected.append({"description": c.get("description", "")[:90], "reason": why})
                    continue
                s = score(c, article_terms, keywords)
                if s:
                    ranked.append((-s, n, dict(c, relevance=why), q))
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


def inline_visuals(cfg: dict, article: dict, pillar: str, out_dir: Path, limit: int, say=print,
                   shoot=None) -> list[dict]:
    """Visuals that add something the text alone doesn't: a real, dated screenshot of our own
    product page where the article links it, then a chart of a table's figures. Nothing is
    made to fill a count, no third-party app screen is ever captured or imitated."""
    ic, html, slug, out = cfg.get("images") or {}, article["content_html"], article["slug"], []
    today = time.strftime("%Y-%m-%d", time.gmtime())
    if ic.get("product_screenshots", True):
        for m in product_mentions(cfg, html)[:1]:
            if len(out) >= limit:
                break
            try:
                png, title = (shoot or screenshot)(m["url"])
                data, height = framed(png)
            except Exception as e:
                say(f"Images: no screenshot of {m['url']} ({str(e)[:160]}).")
                continue
            n = len(out) + 1
            (out_dir / f"inline-{n}.webp").write_bytes(data)
            where = m["url"].split("//", 1)[1].rstrip("/")
            page = f' page "{title.strip()}"' if title.strip() else ""
            out.append({"provider": "in-house", "license": "own-screenshot", "kind": "screenshot",
                        "anchor": {"href": m["url"]}, "file": f"inline-{n}.webp", "url_shot": m["url"],
                        "filename": f"{slug}-{slugify(m['name'], 30)}-screenshot.webp", "width": 1200,
                        "height": height, "bytes": len(data), "captured": today,
                        "alt_text": _cap(f"Screenshot of the {m['name']}{page} at {where}"),
                        "caption": f"{m['name']} ({where}), screenshot taken {today}."})
    for t in tables(html):
        if len(out) >= limit:
            break
        data = chart_data(t)
        if not data:
            continue
        img, height = bar_chart(data, pillar)
        n = len(out) + 1
        (out_dir / f"inline-{n}.webp").write_bytes(img)
        out.append({"provider": "in-house", "license": "generated-in-house", "kind": "chart",
                    "anchor": {"table_index": t["index"]}, "file": f"inline-{n}.webp",
                    "filename": f"{slug}-chart-{slugify(data['title'], 40)}.webp", "width": 1200, "height": height,
                    "bytes": len(img), "heading": data["title"],
                    "alt_text": _cap(f"Bar chart of {data['value_header'].lower()} by {data['label_header'].lower()}: "
                                     + "; ".join(f"{b['label']} {b['text']}" for b in data["bars"])),
                    "caption": f"The figures from the table above, side by side."})
    return out


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
    topic = topic_for(cfg, pillar, article.get("title", ""), article.get("focus_keyphrase", ""), query or "")
    plan["topic"] = {"motif": topic["motif"], "match": topic.get("match", f"pillar {pillar}")}
    rejected: list = []
    if ic.get("stock_photos", True):
        search = search or StockSearch(ledger.DATA / "image_cache", pexels_key=os.environ.get("PEXELS_API_KEY"),
                                       cache_days=ic.get("cache_days", 30),
                                       licenses=ic.get("stock_licenses", ("cc0", "pdm", "by", "by-sa")),
                                       sources=ic.get("openverse_sources", ("stocksnap", "nappy", "flickr", "wikimedia")))
        scenes = topic.get("queries") if topic.get("match") else (ic.get("photo_queries") or {}).get(pillar, [])
        queries = list(dict.fromkeys(q for q in [query, article.get("focus_keyphrase"), *(scenes or [])] if q))
        article_terms = terms(query or "", article.get("focus_keyphrase", ""), article.get("title", ""))
        before, logged = search.requests, len(search.log)
        try:
            featured = featured_photo(cfg, pillar, queries, article_terms, search, say=say, get=get, topic=topic,
                                      rejected=rejected)
        except Exception as e:  # never let an image problem stop the article
            say(f"Images: stock photo search failed ({str(e)[:120]}).")
        plan["stock_api_calls"] = search.requests - before
        plan["photo_searches"] = search.log[logged:]
        plan["photo_rejections"] = rejected
    if featured:
        c, data = featured
        plan["featured"] = {k: c[k] for k in ("provider", "id", "description", "page_url", "creator", "creator_url",
                                              "license", "license_version", "license_url", "source", "query",
                                              "relevance")}
        plan["featured"].update(alt_text=_alt_from(c), caption="", filename=f"{slug}-featured.webp")
    else:
        motif = topic["motif"] if topic["motif"] in MOTIF_ALT else "pdf"
        try:
            data = topic_illustration(motif, pillar)
            plan["featured"] = {"provider": "in-house", "kind": "illustration", "motif": motif,
                                "license": "generated-in-house", "alt_text": MOTIF_ALT[motif], "caption": "",
                                "filename": f"{slug}-featured.webp", "fallback": True}
            plan["notes"].append(f"FALLBACK: no free, clearly licensed photo matched this article's topic, so the "
                                 f"featured image is an in-house illustration ({motif}). Replace it in WP Admin if "
                                 f"you find a better photo.")
            say(f"Images: no photo matched the topic; using the {motif} illustration.")
        except Exception as e:  # the title card is the very last resort
            headline = article.get("image_headline") or article["title"]
            data = title_card(headline, pillar)
            plan["featured"] = {"provider": "in-house", "kind": "title-card", "license": "generated-in-house",
                                "alt_text": alt_text(headline), "caption": "", "filename": f"{slug}-featured.webp",
                                "fallback": True}
            plan["notes"].append(f"FALLBACK: illustration failed ({str(e)[:80]}); using the title card.")
    (out_dir / "featured.webp").write_bytes(data)
    plan["featured"]["file"] = "featured.webp"
    plan["featured"]["bytes"] = len(data)

    plan["inline"] = inline_visuals(cfg, article, pillar, out_dir, min(hi - 1, ic.get("max_inline", 2)), say=say)
    if 1 + len(plan["inline"]) < lo:
        plan["notes"].append(f"{1 + len(plan['inline'])} image(s), below the {lo}-{hi} guideline: nothing else in "
                             "the article would gain from a visual, and decorative images are not added")
    return plan


# ---------------------------------------------------------------- insertion and checks
def figure(src: str, media_id: int, img: dict) -> str:
    e = htmllib.escape
    size = f' width="{img["width"]}" height="{img["height"]}"' if img.get("width") else ""
    cap = f'<figcaption class="wp-element-caption">{e(img["caption"])}</figcaption>' if img.get("caption") else ""
    return (f'<figure class="wp-block-image size-large"><img src="{e(src)}" alt="{e(img["alt_text"])}"{size} '
            f'class="wp-image-{media_id}" loading="lazy" decoding="async"/>{cap}</figure>')


def featured_figure(media: dict, plan: dict) -> str:
    """The featured image at the top of the post body, for themes that do not print it on the
    single post page (config images.featured_in_content). It is above the fold, so it is not
    lazy-loaded; WordPress adds srcset/sizes to the wp-image-<id> class when the page renders."""
    e = htmllib.escape
    f = plan["featured"]
    return (f'<figure class="wp-block-image size-large newearns-featured-image"><img src="{e(media["url"])}" '
            f'alt="{e(f["alt_text"])}" width="{FEATURED_SIZE[0]}" height="{FEATURED_SIZE[1]}" '
            f'class="wp-image-{media["id"]}" fetchpriority="high" decoding="async"/></figure>')


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


def _anchor_at(content_html: str, anchor: dict) -> int | None:
    """Where a visual goes: just after its table, or after the paragraph that links the product
    (and after the ownership disclosure that follows it)."""
    if "table_index" in anchor:
        t = [t for t in tables(content_html) if t["index"] == anchor["table_index"]]
        return t[0]["end"] if t else None
    m = re.search(rf'href="{re.escape(anchor["href"])}', content_html)
    if not m:
        return None
    ends = [i for i in (content_html.find("</p>", m.end()), content_html.find("</li>", m.end())) if i != -1]
    if not ends:
        return None
    at = min(ends)
    if content_html.startswith("</li>", at):  # inside a list: after the whole list
        close = re.search(r"</(ul|ol)>", content_html[at:])
        at = at + close.end() if close else at + 5
    else:
        at += 4
    nxt = re.match(r"\s*<p><em>Disclosure:.*?</p>", content_html[at:], re.S)
    return at + nxt.end() if nxt else at


def insert(content_html: str, plan: dict, uploaded: list[dict], featured: dict | None = None) -> str:
    """Each inline visual goes at its anchor; the photo credit goes last. With `featured` (the
    uploaded featured image), it goes first: below the title the theme prints, above the intro."""
    placed = [(0, featured_figure(featured, plan))] if featured and f'wp-image-{featured["id"]}"' not in content_html \
        else []
    for img, media in zip(plan.get("inline", []), uploaded):
        at = _anchor_at(content_html, img["anchor"])
        if at is not None:
            placed.append((at, figure(media["url"], media["id"], img)))
    out = content_html
    for at, fig in sorted(placed, key=lambda p: -p[0]):
        out = out[:at] + fig + out[at:]
    return out + credit_line(plan)


def check_in_post(post: dict, featured: dict, uploaded: list[dict], get=requests.get, *,
                  in_content: bool = False) -> list[str]:
    """Problems with the images in the saved post: featured image set, every inline image in the
    stored content, and every file served as an image. With `in_content`, the featured image must
    also be in the content exactly once, before the first paragraph."""
    problems = []
    if post.get("featured_media") != featured["id"]:
        problems.append(f"featured image is {post.get('featured_media')}, expected media {featured['id']}")
    content = (post.get("content") or {}).get("raw") or (post.get("content") or {}).get("rendered") or ""
    if in_content:
        at = [m.start() for m in re.finditer(rf'wp-image-{featured["id"]}"', content)]
        first_p = content.find("<p")
        if len(at) != 1:
            problems.append(f"featured image {featured['id']} is in the post content {len(at)} times, expected once")
        elif first_p != -1 and at[0] > first_p:
            problems.append(f"featured image {featured['id']} is not above the introduction")
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


def featured_on_page(page_url: str, media_url: str, get=requests.get) -> int:
    """How many images on the rendered article page show the featured file (any size WordPress
    made of it, lazy-loaded or not). 1 is right; 2 means the theme prints it as well."""
    r = get(page_url, timeout=30, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    stem = re.escape(media_url.rsplit("/", 1)[-1].rsplit(".", 1)[0])
    shows = re.compile(rf"/{stem}(?:-\d+x\d+)?\.\w+")
    page = re.sub(r"<noscript\b.*?</noscript>", "", r.text, flags=re.S | re.I)  # lazy-load fallbacks repeat each image
    return sum(1 for tag in re.findall(r"<img\b[^>]*>", page) if shows.search(tag))


def served_featured(wp, post: dict, local: bytes) -> tuple[dict, bytes]:
    """The featured image WordPress actually serves for the post, compared with the file we
    chose: (details, bytes)."""
    m = wp.get_media(post["featured_media"])
    data = wp.fetch_file(m["source_url"])
    details = m.get("media_details") or {}
    return {"media_id": m["id"], "url": m["source_url"], "alt_text": m.get("alt_text", ""),
            "mime": m.get("mime_type", ""), "width": details.get("width"), "height": details.get("height"),
            "bytes": len(data), "same_as_chosen": hashlib.sha256(data).digest() == hashlib.sha256(local).digest()}, data


def report(plan: dict) -> list[str]:
    f = plan["featured"]
    if f["provider"] == "in-house":
        src = (f"FALLBACK in-house illustration ({f.get('motif')}): no licensed photo matched the topic"
               if f.get("kind") == "illustration" else "FALLBACK title card (no suitable licensed photo)")
    else:
        name = LICENSE_NAMES[f["license"]] + (f" {f['license_version']}" if f.get("license_version") else "")
        src = (f"{f['provider']} photo by {f.get('creator') or 'unknown'}, {name}, {f.get('page_url')} "
               f"(query {f.get('query')!r}; {f.get('relevance', '')})")
    rows = [f"{plan['words']} words; guideline {plan['target'][0]}-{plan['target'][1]} images; "
            f"using {1 + len(plan['inline'])}. Image cost: $0 (free stock API calls this run: {plan['stock_api_calls']}).",
            f"- featured: {src}; alt: {f['alt_text']!r}; {f['bytes'] // 1024} KB"]
    for i in plan["inline"]:
        where = (f"after the table under {i['heading']!r}" if i["kind"] == "chart"
                 else f"after the paragraph linking {i['url_shot']}")
        rows.append(f"- {i['kind']} ({i['license']}) {where}; {i['width']}x{i['height']}; {i['bytes'] // 1024} KB")
    rows += [f"- search: {s['provider']} {s['query']!r} -> {s['result']}" for s in plan.get("photo_searches", [])]
    rows += [f"- rejected photo: {r['description']!r} ({r['reason']})" for r in plan.get("photo_rejections", [])]
    return rows + [f"- note: {n}" for n in plan["notes"]]
