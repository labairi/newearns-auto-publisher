#!/usr/bin/env bash
# Commit queue changes and push, retrying if someone else pushed first (a dashboard commit, for example).
# Only files under social/queue and social/status are ever added, so nothing else can be committed by accident.
set -u
msg="${1:-social: update}"
git config user.name  "social-publisher"
git config user.email "actions@users.noreply.github.com"
for d in social/queue social/status; do [ -e "$d" ] && git add "$d"; done
if git diff --cached --quiet; then exit 0; fi
git commit -q -m "$msg"
for i in 1 2 3 4 5; do
  git pull -q --rebase --autostash && git push -q && exit 0
  sleep $((i * 3))
done
echo "git_sync: could not push after 5 tries" >&2
exit 1
