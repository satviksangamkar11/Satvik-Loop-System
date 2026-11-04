"""I57: the daily accounting epoch never runs backwards, so a clock that jumps back across the ledger day does not reset the day counts a second
time; and a recorded owner is alive only as the same process (pid AND process start), so a reused pid does not keep a dead job's lease, gate
slot or paid hold alive. No model call."""
import json
import os
from datetime import datetime, timedelta, timezone

import loop_home as lh
import pytest
import shared_limits as sl

IST = timezone(timedelta(hours=5, minutes=30))


def ist(y, m, d, h, mi):
    return datetime(y, m, d, h, mi, tzinfo=IST).timestamp()


# The ledger day changes at 05:30 IST (day_key shifts the clock back 5 h 30 min).
T_DAY = ist(2026, 11, 4, 12, 0)     # ledger day 2026-11-04
T_NEXT = ist(2026, 11, 5, 6, 0)     # ledger day 2026-11-05
T_BACK = ist(2026, 11, 5, 5, 0)     # the clock went back: ledger day 2026-11-04 again


def test_a_backward_clock_jump_never_resets_the_day_counts_a_second_time(tmp_path):
    st = sl._state(tmp_path, T_DAY)
    st["review_spent"] = 500_000
    sl._save_state(tmp_path, st)
    st = sl._state(tmp_path, T_NEXT)                          # the new day clears the day counts, once
    assert st["review_spent"] == 0 and st["day"] == "2026-11-05"
    st["review_spent"] = 120_000
    sl._save_state(tmp_path, st)
    st = sl._state(tmp_path, T_BACK)                          # the clock is back on the old day: nothing resets again
    assert st["day"] == "2026-11-05" and st["review_spent"] == 120_000
    assert sl._state(tmp_path, T_NEXT + 3600)["review_spent"] == 120_000


def test_the_epoch_is_persisted_and_kept_as_the_latest_instant(tmp_path):
    st = sl._state(tmp_path, T_NEXT)
    sl._save_state(tmp_path, st)
    assert json.loads(sl._path(tmp_path).read_text(encoding="utf-8"))["epoch"] == T_NEXT
    assert sl._state(tmp_path, T_BACK)["epoch"] == T_NEXT


def test_a_live_lease_of_this_process_is_held(tmp_path):
    lease = lh.Lease(tmp_path).acquire("job-live")
    assert lh.Lease(tmp_path).held("job-live") and lease >= 1


def test_a_lease_whose_pid_was_reused_is_not_held(tmp_path):
    if lh.process_start(os.getpid()) is None:
        pytest.skip("this platform gives no process start identity (Linux and Windows do), so a reused pid cannot be told apart here")
    lh.Lease(tmp_path).acquire("job-a")
    path = tmp_path / "leases" / "job-a.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["start"] = "not-this-process"                         # the pid is alive, but it is another process now
    path.write_text(json.dumps(data), encoding="utf-8")
    assert lh.Lease(tmp_path).held("job-a") is False
    assert lh.Claims(tmp_path).take("job-b", lh.Lease(tmp_path).acquire("job-b"), ["x.txt"])[0]   # its claims do not block


def test_a_gate_slot_held_by_a_reused_pid_is_free(tmp_path):
    if lh.process_start(os.getpid()) is None:
        pytest.skip("this platform gives no process start identity (Linux and Windows do), so a reused pid cannot be told apart here")
    (tmp_path / "gates.json").write_text(json.dumps({"sandbox": [{"pid": os.getpid(), "start": "not-this-process", "gen": 1,
                                                                  "token": "old"}]}), encoding="utf-8")
    with lh.Gate(tmp_path).slot("sandbox", 2, timeout_s=0, capacity=1):
        pass                                                   # no TimeoutError: the old holder is dead


def test_a_paid_hold_of_a_reused_pid_is_released_as_spent(tmp_path):
    if lh.process_start(os.getpid()) is None:
        pytest.skip("this platform gives no process start identity (Linux and Windows do), so a reused pid cannot be told apart here")
    now = T_NEXT
    st = {"holds": {"c1": {"job": "j", "usd": 1000, "at": now - sl.HOLD_STALE_S - 10, "pid": os.getpid(), "start": "not-this-process"}},
          "review_spent": 0, "jobs": {}}
    sl._reconcile(st, now)
    assert st["holds"] == {} and st["review_spent"] == 1000
