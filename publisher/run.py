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
  python -m publisher.run article --scheduled     the daily scheduled run: one approved calendar topic,
                                                  live only if every gate passes (see publisher/schedule.py)
  python -m publisher.run resume-schedule         let scheduled runs continue after they paused
  python -m publisher.run image-test --article-file x.json
                                                  try the image step on a saved article: one WordPress
                                                  draft with an image-test- slug; no Claude call, no log
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

from . import images, ledger, performance, quality, schedule, sources, writer
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


def prepare_images(cfg: dict, article: dict, pillar: str, cp: ledger.Checkpoint, query: str | None) -> dict:
    """Pick or draw the article's images once per run; a resumed run reuses them (no new searches)."""
    plan = cp.get("images")
    if not plan or not all((cp.dir / i["file"]).exists() for i in [plan["featured"], *plan["inline"]]):
        plan = images.build(cfg, article, pillar, cp.dir, query=query, say=say)
        cp.put("images", plan)
    article["image_alt_text"] = plan["featured"]["alt_text"]
    article["featured_image"] = {"alt_text": plan["featured"]["alt_text"], "license": plan["featured"]["license"],
                                 **{k: plan["featured"].get(k, "") for k in ("creator", "license_url")}}
    article["inline_images"] = [{"alt_text": i["alt_text"], "license": i["license"]} for i in plan["inline"]]
    say("Images:\n" + "\n".join(images.report(plan)))
    return plan


def upload_images(wp: WordPress, cp: ledger.Checkpoint, plan: dict, featured_bytes: bytes,
                  slug: str, mark_used: bool = True) -> tuple[dict, list[dict]]:
    """Upload to the Media Library once; ids are checkpointed so a resumed run never re-uploads.
    A stock photo used for an article is not picked again for another one."""
    f = plan["featured"]
    media = cp.get("wp_media")
    if not media or "url" not in media:
        m = wp.upload_media(f["filename"], featured_bytes, "image/webp", f["alt_text"], f.get("caption", ""))
        media = {"id": m["id"], "url": m.get("source_url", "")}
        cp.put("wp_media", media)
        if mark_used:
            images.mark_used(plan, slug)
    inline = cp.get("wp_media_inline") or []
    for img in plan["inline"][len(inline):]:
        m = wp.upload_media(img["filename"], (cp.dir / img["file"]).read_bytes(), "image/webp",
                            img["alt_text"], img.get("caption", ""))
        inline.append({"id": m["id"], "url": m.get("source_url", "")})
        cp.put("wp_media_inline", inline)
    return media, inline


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


def cmd_scheduled(cfg: dict, args) -> int:
    """One approved calendar topic per day. Every skip happens before any Claude call."""
    if args.article_file or args.pillar or args.topic or args.dry_run or args.refresh_fact_check or args.force:
        say("Refused: --scheduled picks its own calendar topic; it takes no other article options.")
        return 7
    # A public read, before any skip check: held posts that went live since are logged as published.
    for e in schedule.reconcile(WordPress.from_env(cfg["site"]["base_url"]).all_posts()):
        say(f"Logged post {e['post_id']} as published: it is live at {e['url']} ({e['note']}).")
    why = schedule.skip_reason(cfg)
    if why:
        say(why)
        return 0
    pick = schedule.pick_topic(cfg)
    if not pick:
        say("Skipped: no eligible calendar topic (every approved topic has been attempted). "
            "Add topics to `calendar` in config.yaml.")
        return 0
    entry, run_id = pick
    args.pillar, args.topic = entry["pillar"], entry["topic"]
    args.run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    say(f"Scheduled topic: {entry['pillar']} / {entry['topic']}"
        + (f" (resuming unfinished run {run_id})" if run_id else ""))
    attempt = schedule.start(args.run_id, entry)
    status = "error"
    try:
        rc = cmd_article(cfg, args)
        st = ledger.RUNS / args.run_id / "state.json"
        status = (json.loads(st.read_text())["stages"].get("final") if st.exists() else None) or f"exit {rc}"
        return rc
    finally:
        schedule.finish(attempt, status)


def cmd_article(cfg: dict, args) -> int:
    mode = effective_mode(cfg, args.dry_run)
    if getattr(args, "scheduled", False) and mode == "publish" and not cfg["publishing"].get("scheduled_live"):
        say("Scheduled runs save drafts only until publishing.scheduled_live is approved: saving as draft.")
        mode = "draft"
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

    run_id = getattr(args, "run_id", None) or (ledger.unfinished_run() if args.resume else None) or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
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

    plan = prepare_images(cfg, article, pillar, cp, planned["query"] if planned else None)
    img_bytes = (cp.dir / plan["featured"]["file"]).read_bytes()
    cp.save_file("article.html", article["content_html"])

    report = quality.run_checks(article, cfg, posts, known_slugs, fact_check=fact)
    cp.put("checks", report.as_dict())
    say(f"Quality gates: {'PASSED' if report.passed else 'FAILED'} ({sum(ok for _, ok, _ in report.results)}/{len(report.results)})")
    for f in report.explained:
        say(f"  - {f}")

    entry = {"run_id": run_id, "date": ledger.now_iso(), "pillar": pillar,
             "topic": article.get("focus_keyphrase"), **({"calendar_topic": planned["topic"]} if planned else {}),
             "title": article["title"], "slug": article["slug"],
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
        media, inline = upload_images(wp, cp, plan, img_bytes, article["slug"])
        # Images go in after the gates and the fact-check, which judged the text itself;
        # each diagram only restates a list already in that text.
        content_html = images.insert(article["content_html"], plan, inline)
        # Create as draft first, then flip to publish only after the post exists intact.
        post = wp.create_post(title=article["title"], slug=article["slug"], content_html=content_html,
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
        problems = images.check_in_post(wp.get_post(post["id"]), media, inline)
        cp.put("image_check", problems or "ok")
        if problems:
            say("Images did not land correctly; holding for review:\n" + "\n".join(f"  - {p}" for p in problems))
            target = "pending"
        else:
            say(f"Images uploaded and in the post: featured + {len(inline)} inline.")
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


def cmd_image_test(cfg: dict, args) -> int:
    """Try the image step on a saved article: one WordPress draft with a test slug, nothing published.

    No Claude call. Not an attempt: the publication log, the schedule log and the
    calendar are untouched, so the daily schedule is unaffected."""
    if not args.article_file:
        say("Refused: image-test needs --article-file (a saved article JSON or a run's state.json).")
        return 7
    article, _, pillar = load_article_file(Path(args.article_file))
    article = dict(article, slug=f"image-test-{article['slug']}", title=f"[Image test] {article['title']}")
    wp = WordPress.from_env(cfg["site"]["base_url"])
    clash = wp.find_by_slug(article["slug"])
    if clash:
        say(f"Refused: a test draft already exists (post {clash['id']}, {clash['status']}); trash it in WP Admin first.")
        return 6
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    cp = ledger.Checkpoint(run_id, root=ledger.DATA / "image_tests")
    say(f"Image test {run_id} on {args.article_file} (draft only)")
    plan = prepare_images(cfg, article, pillar, cp, None)
    media, inline = upload_images(wp, cp, plan, (cp.dir / plan["featured"]["file"]).read_bytes(), article["slug"],
                                  mark_used=False)
    post = wp.create_post(title=article["title"], slug=article["slug"],
                          content_html=images.insert(article["content_html"], plan, inline),
                          excerpt=article.get("excerpt", ""), status="draft",
                          category_id=category_for(cfg, pillar, wp.categories()), featured_media=media["id"],
                          seo_title=article["seo_title"], meta_description=article["meta_description"],
                          focus_keyphrase=article["focus_keyphrase"])
    cp.put("wp_post", {"id": post["id"], "status": post["status"], "link": post["link"]})
    problems = images.check_in_post(wp.get_post(post["id"]), media, inline)
    cp.put("image_check", problems or "ok")
    cp.put("final", "image-test-draft")
    say(f"Draft post {post['id']} (status {post['status']}); media ids: featured {media['id']}, "
        f"inline {[m['id'] for m in inline]}")
    if problems:
        say("Image problems:\n" + "\n".join(f"  - {p}" for p in problems))
        return 5
    say("All images uploaded to the Media Library and present in the draft. Preview it in WP Admin > Posts > Drafts, "
        "then move it to the trash.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["check-setup", "article", "report", "resume-schedule", "image-test"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--force", action="store_true", help="ignore min_hours_between_posts")
    ap.add_argument("--article-file")
    ap.add_argument("--refresh-fact-check", action="store_true",
                    help="with --article-file --dry-run: re-run only the fact-check on the (edited) article")
    ap.add_argument("--pillar", help="pillar of an approved calendar topic (with --topic)")
    ap.add_argument("--topic", help="exact title of an approved calendar topic in config.yaml")
    ap.add_argument("--scheduled", action="store_true",
                    help="the daily scheduled run: next calendar topic, 20h gap after any attempt, pause rule")
    args = ap.parse_args(argv)
    cfg = load_cfg()
    if args.command == "check-setup":
        return cmd_check_setup(cfg)
    if args.command == "report":
        return cmd_report(cfg)
    if args.command == "resume-schedule":
        schedule.resume()
        say("Scheduled runs may continue; the 20-hour gap still applies.")
        return 0
    if args.command == "image-test":
        with ledger.RunLock():
            return cmd_image_test(cfg, args)
    with ledger.RunLock():
        return cmd_scheduled(cfg, args) if args.scheduled else cmd_article(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
