"""Per-platform publishing rules.

Limits come from each platform's official API documentation (checked 2026-10-22, see
docs/PLATFORMS.md). `status` says how far THIS project's adapter goes:

  implemented      the adapter exists and is covered by mocked-API tests. None of them has
                   been run against a live account yet; run `check-setup` first.
  not_implemented  documented but there is no adapter. A post that targets one is rejected
                   when it is scheduled, never silently skipped.
"""
from __future__ import annotations

PLATFORMS: dict[str, dict] = {
    "bluesky": {
        "label": "Bluesky", "status": "implemented", "max_chars": 300, "max_images": 4,
        "video": False, "image": True, "requires_media": False, "requires_video": False,
        "title_required": False, "daily_cap": None,
    },
    "mastodon": {
        "label": "Mastodon", "status": "implemented", "max_chars": 500, "max_images": 4,
        "video": True, "image": True, "requires_media": False, "requires_video": False,
        "title_required": False, "daily_cap": None,
    },
    "threads": {
        "label": "Threads", "status": "implemented", "max_chars": 500, "max_images": 1,
        "video": True, "image": True, "requires_media": False, "requires_video": False,
        "title_required": False, "daily_cap": 250,
    },
    "facebook_page": {
        "label": "Facebook Page", "status": "implemented", "max_chars": 63206, "max_images": 1,
        "video": True, "image": True, "requires_media": False, "requires_video": False,
        "title_required": False, "daily_cap": None,
    },
    "instagram": {
        "label": "Instagram", "status": "implemented", "max_chars": 2200, "max_images": 1,
        "video": True, "image": True, "requires_media": True, "requires_video": False,
        "title_required": False, "daily_cap": 100,
        "image_ext": ("jpg", "jpeg"),
    },
    "mock": {
        "label": "Mock (tests and demos)", "status": "implemented", "max_chars": 1000, "max_images": 4,
        "video": True, "image": True, "requires_media": False, "requires_video": False,
        "title_required": False, "daily_cap": None,
    },
    # ---- documented, not built (see docs/PLATFORMS.md for what each one needs) ----
    "x": {
        "label": "X", "status": "not_implemented", "max_chars": 280, "max_images": 4,
        "video": True, "image": True, "requires_media": False, "requires_video": False,
        "title_required": False, "daily_cap": None,
        "why": "X has no free posting tier for new developers; posting is pay-per-use.",
    },
    "linkedin": {
        "label": "LinkedIn", "status": "not_implemented", "max_chars": 3000, "max_images": 20,
        "video": True, "image": True, "requires_media": False, "requires_video": False,
        "title_required": False, "daily_cap": None,
        "why": "Needs the LinkedIn 'Share on LinkedIn' product; company pages need Community Management API approval.",
    },
    "linkedin_page": {
        "label": "LinkedIn Page", "status": "not_implemented", "max_chars": 3000, "max_images": 20,
        "video": True, "image": True, "requires_media": False, "requires_video": False,
        "title_required": False, "daily_cap": None,
        "why": "Company pages need Community Management API approval (about 1 to 2 weeks).",
    },
    "tiktok": {
        "label": "TikTok", "status": "not_implemented", "max_chars": 2200, "max_images": 0,
        "video": True, "image": False, "requires_media": True, "requires_video": True,
        "title_required": False, "daily_cap": None,
        "why": "Posts from an unaudited app are private-only until TikTok audits the app.",
    },
    "youtube": {
        "label": "YouTube", "status": "not_implemented", "max_chars": 5000, "max_images": 0,
        "video": True, "image": False, "requires_media": True, "requires_video": True,
        "title_required": True, "title_max": 100, "daily_cap": None,
        "why": "Needs a Google Cloud project with OAuth consent; check the upload quota and visibility rules for unverified projects.",
    },
}

DEFAULT_MAX_ATTEMPTS = 3
BACKOFF_MINUTES = (15, 60, 240)   # wait before attempt 2, 3, 4
STUCK_PUBLISHING_MINUTES = 30      # a target left "publishing" longer than this is treated as uncertain


def rule(platform: str) -> dict:
    if platform not in PLATFORMS:
        raise KeyError(f"unknown platform: {platform}")
    return PLATFORMS[platform]
