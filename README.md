# New Earns automated publisher

Researches, writes, fact-checks and publishes one article per run to
https://newearns.com, then logs it and tracks it in Search Console.

## What New Earns runs on (checked 2026-10-19 from the public site)

| Item | Finding |
|---|---|
| CMS | WordPress, REST API open at `/wp-json/wp/v2` (posts, media, categories) |
| Hosting | Hostinger (Hostinger Tools plugin), LiteSpeed Cache |
| Theme | GeneratePress + GenerateBlocks (Pro) |
| SEO | All in One SEO 5.0; its title/description/focus keyphrase can be set through the same REST call (`aioseo_meta_data`) |
| Analytics | Google Site Kit + MonsterInsights (GA4 outbound-link tracking), Complianz cookie consent |
| Content | 9 posts (Jul to Sep 2025), categories: Passive Income Ideas (1), Make Money Online (30), AI & Future Trends (34); single author "David" |
| Products | Neither product site has a blog. Both load Google Tag Manager, so UTM-tagged visits from New Earns show up in their analytics. |

## Recommended stack and monthly cost

| Piece | Choice | Cost |
|---|---|---|
| Publishing | WordPress REST API + Application Password (built into WordPress) | $0 |
| Scheduler and runner | GitHub Actions in a private repo. Manual runs, plus one scheduled run a day at 11:00 Morocco time (see "Daily scheduled run") | $0 (about 30 to 60 of the free 2,000 minutes per month) |
| Research, writing, fact-check | Claude API, Claude Opus 5.5 with web search and web fetch | about $1 to $2 per article; about $15 to $30 per month at 3 per week. Every run logs its own cost estimate. |
| Search performance | Google Search Console API (service account, read-only) | $0 |
| Engagement | GA4 Data API (same service account, read-only) | $0 |
| Keyword data | Search Console queries + web research on what ranks. Optional later: DataForSEO pay-as-you-go ($50 minimum top-up, about $0.06 per 1,000-keyword volume lookup) | $0 now |
| Images | Licensed, topic-relevant photo for the featured image (Openverse, then Wikimedia Commons; no keys), else a topic illustration; own-product screenshots and charts of table figures. No paid image API, no AI image generation | $0 |

n8n was considered: n8n Cloud Starter is about €20/month, and self-hosting needs a
VPS you maintain. GitHub Actions does the same job for $0, keeps every change
versioned, and stores the publication log in the repo.

## How a run works

1. **Pick a pillar.** Rotation weights PDF 40%, Instagram 40%, other money topics
   20%. It picks whichever pillar is furthest below its share, and avoids the
   same pillar twice in a row. Search Console demand is fed into topic choice.
2. **Pick a topic.** Seed topics in `config.yaml` that no existing post already
   covers, plus queries New Earns already gets impressions for but ranks badly on.
3. **Research brief (Claude + web search/fetch).** Looks at what ranks to find the
   real search intent, chooses one keyphrase and angle, and reads 3 to 6
   authoritative sources.
4. **Write.** Structured output: title, SEO title, slug, meta description,
   keyphrase, HTML body, FAQ, source list, internal links, and the product link
   only if justified. Product claims are limited to the allowed-claims list in
   `config.yaml`. A product link always carries a UTM tag and an ownership disclosure.
5. **Independent fact-check.** A separate request opens the cited sources again
   and grades every claim, the search intent, original value and product claims.
   Each claim is *supported*, *contradicted* (a source says otherwise), *unsupported*
   (no evidence found, which is not the same as false) or *uncertain* (the source
   could not be read), with a suggested fix. A *supported* claim counts only if its
   `source_url` is a page the pipeline itself read. A page that redirects to a different site
   (`www.` aside) is not counted as read, and the fact-checker is told where the text came from.
   Limitation: server-side `web_fetch` reports no redirect details, so only a result on a
   different site from the URL requested can be caught there. Each cited source is marked relevant or not.
6. **Quality gates.** 36 checks in `publisher/quality.py`: length, structure,
   keyword density and stuffed headings, metadata lengths, slug format, near-duplicate
   body text against every existing post, a near-identical title or a keyphrase that
   is already targeted, fabricated-experience and hype phrases, broken links,
   internal links that must point at published New Earns posts (or, when no published
   post is relevant, an `internal_links_note` saying so; product sites never count as
   internal), https only, product link limits, UTM and disclosure, at least 3
   independent sources, statistics without a linked source, image alt text and
   licence, the fact-check verdicts, a fact-check that matches the exact text, and
   every cited source read and relevant. Each failure is reported with a fix.

   **Correcting a held article without regenerating it:** edit the article JSON (or
   copy `article` out of `data/runs/<id>/state.json`) and run `recheck` on it. Tick
   `refresh_fact_check` to re-run only the fact-check on the edited text (about
   $0.40 at list prices on the 2026-10-20 run); the result is saved as
   `data/runs/<new id>/article.json`, which later rechecks reuse at no cost.
7. **Images** (`publisher/images.py`, settings under `images:` in `config.yaml`). $0.
   - How many: a guideline by length, counting the featured image: 600-900 words 1-2,
     900-1,500 words 2-3, 1,500-2,500 words 3-4, over 2,500 words 4-5. It is a ceiling,
     not a quota: no image is added just to reach it.
   - Featured image: a real photograph. It searches Openverse (StockSnap, nappy, Flickr and
     Wikimedia photos), then Wikimedia Commons directly. Neither needs a key. The article's own
     query comes first, then the pillar's scene searches, and photos are ranked by words from
     the article (worth 3) and the pillar's scene words (worth 1). Logos, screenshots,
     drawings, portrait shots, small files and camera file names are skipped.
   - Relevance: each article gets a topic from `images.topics` (compress, password, merge,
     split, convert, sign, edit, follower location...). A photo is used only if its own title
     names the device the task is done on (laptop, phone...), it mentions the topic's subject
     words (e.g. PDF, compression, attachment), and it is not a wrong scene: signing, contracts,
     meetings, handshakes, books or paperwork are rejected unless the topic is about them.
     Broad words such as "document", "business" or "office" never count. Rejected photos and
     the reason are listed in the run summary.
   - Licence: only CC0, public domain, CC BY or CC BY-SA, read from the source's API. Before
     anything is downloaded, the licence link (and for BY / BY-SA the creator) must be present.
     Commons files with personality-rights or trademark restrictions are never used. The post
     ends with a credit line. Image search engines such as Google Images are never used.
   - Failures: a search that times out or returns 429/5xx is retried once, and then the next
     query is tried. Two failed searches in a row move on to the next source. Failed searches
     are not cached.
   - No fitting photo: an in-house illustration of the topic (a large PDF shrinking into a
     smaller one on a laptop, a PDF with a padlock, a phone with map pins...), reported as a
     FALLBACK. It is plainly a drawing, with no app interface, brand or figures. The branded
     title card is used only if the illustration cannot be drawn.
   - Inline images, at most 2, only where they add something; none just to reach a count:
     a real screenshot of our own product's page (Private PDF Editor / Instagramaps), captured
     live with the runner's Chrome at run time, captioned with the date and placed after the
     paragraph that links it. Third-party apps are never captured or imitated. Also a bar chart
     of a table that compares quantities (3-8 rows in one unit), placed after the table. A value
     far above the rest is drawn with a visible break and a note.
   - Files: 1200 px wide WebP (quality 80, no metadata), named `<slug>-featured.webp` /
     `<slug>-chart-<section>.webp` / `<slug>-<product>-screenshot.webp`, with alt text. Successful searches are cached in
     `data/image_cache/` for 30 days. A photo is used for one article only. Uploads are
     checkpointed, and a file already in the Media Library under the same name is reused.
   - After the post is created, the run reads it back and checks the featured image, every
     inline image and every file. If anything is missing, the post is held as Pending Review.
   - Pexels (free key, better stock) is supported but off. It would need a `PEXELS_API_KEY`
     secret, and that needs the owner's approval first.
   - **image-test-draft** command: images for a saved article (`article_file`), saved as one
     new WordPress draft with an `image-test-<time>-` slug. No Claude call, and not logged as an
     attempt, so the schedule and calendar are unaffected.
8. **Publish or hold.** If everything passes, the post is created as a draft,
   then switched to published. Anything failing is saved as **Pending Review**
   in WordPress. That is the review queue, and the reasons are in the log.
9. **Verify.** Loads the public URL like a visitor would, and checks for HTTP 200,
   the title on the page, and no noindex. If verification fails, the post is moved
   back to Pending.
10. **Log.** `data/publication_log.jsonl` records the date, URL, post id, pillar,
   keyphrase, product links, sources, failures and estimated cost.
11. **Weekly report.** `report` flags articles 45+ days old that have no
    impressions, a low click-through rate on page 1, or a position of 8 to 25.

**Safety:** a run lock and GitHub concurrency group stop runs overlapping. Slug
checks against WordPress and the log block duplicates. A minimum gap of 20 hours
separates publishes. Every stage is checkpointed, so `--resume` finishes a crashed
run without paying to regenerate it. The code only touches posts and media: it
never edits pages, theme, plugins, settings, users, categories or existing posts.

## Modes and safety switches

| Setting | Effect |
|---|---|
| `--dry-run` | Generates and checks the article, saves it under `data/runs/`, sends nothing to WordPress |
| `publishing.mode: review` | Every article is saved as Pending Review |
| `publishing.mode: draft` | Passing articles become Drafts, failing ones Pending Review |
| `publishing.mode: publish` (shipped) | Passing articles go live, **only if** `ALLOW_PUBLISH=true`. The workflow sets that only for an `article` run on `main` with the **allow_publish** box ticked; otherwise it falls back to draft. A generated article also needs `--pillar`/`--topic` from the approved `calendar` in config.yaml. At most one live post per 20 hours. |
| `WP_BASE_URL` env var | Overrides `site.base_url` from config.yaml |

Credentials are read only from environment variables / GitHub Actions secrets:
`ANTHROPIC_API_KEY`, `WP_USERNAME`, `WP_APP_PASSWORD`, optional `GOOGLE_SERVICE_ACCOUNT_JSON`.

GitHub: Actions > **publisher** > Run workflow > choose `check-setup`, `article-dry-run` or `article`.

## Daily scheduled run

The workflow also runs once a day at **11:07 Morocco time**: cron `7 11 * * *`, with a
backup at `37 11 * * *` in case GitHub drops the first one. Both are off the hour because
GitHub delays top-of-hour schedules most (in October 2026 the 11:00 runs arrived 3-5 hours late). GitHub cron is always UTC.
Morocco has been on UTC+0 all year since 2026-09-20, with no daylight saving and no
Ramadan change, so 11:07 Morocco is 11:07 UTC. If Morocco changes its clocks again, both
cron lines must change. GitHub can start a scheduled run late, or occasionally skip one.
The backup costs nothing when the first run already happened, because of the 20-hour gap.
Schedules only run from `main`.

**When GitHub's cron is late.** In October 2026 GitHub delivered the daily cron 3-5 hours
late or not at all. The `daily` command runs exactly what the cron runs (`article
--scheduled`, same 20-hour gap, pause rule, calendar and duplicate checks), so an outside
scheduler can start it on time with one API call:

```
POST https://api.github.com/repos/labairi/newearns-auto-publisher/actions/workflows/publish.yml/dispatches
Authorization: Bearer <fine-grained token: this repo only, Actions read and write>
{"ref": "main", "inputs": {"command": "daily"}}
```

Whichever comes first, the outside call or the cron, does the day's article; the other skips
for free.

**What it does.** `python -m publisher.run article --scheduled`:

1. **Skip checks first, with no Claude call and no WordPress write:**
   - **20-hour gap.** It skips if any non-dry-run attempt (manual or scheduled; published,
     draft, held, duplicate, failed verification, or a scheduled run that errored or never
     finished) happened in the last `min_hours_between_posts` (20) hours.
   - **Pause.** It skips if the last 2 attempts were both unsuccessful (held, duplicate,
     failed verification, error or crash). A draft or published post counts as a success.
2. **One topic.** If an earlier run of an approved calendar topic stopped before finishing,
   it resumes that run and reuses its saved research, article and fact-check. Otherwise it
   takes the first `calendar` entry in config.yaml that no run has attempted yet. Dry runs
   don't use up a topic. **A held topic is not retried automatically**, because retrying
   would pay for a new article; correct it and use `recheck` instead. When every topic has
   been attempted, it logs "no eligible calendar topic" and stops.
3. **The usual pipeline, unchanged:** research, write, fact-check, all 36 gates, duplicate
   and slug checks, then WordPress.
4. **Live only if every gate passes.** A passing article is published, then its public
   URL is checked. If that check fails, the post goes back to **Pending Review**. A failing
   article is saved as **Pending Review** and never goes live. Two keys are both needed:
   `publishing.scheduled_live: true` in config.yaml, and the workflow's `ALLOW_PUBLISH`,
   which is `true` only on `main`, for scheduled runs or for a manual `article` run with
   **allow_publish** ticked. Set `scheduled_live: false` to make scheduled runs save
   Drafts instead. Approved by the owner on 2026-10-21.

Every attempt is recorded in `data/schedule_log.jsonl` (start and outcome) and, once it
reaches WordPress, in `data/publication_log.jsonl` with its `calendar_topic`.

Manual runs keep their own rules: a manual `article` run's 20-hour check still counts
only *published* posts, so a held attempt doesn't block your manual live test.

**Setup.** Nothing beyond the secrets already set (`ANTHROPIC_API_KEY`, `WP_USERNAME`,
`WP_APP_PASSWORD`). After merging, check Actions > publisher the next day: the run summary
says which topic it chose, or why it skipped.

**Cost** (list-price estimates from the 2026-10-20 run):

| Case | Claude cost |
|---|---|
| A skipped day (gap, pause, no topic) | $0 |
| One new article | about $1.91, the same whether it is saved as a draft or held |
| A resumed run | only the stages it hadn't finished |
| Most per day | one attempt, about $1.91 |
| Before a pause | at most 2 unsuccessful attempts in a row, about $3.82 |
| Whole calendar | 14 topics left after 2026-10-21, about $27 in total; after that it skips at $0 until you add topics |

**Pausing.**
- **Stop it completely:** GitHub > Actions > publisher > "..." > **Disable workflow**.
  Enable it again the same way. No code change is needed.
- **After an automatic pause:** fix the cause (the run summary and log list each failure
  with a fix), then run the workflow manually with the command **resume-schedule**. That
  costs nothing and lets the next scheduled run go ahead. The 20-hour gap still applies.

## Commands

```
python -m publisher.run check-setup        # verify keys and WordPress permissions (no writes)
python -m publisher.run article --dry-run  # full generation + checks, nothing sent to WordPress
python -m publisher.run article            # uses publishing.mode (draft | publish)
python -m publisher.run article --resume   # finish a crashed run
python -m publisher.run article --pillar pdf --topic "How to Compress a PDF for Email"   # one approved calendar topic
python -m publisher.run article --scheduled   # the daily scheduled run (see above)
python -m publisher.run resume-schedule       # let scheduled runs continue after a pause
python -m publisher.run image-test --article-file data/runs/<id>/article.json   # images -> one test draft
python -m publisher.run report             # Search Console / GA4 improvement flags
pytest -q                                  # offline tests, incl. end-to-end against a fake WordPress
```

See SETUP.md for the go-live steps.
