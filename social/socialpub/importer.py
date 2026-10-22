"""Import posts prepared by an AI assistant you already use (no paid API needed).

The assistant is given the prompt from `prompts.build_prompt`, and answers with JSON. This module
turns that JSON into draft post files after validating every one. Nothing is scheduled or published
by importing: imported posts are always drafts.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .store import Store
from .timeutil import iso, utcnow
from .validate import TOP_KEYS, validate_post

FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_ai_output(text: str) -> list[dict]:
    t = FENCE.sub("", text.strip())
    start = min([i for i in (t.find("["), t.find("{")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("no JSON found in the pasted text")
    data = json.loads(t[start:])
    if isinstance(data, dict) and "posts" in data:
        data = data["posts"]
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
        raise ValueError("expected a post, a list of posts, or {\"posts\": [...]}")
    return data


def slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return (s or "post")[:60].strip("-") or "post"


def import_posts(store: Store, text: str) -> dict[str, Any]:
    """Returns {"saved": [ids], "rejected": [{"index", "errors"}]}. Existing posts are never overwritten."""
    accounts = store.accounts()
    saved, rejected = [], []
    for i, raw in enumerate(parse_ai_output(text)):
        p = {k: v for k, v in raw.items() if k in TOP_KEYS}
        dropped = sorted(set(raw) - TOP_KEYS)
        p.setdefault("id", slug(str(p.get("title", "")) + "-" + iso(utcnow())[:10].replace("-", "")))
        p["intent"] = "draft"                      # an import never schedules
        p.pop("scheduled", None)
        base, n = p["id"], 1
        while p["id"] in store.post_ids():
            n += 1
            p["id"] = f"{base}-{n}"
        errs = validate_post(p, accounts, for_schedule=False)
        if errs:
            rejected.append({"index": i, "title": p.get("title", ""), "errors": errs})
            continue
        store.save_post(p)
        store.log("imported", p["id"], dropped_fields=",".join(dropped))
        saved.append(p["id"])
    return {"saved": saved, "rejected": rejected}
