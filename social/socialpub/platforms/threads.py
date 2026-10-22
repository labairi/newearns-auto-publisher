"""Threads API (Meta). Needs a Meta developer app with the Threads use case, a Threads tester or
the app's own account, and a long-lived access token with threads_basic + threads_content_publish.
Long-lived tokens expire after about 60 days: see docs/SETUP.md (token reminders).

Two steps, as the API defines them: create a media container, then publish it. Only the publish call
creates the public post, so only that one is treated as 'uncertain' when it times out.
"""
from __future__ import annotations

from ..errors import PublishError
from ..http import call
from .base import Adapter, PublishRequest, PublishResult

BASE = "https://graph.threads.net/v1.0"


class ThreadsAdapter(Adapter):
    platform = "threads"
    needs = ("access_token",)

    def verify(self) -> dict:
        r = call(self.http, "GET", f"{BASE}/me", what="Threads identity",
                 params={"fields": "id,username", "access_token": self.secret("access_token")}).json()
        return {"ok": True, "identity": r.get("username"), "id": r.get("id")}

    def publish(self, req: PublishRequest) -> PublishResult:
        tok = self.secret("access_token")
        me = call(self.http, "GET", f"{BASE}/me", what="Threads identity", params={"fields": "id", "access_token": tok}).json()["id"]
        p = {"text": req.text, "access_token": tok}
        if not req.media:
            p["media_type"] = "TEXT"
        else:
            m = req.media[0]
            if m["type"] == "image":
                p.update(media_type="IMAGE", image_url=m["url"])
            else:
                p.update(media_type="VIDEO", video_url=m["url"])
        c = call(self.http, "POST", f"{BASE}/{me}/threads", what="Threads create container", params=p).json()["id"]
        if p["media_type"] == "VIDEO":
            for _ in range(30):
                st = call(self.http, "GET", f"{BASE}/{c}", what="Threads container status",
                          params={"fields": "status,error_message", "access_token": tok}).json()
                if st.get("status") == "FINISHED":
                    break
                if st.get("status") in ("ERROR", "EXPIRED"):
                    raise PublishError("permanent", f"Threads could not process the video: {st.get('error_message', st.get('status'))}")
                self.sleep(5)
            else:
                raise PublishError("transient", "Threads: video still processing after 150 s")
        r = call(self.http, "POST", f"{BASE}/{me}/threads_publish", create=True, what="Threads publish",
                 params={"creation_id": c, "access_token": tok}).json()
        pid = r["id"]
        url = ""
        try:
            url = call(self.http, "GET", f"{BASE}/{pid}", what="Threads permalink",
                       params={"fields": "permalink", "access_token": tok}).json().get("permalink", "")
        except PublishError:
            pass   # the post exists; a missing link must never turn a success into a failure
        return PublishResult(pid, url)
