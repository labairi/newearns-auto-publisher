"""Facebook Page via the Graph API. Needs a Meta app, a Page access token with pages_manage_posts
(plus publish_video for video) from an admin of the Page, and the Page id.

Use a long-lived user token to get a Page token: Page tokens derived that way do not expire
(see docs/SETUP.md). Media is fetched by Facebook from the public https address.
"""
from __future__ import annotations

from ..http import call
from .base import Adapter, PublishRequest, PublishResult

DEFAULT_VERSION = "v23.0"   # confirm the current version in Meta's changelog when you set this up


class FacebookAdapter(Adapter):
    platform = "facebook_page"
    needs = ("page_token",)

    def _b(self):
        return f"https://graph.facebook.com/{self.account.get('graph_version', DEFAULT_VERSION)}"

    def _page(self):
        pid = str(self.account.get("page_id", "")).strip()
        if not pid.isdigit():
            from ..errors import PublishError
            raise PublishError("permanent", "Facebook: account.page_id must be the numeric Page id")
        return pid

    def verify(self) -> dict:
        r = call(self.http, "GET", f"{self._b()}/{self._page()}", what="Facebook Page identity",
                 params={"fields": "id,name", "access_token": self.secret("page_token")}).json()
        return {"ok": True, "identity": r.get("name"), "id": r.get("id")}

    def publish(self, req: PublishRequest) -> PublishResult:
        tok, page, b = self.secret("page_token"), self._page(), self._b()
        if not req.media:
            r = call(self.http, "POST", f"{b}/{page}/feed", create=True, what="Facebook post",
                     data={"message": req.text, "access_token": tok}).json()
        else:
            m = req.media[0]
            if m["type"] == "image":
                r = call(self.http, "POST", f"{b}/{page}/photos", create=True, what="Facebook photo post",
                         data={"url": m["url"], "message": req.text, "published": "true", "access_token": tok}).json()
            else:
                r = call(self.http, "POST", f"{b}/{page}/videos", create=True, what="Facebook video post",
                         data={"file_url": m["url"], "description": req.text, "access_token": tok}).json()
        pid = str(r.get("post_id") or r.get("id"))
        return PublishResult(pid, f"https://www.facebook.com/{pid}")
