# Security checklist

Tick these before going live.

**Credentials**
- [ ] Platform credentials exist only in the `SOCIAL_SECRETS` GitHub secret. `config/accounts.json` holds names only (`validate` refuses values that look like secrets).
- [ ] Use a Bluesky **app password**, never the main one. Give tokens the minimum scopes in PLATFORMS.md.
- [ ] The dashboard token is fine-grained, limited to this repository, Contents + Actions only, with an expiry.
- [ ] You never paste a token into a post file, an issue, a pull request or a chat.
- [ ] Secret values are replaced with `[redacted]` in logs and state (tested), but do not rely on that as the only protection.

**Who can publish**
- [ ] The workflow triggers only on `schedule` and `workflow_dispatch`. It has **no** `pull_request` trigger, so a pull request (including from a fork) never receives `SOCIAL_SECRETS`.
- [ ] Real publishing only happens on `main`, from the schedule, or a manual run with mode `publish-due` and the `allow_publish` box ticked. Other manual runs are dry runs.
- [ ] Anyone with write access to the repository, or your dashboard token, can queue a post and start a run. Keep the repository private and the collaborator list short. Branch protection on `main` does not stop queue commits made by the workflow itself, which is intended.
- [ ] `social-tests.yml` has `contents: read` only and uses no secrets.
- [ ] Workflow permissions are least privilege: `contents: write` only, to commit queue state.

**Content safety**
- [ ] Media must be https links. For Bluesky and Mastodon the workflow runner downloads the file from the link you gave, so only use links you trust.
- [ ] The dashboard escapes all repository content before showing it (tested with a hostile title).
- [ ] Post files with unknown fields are rejected; post ids must match a strict pattern, so a file name cannot escape `queue/posts`.

**Reliability that protects you**
- [ ] One run at a time (Actions concurrency group, never cancelled mid-run).
- [ ] Attempt is saved and committed **before** calling the platform; a crash cannot silently lead to a double post.
- [ ] Uncertain outcomes are never auto-retried. Temporary errors stop after 3 attempts. Posts that are 24 h late are not sent.
- [ ] Every request has a timeout (10 s connect, 60 s read).

**Known residual risks (not mitigated)**
- The dashboard token sits in that browser's local storage. Do not use a shared computer; delete it when done (Setup, Forget).
- A stolen dashboard token can queue and publish posts until you revoke it.
- Media hosted in a public repository is public.
- If GitHub is down or your Actions minutes run out, nothing is published; posts then wait and may be refused as "too late".
