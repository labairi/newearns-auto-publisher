"""In-memory platform for tests and dry demos. Never touches a network."""
from __future__ import annotations

from ..errors import PublishError
from .base import Adapter, PublishRequest, PublishResult

STORE: dict[str, list] = {}   # account id -> posts "published" on the fake platform
SCRIPT: dict[str, list] = {}  # account id -> queued behaviours: "ok" | "transient" | "permanent" | "uncertain" | "uncertain_created"


class MockAdapter(Adapter):
    platform = "mock"

    def verify(self) -> dict:
        return {"ok": True, "identity": "mock:" + self.account["id"]}

    def publish(self, req: PublishRequest) -> PublishResult:
        acc = self.account["id"]
        behaviour = (SCRIPT.get(acc) or ["ok"]).pop(0) if SCRIPT.get(acc) else "ok"
        posts = STORE.setdefault(acc, [])
        if behaviour == "transient":
            raise PublishError("transient", "mock: rate limited", 429)
        if behaviour == "permanent":
            raise PublishError("permanent", "mock: token rejected", 401)
        if behaviour in ("uncertain", "uncertain_created"):
            if behaviour == "uncertain_created":
                posts.append({"id": f"m{len(posts) + 1}", "text": req.text, "attempt": req.attempt_id, "post_id": req.post_id})
            raise PublishError("uncertain", "mock: timed out waiting for the answer")
        pid = f"m{len(posts) + 1}"
        posts.append({"id": pid, "text": req.text, "attempt": req.attempt_id, "post_id": req.post_id})
        return PublishResult(pid, f"https://mock.example/{acc}/{pid}")

    def find_existing(self, req: PublishRequest):
        for p in STORE.get(self.account["id"], []):
            if p["post_id"] == req.post_id:
                return PublishResult(p["id"], f"https://mock.example/{self.account['id']}/{p['id']}")
        return None
