from __future__ import annotations

from ..errors import PublishError
from .bluesky import BlueskyAdapter
from .facebook import FacebookAdapter
from .instagram import InstagramAdapter
from .mastodon import MastodonAdapter
from .mock import MockAdapter
from .threads import ThreadsAdapter

REGISTRY = {"bluesky": BlueskyAdapter, "mastodon": MastodonAdapter, "threads": ThreadsAdapter,
            "facebook_page": FacebookAdapter, "instagram": InstagramAdapter, "mock": MockAdapter}


def build_adapter(account: dict, secrets: dict[str, str], http, sleep=None):
    cls = REGISTRY.get(account.get("platform"))
    if not cls:
        raise PublishError("permanent", f"publishing to {account.get('platform')} is not built yet")
    resolved = {role: secrets.get(name, "") for role, name in (account.get("secrets") or {}).items()}
    kw = {"sleep": sleep} if sleep else {}
    return cls(account, resolved, http, **kw)
