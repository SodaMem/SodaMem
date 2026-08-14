"""Smoke tests for Protocol v1.0 packaging and runner wiring."""
from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_ROOT = ROOT / "benchmarking" / "protocol_v1.0"


def test_protocol_package_imports_and_apply_is_idempotent(monkeypatch):
    monkeypatch.syspath_prepend(str(PROTOCOL_ROOT))
    monkeypatch.syspath_prepend(str(ROOT))

    protocol_v1 = importlib.import_module("protocol_v1")
    assert hasattr(protocol_v1, "apply")
    protocol_v1.apply()
    assert protocol_v1.is_applied() is True
    protocol_v1.apply()


def test_protocol_runner_help_dry_run():
    runner = PROTOCOL_ROOT / "run_protocol_s500.py"
    assert runner.is_file()
    proc = subprocess.run(
        [sys.executable, str(runner), "--help"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert "--only" in proc.stdout
    assert "Protocol v1.0" in proc.stdout or "LongMemEval" in proc.stdout or "TAS" in proc.stdout or "Typed" in proc.stdout


def test_protocol_archive_ships_answers_and_summary():
    archive = PROTOCOL_ROOT / "ARCHIVE_S500"
    summary = archive / "summary.json"
    answers = archive / "answers_all.jsonl"
    assert summary.is_file(), "summary.json missing from Protocol archive"
    assert answers.is_file(), "answers_all.jsonl missing from Protocol archive"
    n = sum(1 for line in answers.read_text(encoding="utf-8").splitlines() if line.strip())
    assert n == 500, f"expected 500 answer rows, got {n}"
