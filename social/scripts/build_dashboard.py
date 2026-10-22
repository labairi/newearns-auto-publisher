#!/usr/bin/env python3
"""Copies the platform rules from socialpub/rules.py into the dashboard, so the page and the publisher
cannot disagree about limits. Run after editing rules.py:  python scripts/build_dashboard.py
tests/test_dashboard_sync.py fails if this was forgotten."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from socialpub.rules import PLATFORMS  # noqa: E402

KEYS = ("label", "status", "max_chars", "max_images", "video", "image", "requires_media", "requires_video",
        "title_required", "title_max", "image_ext", "why", "daily_cap")


def rules_js() -> str:
    data = {k: {f: v[f] for f in KEYS if f in v and v[f] is not None} for k, v in PLATFORMS.items() if k != "mock"}
    data = {k: {f: (list(x) if isinstance(x, tuple) else x) for f, x in v.items()} for k, v in data.items()}
    return "/*RULES*/const RULES = " + json.dumps(data, sort_keys=True, ensure_ascii=False) + ";/*END*/"


def main() -> int:
    p = ROOT / "dashboard" / "index.html"
    s = p.read_text(encoding="utf-8")
    new, n = re.subn(r"/\*RULES\*/.*?/\*END\*/", lambda m: rules_js(), s, count=1, flags=re.S)
    if n != 1:
        print("marker not found"); return 1
    p.write_text(new, encoding="utf-8")
    print("dashboard rules updated"); return 0


if __name__ == "__main__":
    sys.exit(main())
