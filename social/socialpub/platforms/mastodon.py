"""Mastodon. Free; create an application on your instance (Preferences > Development) with the
scopes write:statuses and write:media, and use its access token.

Posting sends an Idempotency-Key header, so if a retry reaches the server twice within its window
(about an hour) it returns the first post instead of making a second one.
"""
from __future__ import annotations

import hashlib
import mimetypes

from ..errors import PublishError
from ..http import call
from .base import Adapter, PublishRequest, PublishResult


class MastodonAdapter(Adapter):
    platform = "mastodon"
    needs = ("access_token",)

    def _base(self) -> str:
        inst = (self.account.get("instance") or "").rstrip("/")
        if not inst.startswith("https://"):
            raise PublishError("permanent", "Mastodon: account.instance must be an https address such as https://mastodon.social")
        return inst

    def _h(self):
        return {"Authorization": "Bearer " + self.secret("access_token")}

    def verify(self) -> dict:
        r = call(self.http, "GET", self._base() + "/api/v1/accounts/verify_credentials", what="Mastodon identity", headers=self._h()).json()
        return {"ok": True, "identity": r.get("acct")}

    def publish(self, req: PublishRequest) -> PublishResult:
        base, h = self._base(), self._h()
        ids = []
        for m in req.media:
            data = call(self.http, "GET", m["url"], what="download media").content
            mime = mimetypes.guess_type(m["url"].split("?")[0])[0] or "application/octet-stream"
            r = call(self.http, "POST", base + "/api/v2/media", what="Mastodon media upload", headers=h,
                     files={"file": (m["url"].rsplit("/", 1)[-1].split("?")[0] or "file", data, mime)},
                     data={"description": m.get("alt", "")})
            mid = r.json()["id"]
            if r.status_code == 202:                       # still processing (video)
                for _ in range(20):
                    self.sleep(3)
                    g = call(self.http, "GET", f"{base}/api/v1/media/{mid}", what="Mastodon media status", headers=h)
                    if g.status_code == 200:
                        break
                else:
                    raise PublishError("transient", "Mastodon: media still processing after 60 s")
            ids.append(mid)
        key = hashlib.sha256(f"{req.post_id}:{self.account['id']}".encode()).hexdigest()[:40]
        body = {"status": req.text, "visibility": self.account.get("visibility", "public")}
        if ids:
            body["media_ids[]"] = ids
        r = call(self.http, "POST", base + "/api/v1/statuses", create=True, what="Mastodon post",
                 headers={**h, "Idempotency-Key": key}, data=body).json()
        return PublishResult(str(r["id"]), r.get("url", ""))
