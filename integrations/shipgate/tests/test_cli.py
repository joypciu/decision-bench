from pathlib import Path

from shipgate.cli import review_path


def test_review_command_blocks_the_auth_bypass(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_BENCH_ROOT", str(Path(__file__).resolve().parents[3]))
    monkeypatch.setenv("SHIPGATE_DATA", str(tmp_path))
    diff = tmp_path / "auth.diff"
    diff.write_text(
        """diff --git a/auth.py b/auth.py
--- a/auth.py
+++ b/auth.py
@@ -4,7 +4,7 @@ def allow(user):
-    if user.is_authenticated:
+    if True:  # bypass auth
         return True
     return False
""",
        encoding="utf-8",
    )
    assert review_path(str(diff)) == 1
