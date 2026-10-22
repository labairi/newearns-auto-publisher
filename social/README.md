# Post Queue: a free, personal social publishing queue

A small, original tool that does the useful core of a social media scheduler for one person, at about $0 a month:
you write posts (or import them from the AI assistant you already use), they wait in a queue stored as files in this
repository, and a GitHub Actions workflow publishes them through each platform's official API and records what happened.

It lives in this folder and does not touch the blog publisher at the repository root.

```
dashboard/index.html     the web page (static; talks to GitHub's API with your token)
queue/posts/<id>.json    the content you wrote           (written by you / the dashboard)
queue/state/<id>.json    what happened to each account   (written only by the publisher)
queue/commands/*.json    requests: cancel, retry, publish now, mark published
queue/log.jsonl         append-only record of every attempt
config/*.json            accounts (secret NAMES only), brand voice, settings
socialpub/               the publisher (Python)
../.github/workflows/social-publish.yml   hourly run + manual run
tests/                   119 automated tests (mocked platforms, fake GitHub, real browser)
docs/                    setup, platforms, security, troubleshooting, status, cost
```

**Read `docs/STATUS.md` first**: it says exactly what works, what is partial and what does not exist.
Setup is in `docs/SETUP.md` (about 30 minutes the first time; Bluesky is the quickest platform to start with).

Common commands (run from this folder; the workflow runs the same code):

```
python -m socialpub validate [--strict]     check config and posts
python -m socialpub status                   one line per post
python -m socialpub run --dry-run            show what would be published, send nothing
python -m socialpub run                      publish what is due (needs SOCIAL_SECRETS)
python -m socialpub check-setup              log in / read identity on each account; never posts
python -m socialpub prompt "topic"           build the prompt to paste into your AI assistant
python -m socialpub import answer.json       turn the assistant's answer into drafts
python -m pytest -q                          run the tests
```
