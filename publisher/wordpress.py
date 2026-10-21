"""Minimal WordPress REST client for New Earns.

Auth: WordPress Application Passwords (core since WP 5.6) over HTTPS.
Only touches posts and media. It never edits pages, themes, plugins,
settings, users or categories.
"""
from __future__ import annotations

import base64
import html
import os
import time
from dataclasses import dataclass

import requests


class WordPressError(RuntimeError):
    pass


@dataclass
class WordPress:
    base_url: str
    username: str | None = None
    app_password: str | None = None
    timeout: int = 30

    @classmethod
    def from_env(cls, base_url: str) -> "WordPress":
        return cls(
            base_url=base_url.rstrip("/"),
            username=os.environ.get("WP_USERNAME"),
            app_password=os.environ.get("WP_APP_PASSWORD"),
        )

    # -- plumbing ---------------------------------------------------------
    def _headers(self, auth: bool) -> dict:
        h = {"User-Agent": "newearns-autopublisher/1.0", "Accept": "application/json"}
        if auth:
            if not (self.username and self.app_password):
                raise WordPressError("WP_USERNAME / WP_APP_PASSWORD are not set")
            token = base64.b64encode(f"{self.username}:{self.app_password}".encode()).decode()
            h["Authorization"] = f"Basic {token}"
        return h

    def _req(self, method: str, path: str, auth: bool = False, **kw) -> requests.Response:
        url = f"{self.base_url}/wp-json{path}"
        for attempt in range(4):
            r = requests.request(method, url, headers={**self._headers(auth), **kw.pop("headers", {})},
                                 timeout=self.timeout, **kw)
            if r.status_code in (429, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** (attempt + 1))
                continue
            if r.status_code >= 400:
                raise WordPressError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
            return r
        raise WordPressError(f"{method} {path} failed after retries")

    # -- reads (public) ----------------------------------------------------
    def all_posts(self, status: str = "publish", auth: bool = False) -> list[dict]:
        posts, page = [], 1
        fields = "id,date,date_gmt,slug,status,title,link,categories,content,aioseo_meta_data"
        while True:
            r = self._req("GET", "/wp/v2/posts", auth=auth, params={
                "per_page": 100, "page": page, "status": status, "_fields": fields})
            batch = r.json()
            posts.extend(batch)
            if page >= int(r.headers.get("X-WP-TotalPages", 1)):
                return posts
            page += 1

    def categories(self) -> dict[int, str]:
        r = self._req("GET", "/wp/v2/categories", params={"per_page": 100, "_fields": "id,slug"})
        return {c["id"]: c["slug"] for c in r.json()}

    def find_by_slug(self, slug: str) -> dict | None:
        """Checks every status the credentials can see (publish/draft/pending/future)."""
        auth = bool(self.username and self.app_password)
        params = {"slug": slug, "_fields": "id,slug,status,link"}
        if auth:
            params["status"] = "publish,future,draft,pending,private"
        r = self._req("GET", "/wp/v2/posts", auth=auth, params=params)
        found = r.json()
        return found[0] if found else None

    # -- auth check ---------------------------------------------------------
    def whoami(self) -> dict:
        return self._req("GET", "/wp/v2/users/me", auth=True,
                         params={"context": "edit", "_fields": "id,name,roles,capabilities"}).json()

    # -- writes (authenticated) ---------------------------------------------
    def upload_media(self, filename: str, data: bytes, mime: str, alt_text: str, caption: str = "") -> dict:
        r = self._req("POST", "/wp/v2/media", auth=True, data=data, headers={
            "Content-Disposition": f'attachment; filename="{filename}"', "Content-Type": mime})
        media = r.json()
        self._req("POST", f"/wp/v2/media/{media['id']}", auth=True,
                  json={"alt_text": alt_text, "caption": caption})
        return media

    def find_media(self, filename: str) -> dict | None:
        """A file this account already uploaded under exactly this name, so it is not uploaded twice."""
        stem = filename.rsplit(".", 1)[0]
        r = self._req("GET", "/wp/v2/media", auth=True, params={
            "search": stem, "per_page": 20, "context": "edit", "_fields": "id,source_url"})
        for m in r.json():
            if m.get("source_url", "").rsplit("/", 1)[-1] == filename:
                return m
        return None

    def create_post(self, *, title: str, slug: str, content_html: str, excerpt: str,
                    status: str, category_id: int, featured_media: int | None,
                    seo_title: str, meta_description: str, focus_keyphrase: str) -> dict:
        if status not in ("draft", "pending", "publish"):
            raise ValueError(status)
        body = {
            "title": title, "slug": slug, "content": content_html, "excerpt": excerpt,
            "status": status, "categories": [category_id], "comment_status": "open",
            # AIOSEO accepts its meta on the core posts endpoint (Lite and Pro).
            "aioseo_meta_data": {
                "title": seo_title,
                "description": meta_description,
                "keyphrases": {"focus": {"keyphrase": focus_keyphrase}, "additional": []},
            },
        }
        if featured_media:
            body["featured_media"] = featured_media
        return self._req("POST", "/wp/v2/posts", auth=True, json=body).json()

    def get_post(self, post_id: int) -> dict:
        """The stored post as the editor sees it (raw content), for checking images landed."""
        return self._req("GET", f"/wp/v2/posts/{post_id}", auth=True, params={
            "context": "edit", "_fields": "id,status,slug,link,featured_media,content"}).json()

    def get_media(self, media_id: int) -> dict:
        return self._req("GET", f"/wp/v2/media/{media_id}", auth=True, params={
            "context": "edit", "_fields": "id,source_url,alt_text,mime_type,media_details"}).json()

    def fetch_file(self, url: str) -> bytes:
        """An uploaded file as the site serves it to visitors."""
        r = requests.get(url, timeout=30, headers={"User-Agent": "newearns-autopublisher/1.0"})
        r.raise_for_status()
        return r.content

    def set_status(self, post_id: int, status: str) -> dict:
        return self._req("POST", f"/wp/v2/posts/{post_id}", auth=True, json={"status": status}).json()


def public_url_ok(url: str, expect_text: str) -> tuple[bool, str]:
    """Fetch the live article like a visitor would and confirm the title renders."""
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 newearns-verify"})
    except requests.RequestException as e:
        return False, f"request failed: {e}"
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}"
    if expect_text.lower()[:40] not in html.unescape(r.text).lower():
        return False, "page loaded but title text not found"
    if 'name="robots" content="noindex' in r.text.replace("'", '"'):
        return False, "page is noindex"
    return True, "ok"
