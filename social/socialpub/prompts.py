"""Builds the text you paste into the AI assistant you already pay for (Claude, ChatGPT, ...)."""
from __future__ import annotations

import json

from .rules import PLATFORMS


def build_prompt(brand: dict, accounts: dict[str, dict], request: str, count: int = 3) -> str:
    plat = sorted({a["platform"] for a in accounts.values() if PLATFORMS.get(a["platform"], {}).get("status") == "implemented"})
    limits = "\n".join(f"- {PLATFORMS[p]['label']} ({p}): at most {PLATFORMS[p]['max_chars']} characters" for p in plat)
    acc = "\n".join(f"- {a['id']} ({a['platform']})" for a in accounts.values() if a["platform"] in plat)
    return f"""You are writing social media posts for a brand. Return ONLY JSON, nothing else.

Brand: {brand.get('name', '')}
Website: {brand.get('website', '')}
Voice: {brand.get('voice', 'clear, friendly, no hype')}
Audience: {brand.get('audience', '')}
Facts you may use (use nothing else; do not invent features, prices, numbers or testimonials):
{chr(10).join('- ' + f for f in brand.get('facts', [])) or '- (none given)'}
Never say: {', '.join(brand.get('avoid', [])) or '(nothing listed)'}

Task: {request}
Write {count} post(s).

Accounts you can target (use these ids in "targets"):
{acc}

Platform limits (the version for each platform must fit):
{limits}

Output shape (a JSON list). Fields marked optional can be left out:
[{{
  "title": "short internal name",
  "campaign": "optional",
  "primary": "the main caption",
  "alt": "optional shorter caption for X-style platforms",
  "cta": "optional call to action, e.g. Try it at example.com",
  "hashtags": ["optional", "no hash sign needed"],
  "targets": [{{"account": "<an account id from above>"}}],
  "versions": {{"<platform id>": {{"text": "optional hand-written version for that platform"}}}},
  "media": [{{"type": "image", "url": "https://public-address", "alt": "describe the image"}}]
}}]
Do not include dates: scheduling is done in the dashboard. Do not include an "id".
"""


def example_post() -> str:
    return json.dumps({"title": "Example", "primary": "Hello", "targets": [{"account": "my-bluesky"}]}, indent=2)
