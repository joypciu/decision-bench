"""Isolated server for browser tests, with a temporary database and demo provider."""
from pathlib import Path
import sys
import tempfile

import uvicorn
from decision_bench.app import create_app
from decision_bench.config import Settings

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="bench-browser-") as directory:
    app = create_app(Settings(root=root, database_path=Path(directory) / "bench.sqlite"))
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="error")
