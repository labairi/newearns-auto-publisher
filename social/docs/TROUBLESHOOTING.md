# Troubleshooting

Start with Dashboard, Logs, and the failed run's page on GitHub (the run summary lists published, failed, uncertain, skipped, invalid).

| Symptom | Likely cause | What to do |
|---|---|---|
| Nothing publishes | Workflow not on `main`; Actions disabled; no accounts or `SOCIAL_SECRETS`; post is a Draft | Actions tab: is **social-publish** listed and enabled? Is the post Scheduled? Run mode **dry-run** and read the summary |
| A run says "invalid" for a post | Over the length limit, JPEG needed, unknown account, platform not built, bad time | The reason is in the run summary and on the Failed/Queue page; edit the post |
| "missing secret 'NAME'" | `SOCIAL_SECRETS` lacks that key | Add it to the JSON secret (names are case sensitive) |
| `SOCIAL_SECRETS is not valid JSON` | A stray quote or newline when pasting | Re-create the secret; the value is not echoed |
| HTTP 401 / "rejected" | Expired or revoked token, wrong password | Create a new credential, update `SOCIAL_SECRETS`, press Retry |
| HTTP 403 / "permissions" | Missing scope, app not approved for this account | PLATFORMS.md; for Meta check the app role and permissions |
| HTTP 429 / "rate limited" | Too many requests or posts | Retried automatically after 15 / 60 / 240 minutes, 3 attempts |
| Status **uncertain** | The platform did not answer in time, or a run was cut off mid-send | Open the platform. There: Resolve, paste the link. Not there: Resolve, leave empty, then run the queue |
| Post "missed its time" | Run was delayed or Actions was off for more than 24 h | Reschedule, or Publish now |
| Post skipped as duplicate | Same text and media was posted to that account in the last 24 h | Change the text, or wait |
| Instagram: image rejected | Not JPEG, or the address is not public | Convert to `.jpg`; open the link in a private window |
| Dashboard: "GitHub said 404" | Wrong owner/repo/folder, or token has no access | Setup page; check the token's repository list |
| Dashboard: "changed since it was loaded" | Someone (or a run) changed that file | Reload, then redo the edit |
| Dashboard: "403 ... rate limit" | Too many API calls | Wait a few minutes |
| Cancel does not seem to work | Cancel is a request applied when the next run starts | Overview, Dry run, or wait for the next hourly run |
| Run is slow to start | GitHub's scheduler delay | See Timing in SETUP.md; use Publish due posts now |
| Queue state conflicts in git | Dashboard commit and a run overlapped | The sync script pulls with rebase and retries 5 times; if it still fails the run stops before sending anything unrecorded, and the next run continues |

Recovering by hand: all state is plain JSON in `social/queue`. To forget a state file (for example after you manually cleaned up the platform) delete `queue/state/<id>.json`, which makes the post eligible again; do that only when you are sure nothing was posted.
