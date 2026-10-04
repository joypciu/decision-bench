import os
from pathlib import Path

from shipgate.review import Reviewer


AUTH = """diff --git a/auth.py b/auth.py
--- a/auth.py
+++ b/auth.py
@@ -4,7 +4,7 @@ def allow(user):
-    if user.is_authenticated:
+    if True:  # bypass auth
         return True
     return False
"""


def bench_root() -> Path:
    env = os.environ.get("DECISION_BENCH_ROOT")
    if env:
        return Path(env)
    sibling = Path(__file__).resolve().parents[2] / "decision-bench"
    if (sibling / "packs").is_dir():
        return sibling
    return Path("decision-bench")


def test_demo_change_lead_blocks_an_auth_bypass(tmp_path: Path):
    reviewer = Reviewer.open(
        bench_root=bench_root(),
        database=tmp_path / "decision_bench.sqlite",
    )
    review = reviewer.review(AUTH)
    assert review.status == "succeeded"
    assert review.verdict == "block"
    assert review.risks[0].file == "auth.py"
    assert review.risks[0].severity == "high"
