# Cost

| Item | Monthly cost | Basis |
|---|---|---|
| GitHub repository, Secrets | $0 | Already yours |
| GitHub Actions, hourly run | $0 on the Free plan, as long as you stay under 2,000 minutes | 24 runs x 30 days = 720 runs, each billed as 1 minute (billing rounds up per job) |
| GitHub Pages dashboard | $0 if the dashboard is in a **public** repository | Pages on private repositories needs a paid plan. The page contains no data or secrets |
| Bluesky, Mastodon, Threads, Facebook Page, Instagram APIs | $0 | No fee found in the official docs; limits in PLATFORMS.md |
| AI writing | $0 extra | You use the assistant you already pay for and paste the answer in |
| **Total** | **$0 expected** | |

**Watch-outs (disclosed, nothing here is added without your say-so)**
- The 2,000 Free-plan minutes are shared by every private repository on the account. This repository's blog publisher and tests also use minutes. If you ever near the limit, change the cron in `social-publish.yml` from hourly to every 2 or 3 hours (360 or 240 minutes), or make the repository public (standard runners are free for public repositories), which exposes the queue content, so only do that deliberately.
- Beyond the free minutes, Linux runners are billed per minute (listed at about $0.006/min at the time of writing; re-check GitHub's pricing page) unless you set a spending limit of $0, which is GitHub's default for personal accounts.
- **X** would cost money per post (not built). Anything you add later can have costs: check before enabling.
- Prices and quotas change; this was checked on 2026-10-22 and some figures came from secondary summaries rather than the official pages. Re-check before relying on them.
