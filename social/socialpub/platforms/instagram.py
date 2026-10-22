"""Instagram (Instagram API via a Facebook Page, the 'Facebook Login' path).

Needs: an Instagram professional account linked to a Facebook Page, a Meta app, a token with
instagram_basic + instagram_content_publish + pages_read_engagement, and the Instagram user id.
Single image (JPEG only) or one video published as a Reel. Carousels and Stories are not built.
Media is fetched by Instagram from a public https address.
"""
from __future__ import annotations

from ..errors import PublishError
from ..http import call
from .base import Adapter, PublishRequest, PublishResult

DEFAULT_VERSION = "v23.0"


class InstagramAdapter(Adapter):
    platform = "instagram"
    needs = ("access_token",)

    def _b(self):
        return f"https://graph.facebook.com/{self.account.get('graph_version', DEFAULT_VERSION)}"

    def _ig(self):
        i = str(self.account.get("ig_user_id", "")).strip()
        if not i.isdigit():
            raise PublishError("permanent", "Instagram: account.ig_user_id must be the numeric Instagram user id")
        return i

    def verify(self) -> dict:
        r = call(self.http, "GET", f"{self._b()}/{self._ig()}", what="Instagram identity",
                 params={"fields": "id,username", "access_token": self.secret("access_token")}).json()
        return {"ok": True, "identity": r.get("username"), "id": r.get("id")}

    def publish(self, req: PublishRequest) -> PublishResult:
        if not req.media:
            raise PublishError("permanent", "Instagram needs an image or a video")
        tok, ig, b = self.secret("access_token"), self._ig(), self._b()
        m = req.media[0]
        p = {"caption": req.text, "access_token": tok}
        if m["type"] == "image":
            ext = m["url"].split("?")[0].rsplit(".", 1)[-1].lower()
            if ext not in ("jpg", "jpeg"):
                raise PublishError("permanent", "Instagram single images must be JPEG; convert the file and use a .jpg address")
            p["image_url"] = m["url"]
        else:
            p.update(media_type="REELS", video_url=m["url"])
        cid = call(self.http, "POST", f"{b}/{ig}/media", what="Instagram create container", data=p).json()["id"]
        if m["type"] == "video":
            for _ in range(40):
                st = call(self.http, "GET", f"{b}/{cid}", what="Instagram container status",
                          params={"fields": "status_code", "access_token": tok}).json()
                code = st.get("status_code")
                if code == "FINISHED":
                    break
                if code in ("ERROR", "EXPIRED"):
                    raise PublishError("permanent", f"Instagram could not process the video ({code})")
                self.sleep(5)
            else:
                raise PublishError("transient", "Instagram: video still processing after 200 s")
        r = call(self.http, "POST", f"{b}/{ig}/media_publish", create=True, what="Instagram publish",
                 data={"creation_id": cid, "access_token": tok}).json()
        pid, url = r["id"], ""
        try:
            url = call(self.http, "GET", f"{b}/{pid}", what="Instagram permalink",
                       params={"fields": "permalink", "access_token": tok}).json().get("permalink", "")
        except PublishError:
            pass
        return PublishResult(pid, url)
