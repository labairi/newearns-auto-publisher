"""Featured image: a branded title card drawn in-house.

No stock photos, no AI image service, so there is no licensing risk and no
extra cost. Swap in Pexels (free licence, attribution optional) later if wanted.
"""
from __future__ import annotations

import io
import textwrap

from PIL import Image, ImageDraw, ImageFont

PALETTES = {
    "pdf": ((178, 34, 34), (255, 255, 255)),
    "instagram": ((131, 58, 180), (255, 255, 255)),
    "money": ((20, 110, 80), (255, 255, 255)),
}
FONT_CANDIDATES = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                   "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf"]


def _font(size: int):
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


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
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=85)
    return buf.getvalue()
