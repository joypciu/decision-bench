"""Isolated server for browser tests, with a temporary database and demo provider."""
from pathlib import Path
import sys
import tempfile
from dataclasses import replace

import uvicorn
from decision_bench.app import create_app
from decision_bench.config import Settings

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="bench-browser-") as directory:
    app = create_app(Settings(root=root, database_path=Path(directory) / "bench.sqlite"))
    @app.post("/test/evaluation-history")
    def synthetic_history():
        saved = app.state.work.repo.list_evals(1)[0]
        for number in range(24):
            app.state.work.repo.create_eval(replace(saved, id=f"synthetic-history-{number:02}",
                created_at=f"2026-10-05T00:{number:02}:00", pass_count=2 if number % 2 else 0))
        return {"created": 24}
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="error")
