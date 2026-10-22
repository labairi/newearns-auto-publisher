from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .http import Http
from .importer import import_posts
from .platforms import build_adapter
from .prompts import build_prompt
from .runner import run
from .store import Store, display_status, redact
from .timeutil import iso, utcnow
from .validate import validate_accounts, validate_post

ROOT = Path(__file__).resolve().parent.parent


def load_secrets() -> dict[str, str]:
    raw = os.environ.get("SOCIAL_SECRETS", "").strip()
    if not raw:
        return {}
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        print("SOCIAL_SECRETS is not valid JSON (value not shown)", file=sys.stderr)
        sys.exit(2)
    if not isinstance(d, dict):
        print("SOCIAL_SECRETS must be a JSON object of name -> value", file=sys.stderr)
        sys.exit(2)
    return {str(k): str(v) for k, v in d.items()}


def git_checkpoint(msg: str) -> None:
    if os.environ.get("SOCIAL_GIT_SYNC") != "1":
        return
    r = subprocess.run([str(ROOT / "scripts" / "git_sync.sh"), msg], cwd=ROOT.parent)
    if r.returncode != 0:
        raise RuntimeError("could not save the queue state to git; stopping so nothing is sent without a record")


def cmd_validate(store: Store, a) -> int:
    """Config problems always fail. Problems in individual posts are listed, and fail only with --strict,
    because the publisher already refuses an invalid post by itself and must not be blocked by one bad file."""
    cfg_bad = post_bad = 0
    try:
        for e in validate_accounts(store.read_json(store.config / "accounts.json", {"accounts": []})):
            print("accounts:", e); cfg_bad += 1
        accounts = store.accounts()
    except Exception as e:
        print("config:", e); return 1
    for pid in store.post_ids():
        try:
            errs = validate_post(store.load_post(pid), accounts)
        except Exception as e:
            errs = [str(e)]
        for e in errs:
            print(f"{pid}: {e}"); post_bad += 1
    print("ok" if not (cfg_bad or post_bad) else f"{cfg_bad} config problem(s), {post_bad} post problem(s)")
    return 1 if cfg_bad or (post_bad and a.strict) else 0


def cmd_status(store: Store, a) -> int:
    for pid in store.post_ids():
        post = store.load_post(pid); st = store.load_state(pid)
        print(f"{display_status(post, st):10} {pid}  {post.get('title', '')}")
        for acc, t in (st or {}).get("targets", {}).items():
            print(f"             {acc}: {t['status']}" + (f" ({t.get('last_error', '')[:80]})" if t.get("last_error") else ""))
    return 0


def cmd_run(store: Store, a) -> int:
    secrets = load_secrets()
    s = run(store, http=Http(), dry_run=a.dry_run, only_post=a.post, secrets=secrets,
            checkpoint=git_checkpoint if not a.dry_run else None)
    print(json.dumps(s, indent=2, ensure_ascii=False))
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a") as f:
            f.write("### social publisher\n")
            for k in ("published", "failed", "uncertain", "skipped", "would_publish", "invalid", "retry_waiting", "healed", "commands"):
                f.write(f"- {k}: {len(s[k])}\n")
    return 1 if s["failed"] and not a.dry_run and any("will_retry_at" not in x for x in s["failed"]) else 0


def cmd_check_setup(store: Store, a) -> int:
    """Read-only: logs in / reads the account identity on every platform. Never posts."""
    secrets = load_secrets()
    store.secrets = [v for v in secrets.values() if v]
    out, bad = [], 0
    for acc in store.accounts().values():
        row = {"account": acc["id"], "platform": acc["platform"], "checked_at": iso(utcnow())}
        missing = [n for n in (acc.get("secrets") or {}).values() if not secrets.get(n)]
        if missing:
            row.update(ok=False, error="missing secret(s): " + ", ".join(missing))
        else:
            try:
                row.update(ok=True, **{k: v for k, v in build_adapter(acc, secrets, Http()).verify().items() if k in ("identity", "id")})
            except Exception as e:
                row.update(ok=False, error=redact(str(e), store.secrets)[:300])
        bad += 0 if row["ok"] else 1
        out.append(row)
        print(("OK   " if row["ok"] else "FAIL ") + f"{acc['id']} ({acc['platform']}): " + (row.get("identity") or row.get("error", "")))
    p = store.root / "status" / "setup.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"checked_at": iso(utcnow()), "accounts": out}, indent=2) + "\n")
    git_checkpoint("social: setup check")
    return 1 if bad else 0


def cmd_import(store: Store, a) -> int:
    text = Path(a.file).read_text() if a.file and a.file != "-" else sys.stdin.read()
    r = import_posts(store, text)
    print(json.dumps(r, indent=2, ensure_ascii=False))
    git_checkpoint("social: import drafts")
    return 1 if r["rejected"] else 0


def cmd_prompt(store: Store, a) -> int:
    print(build_prompt(store.read_json(store.config / "brand.json", {}) or {}, store.accounts(), a.request, a.count))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="socialpub")
    ap.add_argument("--root", default=str(ROOT))
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate"); v.add_argument("--strict", action="store_true")
    sub.add_parser("status")
    r = sub.add_parser("run"); r.add_argument("--dry-run", action="store_true"); r.add_argument("--post")
    sub.add_parser("check-setup")
    i = sub.add_parser("import"); i.add_argument("file", nargs="?", default="-")
    pr = sub.add_parser("prompt"); pr.add_argument("request"); pr.add_argument("--count", type=int, default=3)
    a = ap.parse_args(argv)
    store = Store(a.root)
    return {"validate": cmd_validate, "status": cmd_status, "run": cmd_run, "check-setup": cmd_check_setup,
            "import": cmd_import, "prompt": cmd_prompt}[a.cmd](store, a)


if __name__ == "__main__":
    sys.exit(main())
