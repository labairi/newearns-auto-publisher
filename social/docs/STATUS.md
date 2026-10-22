# What works, what is partial, what is not built

Verification levels used below:
- **Tested with mocks**: automated tests pass against fake platform responses written from the official API documentation.
- **Not verified live**: no real account was contacted (this tool was built in an environment with no platform credentials and no access to the platforms). The first real post is your acceptance test; see SETUP.md step 8.

## Working (tested with mocks, not verified live)
| Feature | Notes |
|---|---|
| Post files, validation, per-platform text and limits | Unknown fields rejected; limits copied from rules.py into the dashboard and checked by a test |
| Time-zone aware scheduling | Local time + IANA zone; DST gaps/overlaps refused, never guessed |
| Queue run, status tracking, provider id saved | A post is shown as published only after the platform returned an id |
| Duplicate prevention | Write-ahead attempt record; published targets never resent; same text+media to the same account inside 24 h skipped |
| Retries | Temporary errors: 15 / 60 / 240 min waits, 3 attempts, then stop. Permanent errors: never auto-retried |
| "Uncertain" outcomes | Timeout or 5xx on the create call is never retried automatically; a person checks, then marks published or retries |
| Crash recovery | A run that dies mid-call leaves "publishing", which becomes "uncertain" after 30 min |
| Late-post guard | A post more than 24 h past its time is not sent by surprise |
| Cancel / retry / publish now / mark published | Dashboard writes a command file; the next run applies it |
| Secrets handling | One GitHub secret; accounts file holds names only; values redacted from logs and state |
| Bluesky, Mastodon, Threads, Facebook Page, Instagram adapters | Request shapes tested; see PLATFORMS.md for limits |
| Dashboard: overview, editor with per-platform versions and counts, drafts, queue, calendar, published, failed, media, accounts, logs, setup | Tested in Chromium against a fake GitHub API |
| AI prompt builder and JSON import | Always creates drafts; no paid AI API involved |
| GitHub Actions workflows | Written and YAML-checked; **never run on GitHub yet** |

## Partial
- **Media upload from the dashboard** goes to a public repository you configure. Written, but only the form is browser-tested; the upload call itself is not covered. Instagram requires JPEG.
- **Facebook Page and Instagram** need a Meta app and tokens; if Meta requires app review for your use, that is outside this tool (PLATFORMS.md).
- **Token renewal**: Threads long-lived tokens last about 60 days and are **not** renewed automatically. A Page token derived from a long-lived user token does not expire. Bluesky app passwords and Mastodon tokens do not expire unless you revoke them.
- **Exact timing**: posts go out on the hourly run after their time, usually within minutes, sometimes much later. Not exact-second scheduling.
- **Analytics**: publication history, failures and workflow runs only. No likes, reach or impressions.

## Not built
- **X** (no free posting tier for new developers; pay per post). **LinkedIn / LinkedIn Page** (needs LinkedIn product approval; company pages need Community Management API approval). **TikTok** (unaudited apps can only post privately). **YouTube** (Google Cloud OAuth project; quota and visibility rules to check). Posts that target these are rejected with the reason, never silently skipped.
- Carousels, Stories, threads of several posts, first comments, hashtags-as-topics.
- MCP server (needs an always-on host; none is free and reliable). The queue is plain files plus a documented command format, so a MCP wrapper could be added later.
- Multi-user accounts, billing, inbox/comments, image or video generation, trend research, ads boosting.
- Automatic cross-run locking beyond the Actions concurrency group (see ARCHITECTURE.md).

## Honest comparison with ShapelessAI
ShapelessAI also generates images and video, runs standing AI agents, reads analytics, manages comments and publishes through platform apps it has already had approved, which a personal tool cannot do for free. This tool covers the queue-and-publish core only.
