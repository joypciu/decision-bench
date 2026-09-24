from __future__ import annotations

import os

import uvicorn

from decision_bench.app import create_app

app = create_app()


def main() -> None:
    uvicorn.run(
        app,
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
    )
