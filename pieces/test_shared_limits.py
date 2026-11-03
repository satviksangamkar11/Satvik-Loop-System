"""Tests for scripts/source/shared_limits.py (package E). Real OS processes share one ledger under SATVIK_HOME; no model call, no network.
The 60-second pace test is real time: 16 sends from 4 processes, so exactly one waits for the window to clear."""
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[1] / "scripts" / "source")
if SRC not in sys.path:
    sys.path.insert(0, SRC)
import loop_home as lh  # noqa: E402
import shared_limits as sl  # noqa: E402

WORKER = r'''
import json, sys, time
sys.path.insert(0, sys.argv[1])
import loop_home as lh
import shared_limits as sl
cmd, job, gen, n, p = sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]), json.loads(sys.argv[6])
for name, value in p.get("set", {}).items():
    setattr(sl, name, value)

def outcome(fn):
    try:
        fn()
        return "ok"
    except sl.DailyCapReached:
        return "daily"
    except sl.MinuteFull:
        return "minute"
    except sl.ReviewCapReached:
        return "review"
    except lh.Fenced:
        return "fenced"

if cmd == "free":
    print(json.dumps([outcome(lambda: sl.reserve_free(job, gen)) for _ in range(n)]), flush=True)
elif cmd == "review":
    print(json.dumps([outcome(lambda: sl.reserve_review(job, gen, p["usd"])) for _ in range(n)]), flush=True)
elif cmd == "pace":
    print(json.dumps([sl.pace() for _ in range(n)]), flush=True)
elif cmd == "hold":
    with sl.model_slot(p["model"], gen, p["timeout"]):
        print("in", flush=True)
        time.sleep(600)
elif cmd == "try-slot":
    try:
        with sl.model_slot(p["model"], gen, p["timeout"]):
            print("in", flush=True)
    except TimeoutError:
        print("timeout", flush=True)
'''


@pytest.fixture
def worker_file(tmp_path):
    path = tmp_path / "worker_script" / "limits_worker.py"
    path.parent.mkdir()
    path.write_text(WORKER, encoding="utf-8")
    return path


@pytest.fixture
def shared_home(tmp_path, monkeypatch):
    home = tmp_path / "satvik_home"
    monkeypatch.setenv("SATVIK_HOME", str(home))
    return home


@pytest.fixture
def procs():
    started = []
    yield started
    for proc in started:
        if proc.poll() is None:
            proc.kill()
        proc.wait(30)


def start(worker, home, cmd, job="job-a", gen=1, n=0, **params):
    env = dict(os.environ, SATVIK_HOME=str(home))
    return subprocess.Popen([sys.executable, str(worker), SRC, cmd, job, str(gen), str(n), json.dumps(params)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)


def finish(procs_, timeout=180):
    results = []
    for proc in procs_:
        out, err = proc.communicate(timeout=timeout)
        assert proc.returncode == 0, err
        results.append(json.loads(out.strip().splitlines()[-1]))
    return results


def tally(results):
    counts = {}
    for row in results:
        for item in row:
            counts[item] = counts.get(item, 0) + 1
    return counts


# ---------------------------------------------------------------- the day and the home
def test_the_ledger_day_turns_over_at_0530_ist():
    ist = sl.IST
    before = datetime(2026, 11, 2, 5, 29, 59, tzinfo=ist).timestamp()
    at = datetime(2026, 11, 2, 5, 30, 0, tzinfo=ist).timestamp()
    assert sl.day_key(before) == "2026-11-01"
    assert sl.day_key(at) == "2026-11-02"


def test_the_ledger_needs_satvik_home(monkeypatch):
    monkeypatch.delenv("SATVIK_HOME", raising=False)
    with pytest.raises(lh.HomeRequired):
        sl.reserve_free("job-a", 1)


def test_the_ledger_lives_in_the_home_ledger_folder(shared_home):
    sl.reserve_free("job-a", 1)
    assert (shared_home / "ledger" / "limits.json").is_file()
    assert sl.usage()["free_today"] == 1


# ---------------------------------------------------------------- one ledger, daily cap, minute window
def test_four_processes_share_one_ledger_and_the_daily_cap_holds(shared_home, worker_file, procs):
    spawned = [start(worker_file, shared_home, "free", n=10, set={"FREE_DAILY_CAP": 5}) for _ in range(4)]
    procs.extend(spawned)
    counts = tally(finish(spawned))
    assert counts == {"ok": 5, "daily": 35}                       # 40 attempts, 5 sent in total: one ledger, not four
    usage = sl.usage()
    assert usage["free_today"] == 5
    assert usage["sent_last_60s"] == 5


def test_the_minute_window_caps_free_requests_across_processes(shared_home, worker_file, procs):
    spawned = [start(worker_file, shared_home, "free", n=10) for _ in range(4)]
    procs.extend(spawned)
    counts = tally(finish(spawned))
    assert counts == {"ok": 15, "minute": 25}                     # the account never gets more than 15 in a minute
    assert sl.usage()["sent_last_60s"] == 15


def test_review_spend_stays_within_050_a_day_across_processes(shared_home, worker_file, procs):
    spawned = [start(worker_file, shared_home, "review", n=3, usd=0.2) for _ in range(4)]
    procs.extend(spawned)
    counts = tally(finish(spawned))
    assert counts == {"ok": 2, "review": 10}                       # 0.20 + 0.20 fits; a third 0.20 would be 0.60
    assert sl.usage()["review_reserved_usd"] == pytest.approx(0.4)
    sl.settle_review("job-a", 1, 0.2, 0.05)                        # the unused part of a reservation comes back
    assert sl.usage()["review_reserved_usd"] == pytest.approx(0.2)
    assert sl.usage()["review_spent_usd"] == pytest.approx(0.05)
    sl.reserve_review("job-a", 1, 0.2)                             # 0.05 + 0.20 + 0.20 = 0.45: fits
    with pytest.raises(sl.ReviewCapReached):
        sl.reserve_review("job-a", 1, 0.1)                         # 0.45 + 0.10 = 0.55: refused


def test_a_stale_generation_is_refused(shared_home, worker_file, procs):
    lease = lh.Lease(shared_home)
    gen1 = lease.acquire("job-a")
    assert lease.release("job-a", gen1)
    gen2 = lease.acquire("job-a")
    assert (gen1, gen2) == (1, 2)
    stale = start(worker_file, shared_home, "free", gen=gen1, n=1)
    fresh = start(worker_file, shared_home, "free", gen=gen2, n=1)
    procs.extend([stale, fresh])
    assert finish([stale]) == [["fenced"]]
    assert finish([fresh]) == [["ok"]]
    assert sl.usage()["free_today"] == 1                           # the refused call counted nothing
    review = start(worker_file, shared_home, "review", gen=gen1, n=1, usd=0.1)
    procs.append(review)
    assert finish([review]) == [["fenced"]]
    with pytest.raises(lh.Fenced):
        sl.settle_review("job-a", gen1, 0.1, 0.1)
    assert sl.usage()["review_reserved_usd"] == 0


# ---------------------------------------------------------------- model slots
SLOT_CASES = [
    ("dots-studio/dots-3-note-preview:free", 1),
    ("xingchen-agi/xing4.0-29b-a4b:free", 1),
    ("cohere/north-mini-code:free", 1),
    ("nvidia/nemotron-3-ultra-550b-a55b:free", 1),
    ("z-ai/glm-5.3-flash", 2),
    ("claude-opus", 1),
    ("sandbox", 4),
]


@pytest.mark.parametrize("model,cap", SLOT_CASES)
def test_model_slots_never_exceed_their_count_across_processes(model, cap, shared_home, worker_file, procs):
    holders = [start(worker_file, shared_home, "hold", model=model, timeout=20) for _ in range(cap)]
    procs.extend(holders)
    for holder in holders:
        assert holder.stdout.readline().strip() == "in", holder.stderr.read()
    extra = start(worker_file, shared_home, "try-slot", model=model, timeout=0.5)
    procs.append(extra)
    out, err = extra.communicate(timeout=60)
    assert out.strip().splitlines()[-1] == "timeout", err


def test_a_model_with_no_slot_is_refused(shared_home):
    with pytest.raises(ValueError):
        with sl.model_slot("mystery/model:free", 1, 0.1):
            pass


# ---------------------------------------------------------------- the day's free cap: the live allowance, else the fallback
def test_the_free_cap_is_the_fallback_until_a_live_allowance_is_recorded(shared_home):
    assert sl.usage()["free_cap"] == sl.FREE_DAILY_CAP == 1000
    assert sl.usage()["free_cap_source"] == "fallback"


def test_a_live_allowance_sets_the_cap_and_anything_unusable_falls_back(shared_home):
    assert sl.record_free_cap(7) == {"cap": 7, "source": "live"}
    assert sl.usage()["free_cap"] == 7 and sl.usage()["free_cap_source"] == "live"
    for unusable in (None, 0, -3, "7", True, 2.5):
        assert sl.record_free_cap(unusable) == {"cap": sl.FREE_DAILY_CAP, "source": "fallback"}, unusable


def test_reserve_free_refuses_at_the_live_cap_and_names_its_source(shared_home):
    sl.record_free_cap(2)
    sl.reserve_free("job-a", 1)
    sl.reserve_free("job-a", 1)
    with pytest.raises(sl.DailyCapReached, match=r"cap 2 \(live\)"):
        sl.reserve_free("job-a", 1)
    assert sl.usage()["free_today"] == 2


def test_a_live_cap_stands_across_days_while_the_count_resets(shared_home):
    sl.record_free_cap(5)
    sl.reserve_free("job-a", 1)
    st = sl._state(shared_home, time.time() + 2 * 86400)
    assert st["free_used"] == 0 and sl.free_cap_of(st) == (5, "live")


# ---------------------------------------------------------------- pace: real 60 seconds
def test_pace_sends_at_most_15_in_any_60_seconds_across_processes(shared_home, worker_file, procs):
    spawned = [start(worker_file, shared_home, "pace", n=4) for _ in range(4)]
    procs.extend(spawned)
    sends = sorted(t for row in finish(spawned) for t in row)
    assert len(sends) == 16
    assert sends[15] - sends[0] >= sl.WINDOW_S - 1e-6              # the 16th send waited for the first to leave the window
    for i in range(len(sends) - 15):
        assert sends[i + 15] - sends[i] >= sl.WINDOW_S - 1e-6
