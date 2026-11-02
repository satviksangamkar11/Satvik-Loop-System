"""Shared contract of scripts/source/loop_home.py (WP2 interface). WP3 runs this same file, so the behaviours below must not change without both sides
agreeing. Real processes where a second holder matters; no model call, no network."""
import subprocess
import sys
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[1] / "scripts" / "source")
if SRC not in sys.path:
    sys.path.insert(0, SRC)
import loop_home as lh  # noqa: E402


HOLD_MERGE = (
    "import sys, time; sys.path.insert(0, sys.argv[1]); import loop_home as lh\n"
    "with lh.merge_lock(sys.argv[2]):\n"
    "    print('held', flush=True)\n"
    "    time.sleep(600)\n"
)


def test_gate_slot_raises_timeouterror_when_no_slot_frees_in_time(tmp_path):
    gate = lh.Gate(tmp_path)
    with gate.slot("contract-gate", 1, 1):
        with pytest.raises(TimeoutError):
            with gate.slot("contract-gate", 1, 0.2):
                pass
    with gate.slot("contract-gate", 1, 1):                    # the slot frees when its holder leaves
        pass


def test_lease_acquire_raises_leaseheld_while_a_live_lease_exists(tmp_path):
    lease = lh.Lease(tmp_path)
    gen = lease.acquire("contract-job")
    with pytest.raises(lh.LeaseHeld):
        lease.acquire("contract-job")
    assert issubclass(lh.LeaseHeld, lh.LoopHomeError)
    assert lease.release("contract-job", gen) is True
    assert lease.acquire("contract-job") == gen + 1           # released: the next generation may take it


def test_lease_beat_raises_leaselost_when_the_lease_is_gone(tmp_path):
    with pytest.raises(lh.LeaseLost):
        lh.Lease(tmp_path).beat("never-acquired", 1)


def test_lease_beat_raises_leaselost_when_the_generation_changed(tmp_path):
    lease = lh.Lease(tmp_path)
    old = lease.acquire("contract-gen")
    assert lease.release("contract-gen", old) is True
    new = lease.acquire("contract-gen")
    with pytest.raises(lh.LeaseLost):
        lease.beat("contract-gen", old)
    lease.beat("contract-gen", new)                           # the current generation beats normally


def test_merge_lock_is_home_merge_lock_and_excludes_another_process(tmp_path):
    with lh.merge_lock(tmp_path):
        assert (tmp_path / "merge.lock").exists()
        with pytest.raises(lh.LockTimeout):
            with lh.merge_lock(tmp_path, timeout_s=0.3):
                pass
    holder = subprocess.Popen([sys.executable, "-c", HOLD_MERGE, SRC, str(tmp_path)], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(lh.LockTimeout):
            with lh.merge_lock(tmp_path, timeout_s=0.3):
                pass
    finally:
        holder.kill()
        holder.wait(30)
    with lh.merge_lock(tmp_path, timeout_s=2):               # free once the other process is gone
        pass


def test_registry_recent_returns_the_last_n_records_newest_first(tmp_path):
    lease, registry = lh.Lease(tmp_path), lh.Registry(tmp_path)
    assert registry.recent(5) == []
    for i, state in enumerate(("passed", "failed", "passed")):
        gen = lease.acquire(f"contract-r{i}")
        registry.begin(f"req-{i}", f"contract-r{i}", gen, "base")
        registry.record(f"req-{i}", f"contract-r{i}", gen, state, "base", None)
    rows = registry.recent(2)
    assert [r["job"] for r in rows] == ["contract-r2", "contract-r1"]
    assert [r["state"] for r in rows] == ["passed", "failed"]
    assert all(isinstance(r["finished"], float) for r in rows)
    assert set(rows[0]) >= {"job", "state", "finished"}
    assert registry.recent(0) == []
    assert len(registry.recent(99)) == 3
