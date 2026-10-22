import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from socialpub.platforms import mock as mockmod  # noqa: E402
from socialpub.store import Store  # noqa: E402

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)   # 21:00 in Tokyo; the default post (10:30 Tokyo) was due 10.5 hours ago


class Resp:
    def __init__(self, status=200, body=None, content=b""):
        self.status_code, self._b, self.content = status, body if body is not None else {}, content
        self.text = json.dumps(self._b)

    def json(self):
        return self._b


class FakeHttp:
    """Routes: list of (method, url substring, handler). handler is a Resp, an Exception, or a callable(kw)->Resp."""

    def __init__(self, routes=()):
        self.routes, self.calls = list(routes), []

    def add(self, method, frag, handler):
        self.routes.append((method, frag, handler))
        return self

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        for m, frag, h in self.routes:
            if m == method and frag in url:
                if isinstance(h, list):
                    h = h.pop(0) if len(h) > 1 else h[0]
                if isinstance(h, Exception):
                    raise h
                return h(kw) if callable(h) else h
        raise AssertionError(f"unexpected request {method} {url}")

    def to(self, frag):
        return [c for c in self.calls if frag in c[1]]


@pytest.fixture(autouse=True)
def _reset_mock():
    mockmod.STORE.clear(); mockmod.SCRIPT.clear()


def make_store(tmp_path, accounts=None, settings=None):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    accounts = accounts if accounts is not None else [{"id": "m1", "platform": "mock"}, {"id": "m2", "platform": "mock"}]
    (tmp_path / "config" / "accounts.json").write_text(json.dumps({"accounts": accounts}))
    if settings:
        (tmp_path / "config" / "settings.json").write_text(json.dumps(settings))
    return Store(tmp_path)


def post(pid="hello-post", accounts=("m1",), local="2026-10-25T10:30", tz="Asia/Tokyo", **kw):
    p = {"id": pid, "title": "t", "primary": "Hello world", "intent": "scheduled",
         "scheduled": {"local": local, "tz": tz}, "targets": [{"account": a} for a in accounts]}
    p.update(kw)
    return p


@pytest.fixture
def store(tmp_path):
    return make_store(tmp_path)


@pytest.fixture
def now():
    return NOW
