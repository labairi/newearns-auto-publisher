"""TEMPORARY (removed before review): PR #10's post-3699 file through the real gates online:
live WordPress posts (public reads only), real link checks. No Claude call, no WordPress write."""
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from publisher import ledger, quality, run  # noqa: E402
from publisher.wordpress import WordPress  # noqa: E402

cfg = yaml.safe_load(open("config.yaml"))
wp = WordPress(cfg["site"]["base_url"])  # no credentials: public reads only
art = json.load(open("data/revisions/post-3699/article.json"))
posts = run.existing_posts(wp)
known = {p["slug"] for p in posts} | ledger.logged_slugs()
print("live posts:", [(p["id"], p["slug"]) for p in posts])
own = run.confirmed_own_post(wp, art)
fcs = {"fact-check saved in the file": art.get("fact_check"),
       "refresh run 39602843217": json.load(open(".github/online-gates/refresh-39602843217-fact-check.json"))}
for mode, op in (("as a new post (main today)", None), ("as the correction to post 3699 (this fix)", own)):
    for name, fc in fcs.items():
        r = quality.run_checks(art, cfg, posts, known, fact_check=fc, own_post=op)
        print(f"=== {mode} | {name}: {sum(ok for _, ok, _ in r.results)}/{len(r.results)}")
        for n, ok, d in r.results:
            if not ok or n in ("internal_links_valid", "internal_links_present", "no_broken_links", "slug_unused",
                               "not_near_duplicate", "distinct_search_intent", "keyphrase_not_targeted_before"):
                print(f"  {'ok  ' if ok else 'FAIL'} {n}: {str(d)[:220]}")
