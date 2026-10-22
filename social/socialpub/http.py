"""One thin HTTP layer so every adapter classifies failures the same way, and tests can swap it out."""
from __future__ import annotations

import requests

from .errors import PublishError

TIMEOUT = (10, 60)  # connect, read seconds


class Http:
    def request(self, method: str, url: str, **kw):
        kw.setdefault("timeout", TIMEOUT)
        return requests.request(method, url, **kw)


def call(http, method: str, url: str, *, create: bool = False, what: str = "request", **kw):
    """Make a request and turn failures into PublishError.

    create=True marks the one call whose success creates the public post. If that call times out or
    returns 5xx we cannot know whether the post exists, so the failure is 'uncertain'.
    """
    try:
        r = http.request(method, url, **kw)
    except requests.exceptions.ConnectTimeout as e:
        raise PublishError("transient", f"{what}: could not connect ({type(e).__name__})") from e
    except requests.exceptions.ConnectionError as e:
        raise PublishError("transient", f"{what}: connection failed ({type(e).__name__})") from e
    except requests.exceptions.Timeout as e:
        raise PublishError("uncertain" if create else "transient", f"{what}: timed out waiting for the answer") from e
    except requests.exceptions.RequestException as e:
        raise PublishError("uncertain" if create else "transient", f"{what}: {type(e).__name__}") from e
    code = r.status_code
    if 200 <= code < 300:
        return r
    body = ""
    try:
        body = r.text[:400]
    except Exception:  # pragma: no cover
        pass
    if code == 429:
        raise PublishError("transient", f"{what}: rate limited (HTTP 429). {body}", code)
    if code >= 500:
        raise PublishError("uncertain" if create else "transient", f"{what}: platform error HTTP {code}. {body}", code)
    raise PublishError("permanent", f"{what}: rejected (HTTP {code}). {body}", code)
