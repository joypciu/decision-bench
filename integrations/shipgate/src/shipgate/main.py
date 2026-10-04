from __future__ import annotations

import os

import uvicorn

from shipgate.app import create_app


app = create_app()


def serve() -> None:
    uvicorn.run(
        "shipgate.main:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8010")),
        reload=False,
    )


def main() -> None:
    serve()
