# Go-live checklist

Each step says who does it. Nothing goes public until step 8.

## 1. You: create a dedicated WordPress publishing account
WP Admin > Users > Add New:
- Username: e.g. `newearns-editorial`. Role: **Author** (it can publish its own
  posts and upload images, but cannot touch pages, plugins, settings or other
  people's posts). Use Editor only if you want it to use the review queue for
  others' posts too. If AIOSEO's Access Control blocks Authors from SEO
  settings, the first test will say so and you can switch the role to Editor.
- Display name: the byline readers will see, for example "New Earns Editorial".
  Please don't use a person's name for automated articles unless that person
  reviews them.

Then log in as that user > Profile > **Application Passwords** > name it
`autopublisher` > copy the generated password (shown once).

Note: today `https://newearns.com/wp-json/wp/v2/users` publicly lists your
admin account with the slug `lahcenlabairigmail-com`, which reveals your login
e-mail. Changing that is your call; I have not changed anything on the site.

## 2. You: decide on two new categories (optional but recommended)
PDF and Instagram articles don't fit the existing three categories well. Suggested:
- "Documents & Productivity" (slug `documents-productivity`)
- "Creator Economy" (slug `creator-economy`)

Create them in Posts > Categories and send me their ids. Until then those
articles go into "Make Money Online". The pipeline never creates categories itself.

## 3. You: Claude API key
https://platform.claude.com > API keys > create key `newearns-publisher`.
Set a monthly spend limit (for example $40) under Limits. This is a paid,
usage-billed service. About $15 to $30 per month is expected at 3 articles per week.

## 4. You: GitHub repository
Private repo `labairi/newearns-auto-publisher` (done) and attach it
to this project (Project settings > Repositories) so I can push the code.
Then add these under Settings > Secrets and variables > Actions
(the Claude GitHub App must also be allowed on this repo):
`ANTHROPIC_API_KEY`, `WP_USERNAME`, `WP_APP_PASSWORD`
(and later `GOOGLE_SERVICE_ACCOUNT_JSON`).

## 5. You (optional, free): Search Console and GA4 access
Google Cloud console > create a project > enable "Google Search Console API"
and "Google Analytics Data API" > create a service account > create a JSON key.
- Search Console > Settings > Users and permissions > add the service account
  e-mail as **Restricted**.
- GA4 > Admin > Property access management > add it as **Viewer**, and put
  the numeric property id in `config.yaml` (`tracking.ga4_property_id`).
Paste the JSON into the `GOOGLE_SERVICE_ACCOUNT_JSON` secret.

## 6. Me: push the code, run tests, then `check-setup`
Confirms the login works, the role is not Administrator, and the categories exist.

## 7. Me + you: end-to-end test with one article
1. Actions > publisher > Run workflow > `check-setup` (read-only).
2. `article-dry-run`: review the generated article, quality report and featured
   image in `data/runs/<id>/`. Nothing is sent to WordPress.
3. `article` with **allow_publish** unticked: the article lands in WordPress as a
   Draft (or Pending Review if a check failed). Open the preview and check the
   layout, category, AIOSEO title/description and image.
4. Only with your explicit approval: run `article` on `main` with a calendar
   `pillar`/`topic` and the **allow_publish** box ticked. Confirm the live URL is
   verified and logged, then run it again to confirm it refuses a second post
   within 20 hours.

## 8. The daily scheduled run
The workflow runs once a day at 11:00 Morocco time (11:00 UTC). It publishes one
calendar topic live if every gate passes, or saves it as Pending Review if a check
failed. See "Daily scheduled run" in the README for the skip rules, cost and how to
pause it. Held articles show in WP Admin > Posts > Pending.

## Pausing or rolling back
- Pause: GitHub > Actions > publisher > "Disable workflow". After an automatic pause
  (2 unsuccessful attempts in a row), run the workflow with **resume-schedule**.
- Stop anything going live: leave **allow_publish** unticked, or set `mode: draft`.
- Remove a bad article: trash it in WP Admin (the log keeps the record).
