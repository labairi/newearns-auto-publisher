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
| Scheduler and runner | GitHub Actions in a private repo. Manual runs only for now; a Mon/Wed/Fri schedule is added only after you approve it | $0 (about 15 to 30 of the free 2,000 minutes per month) |
| Research, writing, fact-check | Claude API, Claude Opus 5.5 with web search and web fetch | about $1 to $2 per article; about $15 to $30 per month at 3 per week. Every run logs its own cost estimate. |
| Search performance | Google Search Console API (service account, read-only) | $0 |
| Engagement | GA4 Data API (same service account, read-only) | $0 |
| Keyword data | Search Console queries + web research on what ranks. Optional later: DataForSEO pay-as-you-go ($50 minimum top-up, about $0.06 per 1,000-keyword volume lookup) | $0 now |
| Images | Branded title card drawn by the pipeline (no licence risk) | $0 |

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
7. **Publish or hold.** If everything passes, the post is created as a draft,
   then switched to published. Anything failing is saved as **Pending Review**
   in WordPress. That is the review queue, and the reasons are in the log.
8. **Verify.** Loads the public URL like a visitor would, and checks for HTTP 200,
   the title on the page, and no noindex. If verification fails, the post is moved
   back to Pending.
9. **Log.** `data/publication_log.jsonl` records the date, URL, post id, pillar,
   keyphrase, product links, sources, failures and estimated cost.
10. **Weekly report.** `report` flags articles 45+ days old that have no
    impressions, a low click-through rate on page 1, or a position of 8 to 25.

**Safety:** a run lock and GitHub concurrency group stop runs overlapping. Slug
checks against WordPress and the log block duplicates. A minimum gap of 36 hours
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

## Commands

```
python -m publisher.run check-setup        # verify keys and WordPress permissions (no writes)
python -m publisher.run article --dry-run  # full generation + checks, nothing sent to WordPress
python -m publisher.run article            # uses publishing.mode (draft | publish)
python -m publisher.run article --resume   # finish a crashed run
python -m publisher.run article --pillar pdf --topic "How to Compress a PDF for Email"   # one approved calendar topic
python -m publisher.run report             # Search Console / GA4 improvement flags
pytest -q                                  # 22 tests incl. end-to-end against a fake WordPress
```

See SETUP.md for the go-live steps.
