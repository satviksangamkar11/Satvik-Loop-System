"""I58 (codex audit 2026-11-04, batch D): one atomic JSON writer for every authoritative JSON file (scripts/source/atomic_json.py), with bounded
Windows sharing-violation retries (PermissionError, WinError 32 and 5). Fakes only: a replace that refuses N times, or always. No network.
"""
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
import atomic_json  # noqa: E402
import stage_config as sc  # noqa: E402

# ---------------------------------------------------------------- I58: one atomic writer

REAL_REPLACE = os.replace


class Flaky:
    """A replace that refuses `fails` times with the given error, then does the real os.replace."""

    def __init__(self, fails, exc_factory=lambda: PermissionError(13, "sharing violation (the file is in use)")):
        self.fails, self.calls, self.exc_factory = fails, 0, exc_factory

    def __call__(self, src, dst):
        self.calls += 1
        if self.calls <= self.fails:
            raise self.exc_factory()
        return REAL_REPLACE(src, dst)


def _no_sleep_log():
    slept = []
    return slept, (lambda s: slept.append(s))


def test_I58_a_sharing_violation_that_clears_after_n_tries_is_retried_and_the_write_lands(tmp_path):
    target = tmp_path / "status.json"
    target.write_text('{"old": true}')
    flaky = Flaky(fails=3)
    slept, sleep = _no_sleep_log()
    atomic_json.write_json_atomic(target, {"new": 1}, replace=flaky, sleep=sleep, attempts=10, delay=0.01)
    assert json.loads(target.read_text()) == {"new": 1}
    assert flaky.calls == 4 and len(slept) == 3                 # three refusals, then the fourth try replaced the file
    assert sorted(p.name for p in tmp_path.iterdir()) == ["status.json"]          # no temp file left


def test_I58_a_target_that_never_unlocks_fails_cleanly_and_keeps_the_old_file(tmp_path):
    target = tmp_path / "stage_snapshot.json"
    target.write_text('{"revision": 1}')
    flaky = Flaky(fails=10**6)
    slept, sleep = _no_sleep_log()
    with pytest.raises(atomic_json.SharingViolation, match="stayed locked"):
        atomic_json.write_json_atomic(target, {"revision": 2}, replace=flaky, sleep=sleep, attempts=5, delay=0.01)
    assert flaky.calls == 5 and len(slept) == 5                 # bounded: exactly the configured tries
    assert json.loads(target.read_text()) == {"revision": 1}      # the old file is whole
    assert sorted(p.name for p in tmp_path.iterdir()) == ["stage_snapshot.json"]  # the temp file is removed


def test_I58_winerror_32_and_5_are_sharing_violations_and_other_errors_are_not_retried(tmp_path):
    def winerror(code):
        exc = OSError(code, "windows says no")
        exc.winerror = code
        return exc
    target = tmp_path / "a.json"
    flaky = Flaky(fails=2, exc_factory=lambda: winerror(32))
    slept, sleep = _no_sleep_log()
    atomic_json.write_json_atomic(target, {"a": 1}, replace=flaky, sleep=sleep, attempts=5, delay=0.01)
    assert json.loads(target.read_text()) == {"a": 1} and len(slept) == 2
    flaky5 = Flaky(fails=1, exc_factory=lambda: winerror(5))
    slept5, sleep5 = _no_sleep_log()
    atomic_json.write_json_atomic(tmp_path / "b.json", {"b": 1}, replace=flaky5, sleep=sleep5, attempts=5, delay=0.01)
    assert len(slept5) == 1
    other = Flaky(fails=1, exc_factory=lambda: FileNotFoundError(2, "gone"))
    slept_other, sleep_other = _no_sleep_log()
    with pytest.raises(FileNotFoundError):
        atomic_json.write_json_atomic(tmp_path / "c.json", {"c": 1}, replace=other, sleep=sleep_other, attempts=5, delay=0.01)
    assert slept_other == [] and other.calls == 1               # raised at once, not retried


def test_I58_the_stage_snapshot_and_the_status_writers_go_through_the_same_writer(tmp_path, monkeypatch):
    """A Windows-style sharing violation on the job's snapshot write clears after two tries: the write lands (no abort of the paid job)."""
    snap, _ = sc.snapshot("test")
    flaky = Flaky(fails=2)
    monkeypatch.setattr(os, "replace", flaky)
    try:
        sc.write_snapshot(tmp_path / "job", snap)
    finally:
        monkeypatch.setattr(os, "replace", REAL_REPLACE)
    assert flaky.calls >= 3
    assert sc.read_snapshot(tmp_path / "job")["revision"] == snap["revision"]


