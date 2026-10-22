import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_dashboard  # noqa: E402


def test_dashboard_rules_match_rules_py():
    html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    m = re.search(r"/\*RULES\*/.*?/\*END\*/", html, re.S)
    assert m and m.group(0) == build_dashboard.rules_js(), "run: python scripts/build_dashboard.py"
