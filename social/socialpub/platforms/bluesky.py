"""Bluesky (AT Protocol). Free, no app review. Needs the account handle and an APP PASSWORD
(Settings > Privacy and security > App passwords), never the main password.

Video is not supported here (Bluesky video needs a separate upload service); posts with video are
rejected at validation time.
"""
from __future__ import annotations

import mimetypes
from datetime import datetime, timezone

from ..errors import PublishError
from ..http import call
from .base import TAG_RE, URL_RE, Adapter, PublishRequest, PublishResult

HOST = "https://bsky.social"


def facets(text: str) -> list[dict]:
    """Link and hashtag facets. Bluesky needs UTF-8 BYTE offsets, not character offsets."""
    out = []
    b = text.encode("utf-8")
    for m in URL_RE.finditer(text):
        url = m.group(0).rstrip(".,;:!?)\"'")
        start = len(text[: m.start()].encode("utf-8"))
        out.append({"index": {"byteStart": start, "byteEnd": start + len(url.encode("utf-8"))},
                    "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}]})
    for m in TAG_RE.finditer(text):
        start = len(text[: m.start()].encode("utf-8"))
        tag = m.group(0)
        out.append({"index": {"byteStart": start, "byteEnd": start + len(tag.encode("utf-8"))},
                    "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": m.group(1)}]})
    assert all(f["index"]["byteEnd"] <= len(b) for f in out)
    return out


class BlueskyAdapter(Adapter):
    platform = "bluesky"
    needs = ("identifier", "app_password")

    def _session(self) -> dict:
        r = call(self.http, "POST", f"{HOST}/xrpc/com.atproto.server.createSession", what="Bluesky login",
                 json={"identifier": self.secret("identifier"), "password": self.secret("app_password")})
        return r.json()

    def verify(self) -> dict:
        s = self._session()
        return {"ok": True, "identity": s.get("handle"), "did": s.get("did")}

    def _headers(self, s):
        return {"Authorization": "Bearer " + s["accessJwt"]}

    def publish(self, req: PublishRequest) -> PublishResult:
        s = self._session()
        h = self._headers(s)
        embed = None
        images = []
        for m in req.media:
            if m["type"] != "image":
                raise PublishError("permanent", "Bluesky: only images are supported here")
            data = call(self.http, "GET", m["url"], what="download image").content
            mime = mimetypes.guess_type(m["url"].split("?")[0])[0] or "image/jpeg"
            up = call(self.http, "POST", f"{HOST}/xrpc/com.atproto.repo.uploadBlob", what="Bluesky image upload",
                      headers={**h, "Content-Type": mime}, data=data).json()
            images.append({"alt": m.get("alt", ""), "image": up["blob"]})
        if images:
            embed = {"$type": "app.bsky.embed.images", "images": images}
        record = {"$type": "app.bsky.feed.post", "text": req.text, "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                  "facets": facets(req.text)}
        if embed:
            record["embed"] = embed
        r = call(self.http, "POST", f"{HOST}/xrpc/com.atproto.repo.createRecord", create=True, what="Bluesky post",
                 headers=h, json={"repo": s["did"], "collection": "app.bsky.feed.post", "record": record}).json()
        rkey = r["uri"].rsplit("/", 1)[-1]
        return PublishResult(r["uri"], f"https://bsky.app/profile/{s.get('handle', s['did'])}/post/{rkey}")

    def find_existing(self, req: PublishRequest):
        s = self._session()
        r = call(self.http, "GET", f"{HOST}/xrpc/com.atproto.repo.listRecords", what="Bluesky list posts",
                 headers=self._headers(s), params={"repo": s["did"], "collection": "app.bsky.feed.post", "limit": 25}).json()
        for rec in r.get("records", []):
            v = rec.get("value", {})
            if v.get("text") == req.text and (not req.since_iso or v.get("createdAt", "") >= req.since_iso):
                rkey = rec["uri"].rsplit("/", 1)[-1]
                return PublishResult(rec["uri"], f"https://bsky.app/profile/{s.get('handle', s['did'])}/post/{rkey}")
        return None
