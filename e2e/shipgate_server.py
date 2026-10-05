"""Test-only Shipgate server: demo provider, caller-owned temporary database."""
import os
from pathlib import Path
import sys

import uvicorn

os.environ["DECISION_BENCH_ROOT"] = str(Path(__file__).resolve().parents[1])
os.environ["SHIPGATE_DATA"] = sys.argv[2]
os.environ["SHIPGATE_PROVIDER"] = "demo"
for name in ("GITHUB_APP_ID", "GITHUB_APP_PRIVATE_KEY", "GITHUB_APP_PRIVATE_KEY_PATH", "GITHUB_TOKEN", "GITHUB_WEBHOOK_SECRET"):
    os.environ.pop(name, None)

from shipgate.app import create_app

uvicorn.run(create_app(), host="127.0.0.1", port=int(sys.argv[1]), log_level="error")
