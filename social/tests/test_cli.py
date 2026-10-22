import json

from socialpub.cli import main
from socialpub.platforms import mock as mockmod
from conftest import make_store, post


def test_cli_validate_status_run_end_to_end(tmp_path, capsys, monkeypatch):
    s = make_store(tmp_path)
    s.save_post(post(local="2020-01-01T10:00", tz="UTC"))
    s.save_post(post("bad-post", accounts=("ghost",)))
    assert main(["--root", str(tmp_path), "validate", "--strict"]) == 1
    assert main(["--root", str(tmp_path), "validate"]) == 0          # a bad post alone does not block the workflow
    out = capsys.readouterr().out
    assert "bad-post: unknown account" in out
    assert main(["--root", str(tmp_path), "run", "--dry-run", "--post", "hello-post"]) == 0
    assert not mockmod.STORE.get("m1")
    monkeypatch.setenv("SOCIAL_SECRETS", "{}")
    main(["--root", str(tmp_path), "run", "--post", "hello-post"])        # far past its time: refused, not posted late
    assert not mockmod.STORE.get("m1")


def test_cli_run_publishes_due_post(tmp_path, capsys):
    from datetime import datetime, timedelta, timezone
    s = make_store(tmp_path)
    local = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M")
    s.save_post(post(local=local, tz="UTC"))
    assert main(["--root", str(tmp_path), "run"]) == 0
    assert len(mockmod.STORE["m1"]) == 1
    main(["--root", str(tmp_path), "status"])
    assert "published" in capsys.readouterr().out


def test_bad_secrets_json_is_reported_without_echoing_it(tmp_path, monkeypatch, capsys):
    import pytest
    make_store(tmp_path)
    monkeypatch.setenv("SOCIAL_SECRETS", "{oops SECRET-VALUE")
    with pytest.raises(SystemExit):
        main(["--root", str(tmp_path), "run"])
    assert "SECRET-VALUE" not in capsys.readouterr().err


def test_check_setup_reports_missing_secret_and_never_posts(tmp_path, monkeypatch, capsys):
    make_store(tmp_path, accounts=[{"id": "t1", "platform": "threads", "secrets": {"access_token": "THREADS_TOKEN"}}])
    monkeypatch.setenv("SOCIAL_SECRETS", "{}")
    assert main(["--root", str(tmp_path), "check-setup"]) == 1
    assert "missing secret(s): THREADS_TOKEN" in capsys.readouterr().out
    assert json.loads((tmp_path / "status" / "setup.json").read_text())["accounts"][0]["ok"] is False


def test_import_and_prompt_commands(tmp_path, capsys, monkeypatch):
    s = make_store(tmp_path)
    f = tmp_path / "ai.json"; f.write_text('[{"title":"Hello","primary":"hi","targets":[{"account":"m1"}]}]')
    assert main(["--root", str(tmp_path), "import", str(f)]) == 0
    assert len(s.post_ids()) == 1 and s.load_post(s.post_ids()[0])["intent"] == "draft"
    main(["--root", str(tmp_path), "prompt", "Announce X"])
    assert "Announce X" in capsys.readouterr().out
