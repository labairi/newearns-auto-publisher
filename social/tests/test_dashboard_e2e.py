"""Drives the real dashboard page in Chromium against a fake GitHub API (no network, no real repo)."""
import base64
import json
import re
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.sync_api")
PAGE = (Path(__file__).resolve().parent.parent / "dashboard" / "index.html").as_uri()
CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "*"}


class FakeGitHub:
    def __init__(self):
        self.files, self.dispatches, self.requests, self.fail_status = {}, [], [], None
        self.n = 0

    def put(self, path, obj):
        text = obj if isinstance(obj, str) else json.dumps(obj)
        self.n += 1
        self.files[path] = (text, f"sha{self.n}")

    def handle(self, route, request):
        if request.method == "OPTIONS":
            return route.fulfill(status=204, headers=CORS)
        url = request.url.split("api.github.com", 1)[1]
        self.requests.append((request.method, url, request.headers.get("authorization")))
        if self.fail_status:
            return route.fulfill(status=self.fail_status, headers=CORS, content_type="application/json", body=json.dumps({"message": "Bad credentials"}))
        m = re.match(r"/repos/me/repo/contents/(.+?)(\?.*)?$", url)
        send = lambda status, body: route.fulfill(status=status, headers=CORS, content_type="application/json", body=json.dumps(body))  # noqa: E731
        if m:
            path = m.group(1)
            if request.method == "GET":
                if path in self.files:
                    t, sha = self.files[path]
                    return send(200, {"content": base64.b64encode(t.encode()).decode(), "sha": sha, "name": path.rsplit("/", 1)[-1]})
                kids = [{"name": p[len(path) + 1:], "sha": s} for p, (t, s) in self.files.items() if p.startswith(path + "/") and "/" not in p[len(path) + 1:]]
                return send(200, kids) if kids else send(404, {"message": "Not Found"})
            body = request.post_data_json
            if request.method == "PUT":
                if path in self.files and body.get("sha") != self.files[path][1]:
                    return send(409, {"message": "sha mismatch"})
                self.put(path, base64.b64decode(body["content"]).decode())
                return send(201, {"content": {"sha": self.files[path][1]}})
            if request.method == "DELETE":
                if path not in self.files or body.get("sha") != self.files[path][1]:
                    return send(409, {"message": "sha mismatch"})
                del self.files[path]
                return send(200, {})
        if "/dispatches" in url:
            self.dispatches.append(request.post_data_json)
            return route.fulfill(status=204, headers=CORS)
        if "/runs" in url:
            return send(200, {"workflow_runs": [{"conclusion": "success", "status": "completed", "created_at": "2026-10-25T10:07:00Z", "event": "schedule", "html_url": "https://github.com/me/repo/actions/runs/1"}]})
        return send(404, {"message": "unhandled " + url})


@pytest.fixture
def gh():
    g = FakeGitHub()
    g.put("social/config/accounts.json", {"accounts": [{"id": "bsky", "platform": "bluesky", "secrets": {}}, {"id": "xacct", "platform": "x"}]})
    g.put("social/config/brand.json", {"name": "PrivatePDF", "website": "https://example.com", "facts": ["Files never leave your device"]})
    g.put("social/queue/posts/draft-one.json", {"id": "draft-one", "title": "My draft", "primary": "hello", "intent": "draft", "targets": [{"account": "bsky"}]})
    g.put("social/queue/posts/sent-one.json", {"id": "sent-one", "title": "Sent post", "primary": "done", "intent": "scheduled", "scheduled": {"local": "2026-10-20T10:00", "tz": "UTC"}, "targets": [{"account": "bsky"}]})
    g.put("social/queue/state/sent-one.json", {"id": "sent-one", "cancelled": False, "targets": {"bsky": {"status": "published", "url": "https://bsky.app/profile/me/post/1", "attempts": 1}}})
    g.put("social/queue/posts/bad-one.json", {"id": "bad-one", "title": "<img src=x onerror=window.__xss=1>", "primary": "oops", "intent": "scheduled", "scheduled": {"local": "2026-10-21T10:00", "tz": "UTC"}, "targets": [{"account": "bsky"}]})
    g.put("social/queue/state/bad-one.json", {"id": "bad-one", "cancelled": False, "targets": {"bsky": {"status": "failed", "attempts": 3, "error_kind": "permanent", "last_error": "Bluesky login: rejected (HTTP 401)"}}})
    g.put("social/queue/log.jsonl", '{"ts":"2026-10-21T10:00:00Z","event":"failed","post_id":"bad-one","account":"bsky","error":"rejected"}\n')
    return g


@pytest.fixture
def page(gh):
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context()
        pg = ctx.new_page()
        pg.route("https://api.github.com/**", gh.handle)
        pg.on("pageerror", lambda e: pytest.fail(f"page error: {e}"))
        yield pg
        b.close()


def connect(pg, token="github_pat_TESTTOKEN"):
    pg.goto(PAGE)
    pg.fill("#s_owner", "me"); pg.fill("#s_repo", "repo"); pg.fill("#s_token", token)
    pg.click('[data-act="savecfg"]')
    pg.wait_for_selector("text=Queue runs")


def test_first_visit_asks_for_setup_and_makes_no_request(page, gh):
    page.goto(PAGE)
    assert page.locator("#s_token").is_visible() and gh.requests == []


def test_overview_shows_real_counts_and_no_fake_analytics(page, gh):
    connect(page)
    txt = page.inner_text("#view")
    assert re.search(r"1\s*draft", txt) and re.search(r"1\s*published", txt) and re.search(r"1\s*failed", txt)
    assert "unknown" in txt and "success" in txt
    assert all(h == "Bearer github_pat_TESTTOKEN" for _, _, h in gh.requests)


def test_bad_token_shows_a_clear_error(page, gh):
    gh.fail_status = 401
    page.goto(PAGE)
    page.fill("#s_owner", "me"); page.fill("#s_repo", "repo"); page.fill("#s_token", "bad")
    page.click('[data-act="savecfg"]')
    page.wait_for_selector("#msg .err")
    assert "rejected" in page.inner_text("#msg")


def test_post_content_is_escaped_not_executed(page, gh):
    connect(page)
    page.click('[data-go="failed"]')
    assert "<img src=x" in page.inner_text("#view")
    assert page.evaluate("window.__xss") is None
    assert "Bluesky login: rejected" in page.inner_text("#view")


def test_compose_schedule_and_save_writes_the_post_file(page, gh):
    connect(page)
    page.click('[data-go="compose"]')
    page.fill("#f_title", "Launch day"); page.fill("#f_primary", "We launched. https://example.com")
    page.fill("#f_cta", "Try it"); page.fill("#f_hashtags", "#pdf, privacy")
    page.check('[data-acc="bsky"]')
    page.select_option("#f_intent", "scheduled")
    page.fill("#f_local", "2030-05-01T09:30"); page.select_option("#f_tz", "Africa/Casablanca")
    assert "Looks valid" in page.inner_text("#problems")
    page.click('button[type=submit]')
    page.wait_for_selector("text=Saved and scheduled")
    saved = json.loads(gh.files["social/queue/posts/launch-day.json"][0])
    assert saved["intent"] == "scheduled" and saved["scheduled"] == {"local": "2030-05-01T09:30", "tz": "Africa/Casablanca"}
    assert saved["targets"] == [{"account": "bsky"}] and saved["hashtags"] == ["pdf", "privacy"] and "created_at" in saved


def test_char_count_blocks_scheduling_but_not_drafts(page, gh):
    connect(page)
    page.click('[data-go="compose"]')
    page.fill("#f_title", "Long one"); page.fill("#f_primary", "x" * 301); page.check('[data-acc="bsky"]')
    assert "301 / 300" in page.inner_text("#versions") and page.locator(".cnt.over").count() >= 1
    page.select_option("#f_intent", "scheduled"); page.fill("#f_local", "2030-05-01T09:30")
    page.click('button[type=submit]')
    assert "social/queue/posts/long-one.json" not in gh.files and "Fix the problems" in page.inner_text("#msg")
    page.select_option("#f_intent", "draft"); page.click('button[type=submit]')
    page.wait_for_selector("text=Draft saved")
    assert "social/queue/posts/long-one.json" in gh.files


def test_platform_version_overrides_the_limit(page, gh):
    connect(page)
    page.click('[data-go="compose"]')
    page.fill("#f_title", "Versioned"); page.fill("#f_primary", "y" * 400); page.check('[data-acc="bsky"]')
    page.fill('[data-ver="bluesky"]', "short bluesky text")
    page.select_option("#f_intent", "scheduled"); page.fill("#f_local", "2030-05-01T09:30")
    assert "Looks valid" in page.inner_text("#problems")


def test_dst_gap_time_is_refused(page, gh):
    connect(page)
    page.click('[data-go="compose"]')
    page.fill("#f_title", "Gap"); page.fill("#f_primary", "hi"); page.check('[data-acc="bsky"]')
    page.select_option("#f_intent", "scheduled"); page.select_option("#f_tz", "Europe/Paris"); page.fill("#f_local", "2027-03-28T02:30")
    assert "does not exist in Europe/Paris" in page.inner_text("#problems")
    page.fill("#f_local", "2027-10-31T02:30")
    assert "happens twice" in page.inner_text("#problems")


def test_unbuilt_platform_is_flagged(page, gh):
    connect(page)
    page.click('[data-go="compose"]')
    page.fill("#f_title", "Ex"); page.fill("#f_primary", "hi"); page.check('[data-acc="xacct"]')
    page.select_option("#f_intent", "scheduled"); page.fill("#f_local", "2030-05-01T09:30")
    assert "not built yet" in page.inner_text("#problems")


def test_edit_existing_draft_uses_sha_and_updates_in_place(page, gh):
    connect(page)
    page.click('[data-go="drafts"]'); page.click('[data-act="edit"]')
    page.fill("#f_primary", "edited caption"); page.click('button[type=submit]')
    page.wait_for_selector("text=Draft saved")
    assert json.loads(gh.files["social/queue/posts/draft-one.json"][0])["primary"] == "edited caption"


def test_conflicting_edit_reports_instead_of_overwriting(page, gh):
    connect(page)
    page.click('[data-go="drafts"]'); page.click('[data-act="edit"]')
    gh.put("social/queue/posts/draft-one.json", {"id": "draft-one", "title": "changed elsewhere", "primary": "other", "intent": "draft", "targets": [{"account": "bsky"}]})
    page.fill("#f_primary", "mine"); page.click('button[type=submit]')
    page.wait_for_selector("#msg .err")
    assert "changed since it was loaded" in page.inner_text("#msg")
    assert json.loads(gh.files["social/queue/posts/draft-one.json"][0])["primary"] == "other"


def test_delete_draft_asks_first(page, gh):
    connect(page)
    page.click('[data-go="drafts"]'); page.click('[data-act="delete"]')
    page.click("#dlg button[value=cancel]")
    assert "social/queue/posts/draft-one.json" in gh.files
    page.click('[data-act="delete"]'); page.click("#dlgOk")
    page.wait_for_selector("text=No drafts")
    assert "social/queue/posts/draft-one.json" not in gh.files


def test_publish_due_needs_confirmation_and_sends_both_keys(page, gh):
    connect(page)
    page.click('[data-act="run"][data-mode="publish-due"]'); page.click("#dlg button[value=cancel]")
    assert gh.dispatches == []
    page.click('[data-act="run"][data-mode="publish-due"]'); page.click("#dlgOk")
    page.wait_for_selector("text=Started")
    assert gh.dispatches == [{"ref": "main", "inputs": {"mode": "publish-due", "allow_publish": "true"}}]


def test_dry_run_never_sets_allow_publish(page, gh):
    connect(page)
    page.click('[data-act="run"][data-mode="dry-run"]'); page.wait_for_selector("text=Started")
    assert gh.dispatches[0]["inputs"] == {"mode": "dry-run", "allow_publish": "false"}


def test_retry_writes_a_command_file(page, gh):
    connect(page)
    page.click('[data-go="failed"]'); page.click('[data-act="retry"]'); page.click("#dlgOk")
    page.wait_for_selector("text=Started")
    cmds = [json.loads(t) for p, (t, _) in gh.files.items() if p.startswith("social/queue/commands/")]
    assert cmds == [{"action": "retry", "post_id": "bad-one"}]


def test_ai_import_creates_drafts_only(page, gh):
    connect(page)
    page.click('[data-go="ai"]'); page.fill("#aiReq", "Announce"); page.click('[data-act="mkprompt"]')
    prompt = page.inner_text("#promptOut")
    assert "PrivatePDF" in prompt and "Files never leave your device" in prompt and "bsky (bluesky)" in prompt and "xacct" not in prompt
    page.fill("#aiIn", '```json\n[{"title":"AI idea","primary":"Hi there","targets":[{"account":"bsky"}],"intent":"scheduled","scheduled":{"local":"2030-01-01T10:00","tz":"UTC"},"junk":1},{"title":"Bad","primary":"x","targets":[{"account":"nope"}]}]\n```')
    page.click('[data-act="import"]'); page.wait_for_selector("text=was not imported")
    saved = json.loads(gh.files["social/queue/posts/ai-idea.json"][0])
    assert saved["intent"] == "draft" and "scheduled" not in saved and "junk" not in saved
    assert "social/queue/posts/bad.json" not in gh.files


def test_calendar_shows_scheduled_posts_in_their_own_month(page, gh):
    connect(page)
    page.click('[data-go="calendar"]')
    page.evaluate("V.calY = 2026; V.calM = 9; render()")
    assert "Sent post" in page.locator(".cal").inner_html()


def test_logs_and_accounts_pages_render(page, gh):
    connect(page)
    page.click('[data-go="logs"]'); assert "rejected" in page.inner_text("#view") and "success" in page.inner_text("#view")
    page.click('[data-go="accounts"]'); t = page.inner_text("#view")
    assert "bsky" in t and "never checked" in t and "not built" in t


def test_mobile_width_has_no_horizontal_scroll(page, gh):
    page.set_viewport_size({"width": 390, "height": 800})
    connect(page)
    for r in ("overview", "compose", "queue", "calendar", "logs", "accounts", "media", "ai", "setup"):
        page.click(f'[data-go="{r}"]')
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), r
