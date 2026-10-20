"""Entry point.

  python -m publisher.run check-setup             verify credentials and permissions (no writes)
  python -m publisher.run article --dry-run       research + write + check, save locally, no WordPress writes
  python -m publisher.run article                 full run using publishing.mode from config.yaml
  python -m publisher.run article --resume        continue the last crashed run from its checkpoint
  python -m publisher.run article --pillar pdf --topic "How to Compress a PDF for Email"
                                                  write one approved calendar topic (required to go live)
  python -m publisher.run article --article-file x.json --dry-run   run the gates on a given article
                                                  (x.json may also be a run's data/runs/<id>/state.json)
  python -m publisher.run article --article-file x.json --dry-run --refresh-fact-check
                                                  re-run only the fact-check on an edited article
                                                  (one Claude call pair, no new article), then the gates
  python -m publisher.run report                  Search Console / GA4 improvement report
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import images, ledger, performance, quality, sources, writer
from .wordpress import WordPress, WordPressError, public_url_ok

ROOT = Path(__file__).resolve().parent.parent


MODES = ("review", "draft", "publish")


def load_cfg() -> dict:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    if os.environ.get("WP_BASE_URL"):
        cfg["site"]["base_url"] = os.environ["WP_BASE_URL"].rstrip("/")
    return cfg


def effective_mode(cfg: dict, dry_run: bool) -> str:
    """dry-run beats everything; publish needs ALLOW_PUBLISH=true as a second key."""
    if dry_run:
        return "dry-run"
    mode = cfg["publishing"].get("mode", "draft")
    if mode not in MODES:
        raise ValueError(f"publishing.mode must be one of {MODES}, got {mode!r}")
    if mode == "publish" and os.environ.get("ALLOW_PUBLISH", "").lower() != "true":
        say("publishing.mode is 'publish' but ALLOW_PUBLISH is not 'true': saving as draft instead.")
        return "draft"
    return mode


def say(msg: str) -> None:
    print(msg, flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(msg + "\n\n")


# ---------------------------------------------------------------- helpers
def existing_posts(wp: WordPress) -> list[dict]:
    out = []
    for p in wp.all_posts():
        meta = p.get("aioseo_meta_data") or {}
        kp = ((meta.get("keyphrases") or {}).get("focus") or {}).get("keyphrase", "")
        out.append({"id": p["id"], "slug": p["slug"], "link": p["link"],
                    "title": quality.parse(p["title"]["rendered"]).text,
                    "text": quality.parse(p["content"]["rendered"]).text, "focus_keyphrase": kp})
    return out


def choose_pillar(cfg: dict) -> str:
    """Weighted rotation: pick the pillar most under its target share in the last 12 runs."""
    recent = [e.get("pillar") for e in ledger.read_log()[-12:] if e.get("status") == "published"]
    def deficit(name):
        share = recent.count(name) / len(recent) if recent else 0
        return cfg["pillars"][name]["weight"] - share
    order = sorted(cfg["pillars"], key=lambda n: (-deficit(n), n))
    if recent and order[0] == recent[-1] and len(order) > 1 and deficit(order[1]) > 0:
        return order[1]  # avoid the same pillar twice in a row when another is also behind
    return order[0]


def calendar_entry(cfg: dict, pillar: str | None, topic: str | None) -> dict | None:
    """The approved calendar entry named by --pillar/--topic, or None if neither was given."""
    if not pillar and not topic:
        return None
    if not (pillar and topic):
        raise ValueError("--pillar and --topic must be given together")
    if pillar not in cfg["pillars"]:
        raise ValueError(f"unknown pillar {pillar!r}; configured pillars: {', '.join(cfg['pillars'])}")
    for e in cfg.get("calendar") or []:
        if e["pillar"] == pillar and e["topic"].strip().lower() == topic.strip().lower():
            return e
    raise ValueError(f"topic {topic!r} is not in the approved calendar for pillar {pillar!r}")


def load_article_file(path: Path) -> tuple[dict, dict | None, str]:
    """An article JSON (with its fact_check), or a run's state.json, so a held article can be
    corrected and revalidated without generating it again."""
    data = json.loads(path.read_text())
    if "stages" in data:
        st = data["stages"]
        if not st.get("article"):
            raise ValueError(f"{path} has no generated article")
        return dict(st["article"]), st.get("fact_check"), st.get("pillar") or "pdf"
    return data, data.get("fact_check"), data.get("pillar", "pdf")


def candidate_topics(cfg: dict, pillar: str, posts: list[dict]) -> list[str]:
    done = [p["title"] for p in posts] + [e.get("topic", "") for e in ledger.read_log()]
    return [t for t in cfg["pillars"][pillar]["seed_topics"]
            if all(quality.title_similarity(t, d) < 0.6 for d in done if d)]


def category_for(cfg: dict, pillar: str, live_categories: dict[int, str]) -> int:
    p = cfg["pillars"][pillar]
    cid = cfg["categories"].get(p["category"]) or cfg["categories"][p["fallback_category"]]
    if cid not in live_categories:
        raise RuntimeError(f"category id {cid} for pillar {pillar} does not exist on the site")
    return cid


def product_links(cfg: dict, html: str) -> list[str]:
    hosts = {p["url"].split("/")[2] for p in cfg["products"].values()}
    return [l["href"] for l in quality.parse(html).links if l["href"].split("/")[2:3] and l["href"].split("/")[2] in hosts]


# ---------------------------------------------------------------- commands
def cmd_check_setup(cfg: dict) -> int:
    ok = True
    wp = WordPress.from_env(cfg["site"]["base_url"])
    say(f"Site: {cfg['site']['base_url']}")
    say(f"Publishing mode: config={cfg['publishing']['mode']} effective={effective_mode(cfg, False)}")
    problem = writer.api_key_problem()
    say(f"ANTHROPIC_API_KEY: {'OK' if not problem else 'PROBLEM - ' + problem}")
    ok &= problem is None
    try:
        me = wp.whoami()
        caps = me.get("capabilities", {})
        need = ["edit_posts", "publish_posts", "upload_files"]
        missing = [c for c in need if not caps.get(c)]
        say(f"WordPress login: OK as '{me['name']}' roles={me.get('roles')} missing caps={missing or 'none'}")
        if "administrator" in me.get("roles", []):
            say("WARNING: this login is an Administrator. Use a dedicated Editor/Author account instead.")
        ok &= not missing
    except WordPressError as e:
        say(f"WordPress login: FAILED ({e})")
        ok = False
    live = wp.categories()
    for key, cid in cfg["categories"].items():
        say(f"category {key}: {cid} -> {'exists' if cid in live else ('not created yet' if cid is None else 'MISSING')}")
    say(f"Search Console/GA4 service account: {'set' if os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON') else 'not set (optional)'}")
    return 0 if ok else 1


def cmd_article(cfg: dict, args) -> int:
    mode = effective_mode(cfg, args.dry_run)
    try:
        planned = calendar_entry(cfg, args.pillar, args.topic)
    except ValueError as e:
        say(f"Refused: {e}.")
        return 7
    if planned and args.article_file:
        say("Refused: --pillar/--topic choose a topic to generate; they can't be combined with --article-file.")
        return 7
    if args.refresh_fact_check and not (args.article_file and args.dry_run):
        say("Refused: --refresh-fact-check revalidates a saved article; it needs --article-file and --dry-run.")
        return 7
    if args.refresh_fact_check and writer.api_key_problem():
        say(f"ANTHROPIC_API_KEY {writer.api_key_problem()}; cannot re-run the fact-check.")
        return 2
    if mode == "publish" and not planned and not args.article_file:
        say("Refused: live publishing needs --pillar and --topic from the approved calendar in config.yaml.")
        return 7
    wp = WordPress.from_env(cfg["site"]["base_url"])

    if mode == "publish":
        last = [e for e in ledger.read_log() if e.get("status") == "published"]
        if last:
            age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(last[-1]["date"])).total_seconds() / 3600
            if age_h < cfg["publishing"]["min_hours_between_posts"] and not args.force:
                say(f"Skipped: last article went out {age_h:.0f}h ago (minimum {cfg['publishing']['min_hours_between_posts']}h).")
                return 0

    run_id = (ledger.unfinished_run() if args.resume else None) or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    cp = ledger.Checkpoint(run_id)
    if planned and cp.get("pillar") and (cp.get("pillar"), cp.get("topic")) != (planned["pillar"], planned["topic"]):
        say(f"Refused: unfinished run {run_id} is for {cp.get('pillar')}/{cp.get('topic')!r}, not the topic given. "
            "Run without --pillar/--topic to finish it first.")
        return 7
    say(f"Run {run_id} (mode: {mode})")

    posts = existing_posts(wp)
    live_cats = wp.categories()
    known_slugs = {p["slug"] for p in posts} | ledger.logged_slugs()
    usages = cp.get("usages") or []

    if args.article_file:
        try:
            article, fact, pillar = load_article_file(Path(args.article_file))
        except (OSError, ValueError) as e:
            say(f"Refused: cannot read {args.article_file}: {e}.")
            cp.put("final", "refused")
            return 7
        if args.refresh_fact_check:
            # Validation only: the fact-check stage on the text as edited. Saved under its own
            # stage name so --resume can never mistake it for a generated article.
            try:
                fact, us = writer.fact_check(cfg, article, pillar, reader=sources.SourceReader(
                    cp.dir / "sources", excerpt_chars=cfg["models"]["excerpt_chars"]))
            except Exception:
                cp.put("final", "fact-check-failed")
                raise
            usages.extend(us); cp.put("usages", usages); cp.put("refreshed_fact_check", fact)
            cp.save_file("article.json", json.dumps(dict(article, fact_check=fact), indent=2, ensure_ascii=False))
            say(f"Fact-check re-run on the edited text; article with the new fact-check saved to "
                f"data/runs/{run_id}/article.json")
    else:
        problem = writer.api_key_problem()
        if problem:
            say(f"ANTHROPIC_API_KEY {problem}; cannot generate an article.")
            return 2
        pillar = cp.get("pillar") or (planned["pillar"] if planned else choose_pillar(cfg))
        cp.put("pillar", pillar)
        if planned:
            cp.put("topic", planned["topic"])
        # Pages are cached under the run, so research and fact-check share one download per URL.
        def reader():
            return sources.SourceReader(cp.dir / "sources", excerpt_chars=cfg["models"]["excerpt_chars"])
        if not cp.get("brief"):
            if planned:
                cands = [f"{planned['topic']} (primary query: {planned['query']})"]
            else:
                cands = candidate_topics(cfg, pillar, posts)
            if not cands:
                say(f"No uncovered seed topics left for pillar '{pillar}'. Add topics to config.yaml.")
                return 3
            brief, u = writer.research_brief(cfg, pillar, cands, [p["title"] for p in posts],
                                             performance.demand_notes(cfg), reader=reader())
            usages.append(u); cp.put("usages", usages); cp.put("brief", brief); cp.put("candidates", cands)
        if not cp.get("article"):
            art, u = writer.write_article(cfg, pillar, cp.get("brief"), posts)
            usages.append(u); cp.put("usages", usages); cp.put("article", art)
        article = cp.get("article")
        if not cp.get("fact_check"):
            fc, us = writer.fact_check(cfg, article, pillar, reader=reader())
            usages.extend(us); cp.put("usages", usages); cp.put("fact_check", fc)
        fact = cp.get("fact_check")

    article["image_alt_text"] = images.alt_text(article["image_headline"])
    img_bytes = images.title_card(article["image_headline"], pillar)
    article["featured_image"] = {"alt_text": article["image_alt_text"], "license": "generated-in-house"}
    cp.save_file("featured.webp", img_bytes)
    cp.save_file("article.html", article["content_html"])

    report = quality.run_checks(article, cfg, posts, known_slugs, fact_check=fact)
    cp.put("checks", report.as_dict())
    say(f"Quality gates: {'PASSED' if report.passed else 'FAILED'} ({sum(ok for _, ok, _ in report.results)}/{len(report.results)})")
    for f in report.explained:
        say(f"  - {f}")

    entry = {"run_id": run_id, "date": ledger.now_iso(), "pillar": pillar,
             "topic": article.get("focus_keyphrase"), "title": article["title"], "slug": article["slug"],
             "focus_keyphrase": article["focus_keyphrase"], "product_links": product_links(cfg, article["content_html"]),
             "sources": [s["url"] for s in article.get("sources", [])], "failures": report.failures,
             "est_cost_usd": writer.estimate_cost(usages),
             "est_cost_by_stage": {u.get("stage", f"stage {n}"): writer.stage_cost(u) for n, u in enumerate(usages, 1)}}
    if usages:
        cp.put("cost", {"by_stage": entry["est_cost_by_stage"], "total_usd": entry["est_cost_usd"]})
        say("Estimated Claude API cost (list prices):\n" + "\n".join(writer.cost_table(usages)))

    if mode == "dry-run":
        cp.put("final", "dry-run")
        say(f"Dry run complete. Files in data/runs/{run_id}/ (nothing sent to WordPress). Est. cost ${entry['est_cost_usd']}")
        return 0 if report.passed else 4

    target = {"publish": "publish", "draft": "draft", "review": "pending"}[mode] if report.passed else "pending"
    category = category_for(cfg, pillar, live_cats)

    # Idempotency: a crashed run may already have created the post.
    post = cp.get("wp_post")
    if not post:
        clash = wp.find_by_slug(article["slug"])
        if clash:
            # A post this run did not create already uses the slug: never touch it.
            entry.update(status="held_duplicate", url=clash["link"], post_id=clash["id"],
                         failures=report.failures + [f"slug already used by post {clash['id']} ({clash['status']})"])
            ledger.append_log(entry); cp.put("final", "held_duplicate")
            say(f"Not publishing: slug '{article['slug']}' already belongs to post {clash['id']} ({clash['status']}).")
            return 6
    elif post.get("status") == "publish":
        say(f"Already published at {post['link']}; not publishing twice.")
        cp.put("final", "already-published")
        return 0
    if not post:
        media = cp.get("wp_media") or wp.upload_media(f"{article['slug']}.webp", img_bytes, "image/webp",
                                                     article["image_alt_text"])
        cp.put("wp_media", {"id": media["id"]})
        # Create as draft first, then flip to publish only after the post exists intact.
        post = wp.create_post(title=article["title"], slug=article["slug"], content_html=article["content_html"],
                              excerpt=article["excerpt"], status="draft", category_id=category,
                              featured_media=media["id"], seo_title=article["seo_title"],
                              meta_description=article["meta_description"],
                              focus_keyphrase=article["focus_keyphrase"])
        cp.put("wp_post", {"id": post["id"], "status": post["status"], "link": post["link"]})
        saved_desc = (post.get("aioseo_meta_data") or {}).get("description")
        if saved_desc is not None and saved_desc != article["meta_description"]:
            say("AIOSEO did not store the meta description (the WP role may lack AIOSEO permissions); holding for review.")
            target = "pending"
        if post["slug"] != article["slug"]:
            say(f"WordPress changed the slug to {post['slug']} (collision); holding for review.")
            target = "pending"
    post = wp.set_status(post["id"], target)
    cp.put("wp_post", {"id": post["id"], "status": post["status"], "link": post["link"]})

    if target == "publish":
        verified, why = False, ""
        for wait in (5, 20, 60):
            time.sleep(wait)
            verified, why = public_url_ok(post["link"], article["title"])
            if verified:
                break
        if not verified:
            wp.set_status(post["id"], "pending")
            entry.update(status="failed_verification", url=post["link"], post_id=post["id"], failures=[why])
            ledger.append_log(entry); cp.put("final", "failed_verification")
            say(f"Published URL failed verification ({why}); moved back to review queue.")
            return 5
        entry.update(status="published", url=post["link"], post_id=post["id"])
        say(f"Published and verified: {post['link']}")
    else:
        entry.update(status="held_for_review" if target == "pending" else "draft", url=post["link"], post_id=post["id"])
        say(f"Saved to WordPress as '{target}' (post {post['id']}). Review in WP Admin > Posts.")
    ledger.append_log(entry)
    cp.put("final", entry["status"])
    return 0


def cmd_report(cfg: dict) -> int:
    flags = performance.improvement_flags(cfg, ledger.read_log())
    out = ledger.DATA / "improvement_report.json"
    out.write_text(json.dumps(flags, indent=2))
    say(f"{len(flags)} articles need attention; see {out.relative_to(ROOT)}")
    for f in flags:
        say(f"- {f['title']} ({f['flag']}): {f['advice']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["check-setup", "article", "report"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--force", action="store_true", help="ignore min_hours_between_posts")
    ap.add_argument("--article-file")
    ap.add_argument("--refresh-fact-check", action="store_true",
                    help="with --article-file --dry-run: re-run only the fact-check on the (edited) article")
    ap.add_argument("--pillar", help="pillar of an approved calendar topic (with --topic)")
    ap.add_argument("--topic", help="exact title of an approved calendar topic in config.yaml")
    args = ap.parse_args(argv)
    cfg = load_cfg()
    if args.command == "check-setup":
        return cmd_check_setup(cfg)
    if args.command == "report":
        return cmd_report(cfg)
    with ledger.RunLock():
        return cmd_article(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
