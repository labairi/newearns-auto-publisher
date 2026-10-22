"""Validation of post files and account config, plus per-platform text rendering.

Nothing here talks to a network. A post that passes `validate_post` can still be refused by a
platform; the publisher reports that error as the platform returned it.
"""
from __future__ import annotations

import re
from typing import Any

from .rules import PLATFORMS
from .timeutil import TimeError, to_utc

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")
ACCOUNT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,39}$")
HTTPS_RE = re.compile(r"^https://[^\s]+$")
TOP_KEYS = {"id", "title", "campaign", "hook", "headline", "description", "primary", "alt", "cta",
            "hashtags", "intent", "scheduled", "targets", "versions", "media", "settings",
            "created_at", "updated_at", "notes"}
INTENTS = ("draft", "scheduled")


def hashtagify(t: str) -> str:
    t = re.sub(r"[^\w]", "", str(t), flags=re.UNICODE)
    return "#" + t if t else ""


def derived_text(post: dict, platform: str) -> tuple[str, str]:
    """(title, text) for one platform: the person's own version if there is one, otherwise built
    from the shared copy and fitted to the platform's limit. Never truncates mid-sentence."""
    r = PLATFORMS[platform]
    v = (post.get("versions") or {}).get(platform) or {}
    title = (v.get("title") or "").strip()
    if v.get("text") is not None and str(v.get("text")).strip():
        return title, str(v["text"]).strip()
    primary = (post.get("primary") or post.get("alt") or "").strip()
    cta = (post.get("cta") or "").strip()
    tags = " ".join(h for h in (hashtagify(t) for t in (post.get("hashtags") or [])) if h)
    short = (post.get("alt") or primary).strip()
    hook = ((post.get("hook") or "") + " " + (post.get("headline") or "")).strip()
    join = lambda *p: "\n\n".join(x for x in p if x)  # noqa: E731
    one = lambda *p: " ".join(x for x in p if x)      # noqa: E731
    cands = [join(primary, cta, tags), join(primary, cta), join(short, cta, tags), join(short, cta),
             one(hook, cta), short, primary, hook]
    cands = [c for c in cands if c]
    if platform == "youtube" and not title:
        title = (post.get("headline") or post.get("title") or "").strip()[: r.get("title_max", 100)]
    for c in cands:
        if len(c) <= r["max_chars"]:
            return title, c
    return title, cands[-1] if cands else ""


def media_kinds(post: dict) -> tuple[int, int]:
    imgs = sum(1 for m in post.get("media") or [] if m.get("type") == "image")
    vids = sum(1 for m in post.get("media") or [] if m.get("type") == "video")
    return imgs, vids


def validate_post(post: Any, accounts: dict[str, dict], *, for_schedule: bool | None = None) -> list[str]:
    """Return a list of problems (empty list = valid). `accounts` maps account id -> account dict."""
    errs: list[str] = []
    if not isinstance(post, dict):
        return ["the post file must be a JSON object"]
    unknown = sorted(set(post) - TOP_KEYS)
    if unknown:
        errs.append("unknown field(s): " + ", ".join(unknown))
    pid = post.get("id")
    if not isinstance(pid, str) or not ID_RE.match(pid):
        errs.append("id must be 3 to 80 characters: lowercase letters, digits and hyphens")
    intent = post.get("intent", "draft")
    if intent not in INTENTS:
        errs.append("intent must be 'draft' or 'scheduled'")
    scheduling = (intent == "scheduled") if for_schedule is None else for_schedule
    sch = post.get("scheduled")
    if scheduling:
        if not isinstance(sch, dict) or not sch.get("local") or not sch.get("tz"):
            errs.append("a scheduled post needs scheduled.local (YYYY-MM-DDTHH:MM) and scheduled.tz (for example Africa/Casablanca)")
        else:
            try:
                to_utc(sch["local"], sch["tz"])
            except TimeError as e:
                errs.append(str(e))
    for k in ("title", "campaign", "hook", "headline", "description", "primary", "alt", "cta"):
        if k in post and not isinstance(post[k], str):
            errs.append(f"{k} must be text")
    if "hashtags" in post and not (isinstance(post["hashtags"], list) and all(isinstance(x, str) for x in post["hashtags"])):
        errs.append("hashtags must be a list of text")
    media = post.get("media") or []
    if not isinstance(media, list):
        errs.append("media must be a list")
        media = []
    for i, m in enumerate(media):
        if not isinstance(m, dict) or m.get("type") not in ("image", "video"):
            errs.append(f"media[{i}].type must be 'image' or 'video'")
            continue
        if not isinstance(m.get("url"), str) or not HTTPS_RE.match(m["url"]):
            errs.append(f"media[{i}].url must be a public https address")
    targets = post.get("targets") or []
    if not isinstance(targets, list) or not targets:
        errs.append("choose at least one target account")
        targets = []
    seen: set[str] = set()
    imgs, vids = media_kinds(post) if isinstance(media, list) else (0, 0)
    for t in targets:
        acc_id = t.get("account") if isinstance(t, dict) else None
        if not acc_id or acc_id not in accounts:
            errs.append(f"unknown account: {acc_id!r}")
            continue
        if acc_id in seen:
            errs.append(f"account listed twice: {acc_id}")
            continue
        seen.add(acc_id)
        plat = accounts[acc_id].get("platform")
        r = PLATFORMS.get(plat)
        if not r:
            errs.append(f"{acc_id}: unknown platform {plat!r}")
            continue
        label = f"{r['label']} ({acc_id})"
        if r["status"] != "implemented":
            errs.append(f"{label}: publishing to {r['label']} is not built yet. {r.get('why', '')}".strip())
            continue
        title, text = derived_text(post, plat)
        if len(text) > r["max_chars"]:
            errs.append(f"{label}: the text is {len(text)} characters, over the {r['max_chars']} limit")
        if r["requires_media"] and not (imgs or vids):
            errs.append(f"{label}: needs an image or a video")
        if r["requires_video"] and not vids:
            errs.append(f"{label}: needs a video")
        if vids and not r["video"]:
            errs.append(f"{label}: does not take video here")
        if imgs and not r["image"]:
            errs.append(f"{label}: does not take images")
        if r.get("image_ext"):
            for m in post.get("media") or []:
                if m.get("type") == "image" and isinstance(m.get("url"), str):
                    ext = m["url"].split("?")[0].rsplit(".", 1)[-1].lower()
                    if ext not in r["image_ext"]:
                        errs.append(f"{label}: images must be {'/'.join(r['image_ext']).upper()} (this one is .{ext})")
        if imgs > r["max_images"] and r["max_images"] > 0:
            errs.append(f"{label}: takes at most {r['max_images']} image(s), the post has {imgs}")
        if vids > 1:
            errs.append(f"{label}: takes one video, the post has {vids}")
        if vids and imgs:
            errs.append(f"{label}: cannot mix a video and images")
        if r["title_required"] and not title:
            errs.append(f"{label}: a title is required")
        if not text and not (imgs or vids):
            errs.append(f"{label}: there is nothing to post (no text, no media)")
    return errs


def validate_accounts(cfg: Any) -> list[str]:
    errs: list[str] = []
    if not isinstance(cfg, dict) or not isinstance(cfg.get("accounts"), list):
        return ["config/accounts.json needs an 'accounts' list"]
    seen = set()
    for a in cfg["accounts"]:
        if not isinstance(a, dict) or not ACCOUNT_RE.match(str(a.get("id", ""))):
            errs.append(f"bad account id: {a.get('id') if isinstance(a, dict) else a!r}")
            continue
        if a["id"] in seen:
            errs.append(f"duplicate account id: {a['id']}")
        seen.add(a["id"])
        if a.get("platform") not in PLATFORMS:
            errs.append(f"{a['id']}: unknown platform {a.get('platform')!r}")
        env = a.get("secrets") or {}
        if not isinstance(env, dict) or any(not isinstance(v, str) for v in env.values()):
            errs.append(f"{a['id']}: 'secrets' must map names to secret key names, never to the secret values")
        for k, v in env.items():
            if re.search(r"[A-Za-z0-9_\-]{40,}", v):
                errs.append(f"{a['id']}: secrets.{k} looks like a real secret value. Put the NAME of the key in SOCIAL_SECRETS here, not the value")
    return errs
