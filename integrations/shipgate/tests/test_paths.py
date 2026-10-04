from pathlib import Path

from shipgate.paths import bench_root


def test_discovers_consolidated_checkout(monkeypatch):
    monkeypatch.delenv("DECISION_BENCH_ROOT", raising=False)
    assert bench_root() == Path(__file__).resolve().parents[3]


def test_explicit_root_takes_priority(monkeypatch, tmp_path):
    monkeypatch.setenv("DECISION_BENCH_ROOT", str(tmp_path))
    assert bench_root() == tmp_path
