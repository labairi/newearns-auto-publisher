import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "git_sync.sh"
pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="git not installed")


def sh(cwd, *a):
    return subprocess.run(a, cwd=cwd, check=True, capture_output=True, text=True).stdout


def test_sync_commits_only_queue_files_and_rebases_over_a_dashboard_commit(tmp_path):
    sh(tmp_path, "git", "init", "-q", "--bare", "-b", "main", "remote.git")
    for n in ("a", "b"):
        sh(tmp_path, "git", "clone", "-q", "remote.git", n)
        sh(tmp_path / n, "git", "config", "user.email", "t@t"); sh(tmp_path / n, "git", "config", "user.name", "t")
    a, b = tmp_path / "a", tmp_path / "b"
    sh(a, "git", "checkout", "-q", "-b", "main"); (a / "social" / "queue").mkdir(parents=True); (a / "social" / "queue" / ".keep").write_text("")
    (a / "README").write_text("x"); sh(a, "git", "add", "-A"); sh(a, "git", "commit", "-qm", "init"); sh(a, "git", "push", "-q", "-u", "origin", "main")
    sh(b, "git", "pull", "-q", "origin", "main"); sh(b, "git", "checkout", "-q", "main")
    (b / "social" / "queue" / "commands").mkdir(); (b / "social" / "queue" / "commands" / "c.json").write_text("{}")
    sh(b, "git", "add", "-A"); sh(b, "git", "commit", "-qm", "dashboard"); sh(b, "git", "push", "-q", "origin", "main")
    (a / "social" / "queue" / "state").mkdir(); (a / "social" / "queue" / "state" / "p.json").write_text("{}")
    (a / "other.txt").write_text("must not be committed")
    subprocess.run(["bash", str(SCRIPT), "social: test"], cwd=a, check=True, capture_output=True)
    files = sh(a, "git", "ls-tree", "-r", "HEAD", "--name-only").split()
    assert "social/queue/state/p.json" in files and "social/queue/commands/c.json" in files and "other.txt" not in files
    sh(b, "git", "pull", "-q", "origin", "main")
    assert (b / "social" / "queue" / "state" / "p.json").exists()
