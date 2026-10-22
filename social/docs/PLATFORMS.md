# Platforms

Checked 2026-10-22 against the platforms' public documentation and secondary summaries; **platform rules change, and several items below were not confirmed on an official page. Re-check the linked doc at setup time.** "Built" means this repository has an adapter (mock-tested, not run on a live account).

| Platform | Built | Cost | Review needed | Text limit | Media notes |
|---|---|---|---|---|---|
| Bluesky | yes | free | none | 300 | images only here (1 to 4); video not built |
| Mastodon | yes | free | none | 500 (instance default; some instances differ) | images and video; async processing handled |
| Threads | yes | free | Meta app; tester/own account works without review | 500 | 1 image or video; 250 posts / 24 h |
| Facebook Page | yes | free | Meta app; advanced permissions may need App Review if the Page is not yours | 63,206 | 1 image or video |
| Instagram | yes | free | Meta app; professional account linked to a Page | 2,200 | needs 1 JPEG image or 1 video (Reel); about 100 API posts / 24 h (docs conflict, 50 appears too: confirm) |
| X | **no** | **paid per post** (about $0.015/post, about $0.20 with a link; vendor blog, verify) | n/a | 280 | no free tier for new developers |
| LinkedIn (personal) | no | free | "Share on LinkedIn" product; unconfirmed | 3,000 | |
| LinkedIn Page | no | free | Community Management API approval, about 1 to 2 weeks | 3,000 | |
| TikTok | no | free | audit needed; posts from an unaudited app are private-only | 2,200 | video; verified URL ownership for pull-from-URL |
| YouTube | no | free | Google OAuth consent; check visibility rules for unverified projects | title 100 / desc 5,000 | upload quota to check (docs differ) |

## Bluesky
- Developer account: none. Credential: handle + **app password**. No OAuth.
- Secrets: `identifier` (your handle or email), `app_password`.
- API: `com.atproto.server.createSession`, `uploadBlob`, `createRecord`. Link and hashtag facets use UTF-8 byte offsets.
- Limits: 300 characters; images up to 4. Rate limits are generous for a personal account.
- Docs: https://docs.bsky.app/

## Mastodon
- Developer account: none. On your instance: Preferences, Development, New application, scopes `write:statuses` and `write:media`; use the access token.
- Account entry needs `"instance": "https://your.instance"`. Optional `"visibility"` (default `public`).
- Posts send an `Idempotency-Key`, so a repeat inside the server's window returns the first post.
- Docs: https://docs.joinmastodon.org/api/

## Threads
- Meta developer account, a Meta app with the Threads use case, permissions `threads_basic` and `threads_content_publish`, a long-lived access token (about 60 days) for your own account (add yourself as a Threads tester).
- Flow: create container, publish container. Only the publish step is treated as "uncertain" on timeout.
- Limits: 500 characters, 250 posts per 24 h, image JPEG/PNG up to 8 MB, video up to 5 minutes (per the docs summary; verify).
- Docs: https://developers.facebook.com/docs/threads/

## Facebook Page
- Meta developer app; a user who administers the Page; `pages_manage_posts` (and `publish_video` for video, plus the Page-read permission Meta asks for); exchange a long-lived user token for a **Page access token**.
- Account entry needs numeric `"page_id"`. Secret role: `page_token`. Graph version is `"graph_version"` (default in code; update when Meta retires it).
- Endpoints used: `/feed`, `/photos`, `/videos`. Native scheduling (10 minutes to 30 days ahead) exists but is **not used**: the queue schedules instead.
- Using the app only on Pages you administer normally works in development mode without App Review; posting for other people's Pages needs review. Confirm in Meta's current docs.
- Docs: https://developers.facebook.com/docs/pages-api/

## Instagram (publishing)
- Professional (Business or Creator) account linked to a Facebook Page; Meta app; permissions `instagram_basic`, `instagram_content_publish`, `pages_read_engagement` (Facebook Login path). Account entry needs numeric `"ig_user_id"`.
- Media must be at a **public https address** Instagram can fetch. Single images must be **JPEG**. One video is published as a Reel. Carousels and Stories are not built.
- No native scheduling parameter; the queue handles timing.
- Docs: https://developers.facebook.com/docs/instagram-platform/

## Not built, with what each would need
- **X**: developer account and a paid plan; posting is per-use priced. Adding it means accepting a recurring cost, so it needs your approval first.
- **LinkedIn**: a LinkedIn developer app and the Share product; for company pages the Community Management API approval. OAuth tokens expire (60 days) and refresh tokens need a program-approved app.
- **TikTok**: TikTok for Developers app with the Content Posting API; until the app is audited, posts are private only.
- **YouTube**: Google Cloud project, YouTube Data API v3, OAuth consent screen and refresh token; check the daily quota and the rule that videos uploaded by unverified projects may be locked private.

## Revoking access
Bluesky: delete the app password. Mastodon: Preferences, Development, delete the app. Meta: Business Integrations / Apps and Websites, remove the app; regenerate tokens. GitHub token: Settings, Developer settings, delete. Then update `SOCIAL_SECRETS`.
