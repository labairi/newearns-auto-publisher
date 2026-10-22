# Setup

Legend: **[once]** one-time setup, **[routine]** something you do when you use it, **[auto]** happens by itself.

## 1. [once] Put the code on the main branch
The workflow only publishes from `main`. Merge the change that adds `social/` and `.github/workflows/social-*.yml`.
GitHub starts the hourly run by itself afterwards. Until you add accounts and secrets, runs do nothing.

## 2. [once] Pick a first platform
Bluesky is the fastest and has no app review: Settings, Privacy and security, **App passwords**, create one (never use your main password).
Other platforms: `PLATFORMS.md`.

## 3. [once] Describe your accounts
Edit `social/config/accounts.json`. It holds **names of secrets, never the secrets**:
```json
{ "accounts": [
  { "id": "my-bluesky", "platform": "bluesky",
    "secrets": { "identifier": "BSKY_ID", "app_password": "BSKY_APP_PW" } },
  { "id": "my-mastodon", "platform": "mastodon", "instance": "https://mastodon.social",
    "secrets": { "access_token": "MASTODON_TOKEN" } },
  { "id": "my-threads", "platform": "threads", "secrets": { "access_token": "THREADS_TOKEN" } },
  { "id": "my-page", "platform": "facebook_page", "page_id": "123456789012345", "secrets": { "page_token": "FB_PAGE_TOKEN" } },
  { "id": "my-ig", "platform": "instagram", "ig_user_id": "17841400000000000", "secrets": { "access_token": "IG_TOKEN" } }
] }
```
`id` is any short name using lowercase letters, digits, `-` or `_`. Add only the accounts you really have. Also fill `config/brand.json` (name, website, voice, true facts, words to avoid): the AI prompt uses it.

## 4. [once] Store the secret values
Repository, Settings, Secrets and variables, Actions, **New repository secret**. Name: `SOCIAL_SECRETS`. Value: one JSON object mapping each secret name to its value:
```json
{"BSKY_ID":"you.bsky.social","BSKY_APP_PW":"xxxx-xxxx-xxxx-xxxx"}
```
Never paste these into a file in the repository, an issue or a chat. To change one, edit the whole secret. To revoke, delete it on the platform (app password / token) and remove it here.

## 5. [once] Check the accounts (read-only)
Actions tab, **social-publish**, Run workflow, mode **check-setup**. It logs in or reads each account's identity and never posts. Results appear in the run summary and on the dashboard's Accounts page. Fix anything marked ✗ before going on.

## 6. [once] Create a dashboard token
GitHub, Settings, Developer settings, **Fine-grained tokens**, Generate. Repository access: **only this repository** (plus your media repository if you use uploads). Permissions: **Contents: Read and write**, **Actions: Read and write**, nothing else. Set an expiry of 30 to 90 days.

## 7. [once] Host the dashboard
The dashboard is one file, `social/dashboard/index.html`, with no data or secrets inside. Options:
- **GitHub Pages (free)**: copy the file into a **public** repository (for example your media repository), then Settings, Pages, deploy from the branch. Pages on private repositories needs a paid plan. Open the page, go to **Setup**, enter owner, repository, branch (`main`), folder (`social`) and the token. They are stored only in that browser.
- Or open the file straight from your computer. Same steps.

## 8. [once] First real test (you approve it)
Use a **test account**, not your main one. In the dashboard: New / edit, write "test post, please ignore", pick the account, set it a few minutes ahead, status Scheduled, Save. Then Overview, **Dry run** and check the run summary lists it as "would publish". When you are happy, press **Publish due posts now**. A post only moves to Published after the platform returned an id; open the link to confirm. Delete the test post on the platform.

## Routine use
- [routine] Write or import posts, schedule them. Check Failed and Queue now and then.
- [auto] Every hour the workflow publishes what is due, saves results and retries temporary failures (3 attempts, then stops).
- [routine] If a post shows **uncertain**: open the platform. If it is there, Resolve and paste its link. If not, Resolve and leave the link empty to retry.

## Timing
GitHub's scheduler runs the job hourly at minute 7 UTC and can start late, occasionally by 15 to 60 minutes or more at busy times, and runs can be skipped. A 10:30 post normally goes out at the 11:07 run. If you need a specific minute, use **Publish due posts now** at that time. Scheduled workflows on a **public** repository are switched off after 60 days without repository activity; this one is private, but if you ever make it public, remember that.

## Token expiry
| Platform | Lifetime | What you do |
|---|---|---|
| Bluesky app password | until revoked | nothing |
| Mastodon token | until revoked | nothing |
| Threads long-lived token | about 60 days | Renew before it expires (Threads API token refresh), update `SOCIAL_SECRETS`. Not automated here. Put a reminder in your calendar |
| Facebook Page token from a long-lived user token | does not expire, but is invalidated by a password change or revoked permissions | regenerate if posts start failing with an auth error |
| Instagram token | depends on the login path; check Meta's current docs | same as above |
| Dashboard GitHub token | the expiry you chose | create a new one, paste it in Setup |

Expired or revoked credentials show up as a **failed** post with the platform's message and are never retried automatically.
