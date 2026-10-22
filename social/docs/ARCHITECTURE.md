# Architecture

```
 you ── dashboard (static page, token in browser) ──GitHub contents API──▶  queue/posts, queue/commands
                                                                                  │
 GitHub Actions (hourly + manual)  ──▶ socialpub run ──▶ platform APIs            │
        ▲   commit queue/state, log ◀──────────────┘                              │
        └──────────────────────── reads posts and commands ◀──────────────────────┘
```

**Why files in git.** Free, durable, auditable, editable by hand. The cost is that commits are the database: fine for hundreds of posts, not thousands.

**Ownership split (this prevents stale overwrites).** The dashboard writes only `queue/posts/*` and `queue/commands/*`. The publisher writes only `queue/state/*` and `queue/log.jsonl`. Content and runtime state are different files, so a dashboard edit and a run never write the same file. Editing a published post cannot cause a resend because the state records what was sent.

**One run at a time.** The workflow's concurrency group queues runs and never cancels one in progress. Inside a run, every state change is written atomically (temp file + rename) and the sync script commits with `pull --rebase` and retries. There is no lock file: if someone runs the CLI by hand with real secrets at the same moment as the workflow, two writers are possible. Do not do that.

**Safe publishing sequence per account.**
1. Validate the post and decide it is due (UTC derived from local time + zone).
2. Write state `publishing` with an attempt id, commit it.
3. Call the platform. Only the call that creates the public post is "uncertain" if it times out or returns 5xx.
4. On success store the provider id and URL, state `published`. On failure classify: transient (retry later), permanent (stop), uncertain (a person decides).
5. For uncertain outcomes, where the API allows, look for a post with the same text created recently and adopt it instead of reposting (Bluesky only in the real adapters; mock covers the logic).

**Commands.** `cancel`, `retry` (uncertain needs `confirm_not_posted`), `publish_now`, `mark_published`. Applied at the start of a run, then deleted.

**Not included.** A backend, OAuth callback server, webhooks, MCP server: none can run for free and reliably. The command files are the API a future wrapper could use.
