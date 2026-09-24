from pathlib import Path

import pytest

from decision_bench.app import create_app
from decision_bench.config import Settings

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def app(tmp_path):
    settings = Settings(root=REPO, database_path=tmp_path / "bench.sqlite")
    return create_app(settings)
