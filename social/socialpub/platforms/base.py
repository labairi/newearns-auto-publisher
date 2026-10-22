from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from ..errors import PublishError


@dataclass
class PublishRequest:
    post_id: str
    account_id: str
    attempt_id: str
    text: str
    title: str = ""
    media: list = field(default_factory=list)       # [{"type","url","alt"}]
    settings: dict = field(default_factory=dict)
    since_iso: str = ""                              # for find_existing: only look at posts after this


@dataclass
class PublishResult:
    provider_id: str
    url: str = ""


class Adapter:
    platform = ""
    needs = ()   # secret roles this adapter reads from the account's 'secrets' map

    def __init__(self, account: dict, secrets: dict[str, str], http, sleep=time.sleep):
        self.account = account
        self.s = secrets
        self.http = http
        self.sleep = sleep

    def secret(self, role: str) -> str:
        v = self.s.get(role, "")
        if not v:
            name = (self.account.get("secrets") or {}).get(role, role)
            raise PublishError("permanent", f"missing secret '{name}' for account {self.account['id']} (role {role})")
        return v

    def verify(self) -> dict:                       # read-only identity check, never posts
        raise NotImplementedError

    def publish(self, req: PublishRequest) -> PublishResult:
        raise NotImplementedError

    def find_existing(self, req: PublishRequest):   # PublishResult | None
        return None


URL_RE = re.compile(r"https?://[^\s]+")
TAG_RE = re.compile(r"(?<!\w)#(\w+)", re.UNICODE)
