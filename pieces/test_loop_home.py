"""Tests for scripts/source/loop_home.py (Revision 4, WP2). Real OS processes, no model call, no network.
Workers are small Python programs run with `python -c`-style children; the parent checks the shared files they leave behind."""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[1] / "scripts" / "source")
if SRC not in sys.path:
    sys.path.insert(0, SRC)
import loop_home as lh  # noqa: E402

WORKER = r'''
import json, sys, time
sys.path.insert(0, sys.argv[1])
import loop_home as lh
cmd, home, job = sys.argv[2], sys.argv[3], sys.argv[4]
if cmd == "hold-lease":
    print(lh.Lease(home).acquire(job), flush=True)
    time.sleep(600)
elif cmd == "hold-claim":
    gen = lh.Lease(home).acquire(job)
    print(lh.Claims(home).take(job, gen, [sys.argv[5]])[0], flush=True)
    time.sleep(600)
elif cmd == "hold-begin":
    gen = lh.Lease(home).acquire(job)
    print(lh.Registry(home).begin(sys.argv[5], job, gen, "base1"), flush=True)
    time.sleep(600)
elif cmd == "hold-slot":
    with lh.Gate(home).slot(sys.argv[5], 1, 5):
        print("in", flush=True)
        time.sleep(600)
elif cmd == "hold-lock":
    with lh._locked(home):
        print("locked", flush=True)
        time.sleep(600)
elif cmd == "write":
    path, text, gen, n = sys.argv[5], sys.argv[6], int(sys.argv[7]), int(sys.argv[8])
    results = []
    for i in range(n):
        try:
            lh.fenced_write(path, f"{text}-{i}", job, gen, home_dir=home)
            results.append("ok")
        except lh.Fenced:
            results.append("refused")
    print(json.dumps(results), flush=True)
elif cmd == "take-many":
    gen = lh.Lease(home).acquire(job)
    for i in range(int(sys.argv[5])):
        lh.Claims(home).take(job, gen, [f"pieces/{job}_{i}.py"])
    print("done", flush=True)
    time.sleep(600)                       # keep the claims: a finished process's claims are pruned by the next take
'''


@pytest.fixture
def workers(tmp_path):
    script = tmp_path / "worker_script" / "worker.py"
    script.parent.mkdir()
    script.write_text(WORKER, encoding="utf-8")
    procs = []

    def spawn(*args, out=True):
        proc = subprocess.Popen([sys.executable, str(script), SRC, *map(str, args)], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        procs.append(proc)
        if out:
            line = proc.stdout.readline()
            assert line, proc.stderr.read()
            return proc, line.strip()
        return proc, None

    yield spawn
    for proc in procs:
        if proc.poll() is None:
            proc.kill()
        proc.wait(30)


def backdate(home, job_id, seconds=400):
    path = Path(home) / "leases" / f"{job_id}.json"
    lease = json.loads(path.read_text(encoding="utf-8"))
    lease["heartbeat"] = time.time() - seconds
    path.write_text(json.dumps(lease), encoding="utf-8")


def dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(30)
    return proc.pid


# ---------------------------------------------------------------- home()
def test_home_is_required_for_more_than_one_loop(tmp_path, monkeypatch):
    monkeypatch.delenv("SATVIK_HOME", raising=False)
    with pytest.raises(lh.HomeRequired):
        lh.home(loops=2, fallback=tmp_path)
    with pytest.raises(lh.HomeRequired):
        lh.home()
    assert lh.home(fallback=tmp_path / "_loop") == (tmp_path / "_loop").resolve()   # one loop may fall back to a given folder


def test_home_reads_satvik_home_and_creates_it(tmp_path, monkeypatch):
    monkeypatch.setenv("SATVIK_HOME", str(tmp_path / "shared"))
    assert lh.home(loops=3) == (tmp_path / "shared").resolve()
    assert (tmp_path / "shared").is_dir()


def test_home_refuses_relative_paths_and_folders_inside_a_worktree(tmp_path, monkeypatch):
    monkeypatch.setenv("SATVIK_HOME", "relative/home")
    with pytest.raises(lh.LoopHomeError):
        lh.home()
    monkeypatch.setenv("SATVIK_HOME", str(tmp_path / "wt" / "home"))
    with pytest.raises(lh.LoopHomeError):
        lh.home(worktrees=[tmp_path / "wt"])
    assert lh.home(worktrees=[tmp_path / "other"]).is_dir()


# ---------------------------------------------------------------- job ids and pids
def test_job_ids_have_the_agreed_shape_and_never_repeat(tmp_path):
    ids = [lh.new_job_id("Adhyay 1: draft!", home_dir=tmp_path) for _ in range(60)]
    assert len(set(ids)) == 60
    shape = re.compile(r"^Adhyay-1-draft-\d{8}-\d{4}-[0-9a-f]{4}$")
    assert all(shape.match(i) for i in ids), ids[0]
    assert all((tmp_path / "leases" / f"{i}.json").exists() for i in ids)     # each id is reserved in the home


def test_bad_job_ids_are_refused(tmp_path):
    for bad in ("", "../x", "a/b", "has space", "x" * 200):
        with pytest.raises(ValueError):
            lh.Lease(tmp_path).acquire(bad)


def test_pid_alive_sees_this_process_and_a_finished_one(tmp_path):
    assert lh.pid_alive(os.getpid()) is True
    assert lh.pid_alive(dead_pid()) is False
    assert lh.pid_alive(0) is False


# ---------------------------------------------------------------- leases
def test_acquire_gives_increasing_generations_and_refuses_a_live_holder(tmp_path):
    lease = lh.Lease(tmp_path)
    gen = lease.acquire("job-a")
    assert gen == 1
    with pytest.raises(lh.LeaseHeld):
        lease.acquire("job-a")                                  # same live pid, fresh heartbeat
    assert lease.release("job-a", gen) is True
    assert lease.acquire("job-a") == 2                          # released: a new generation


def test_a_stale_generation_cannot_release_or_beat(tmp_path):
    lease = lh.Lease(tmp_path)
    gen1 = lease.acquire("job-b")
    assert lease.release("job-b", gen1) is True
    gen2 = lease.acquire("job-b")
    assert lease.release("job-b", gen1) is False                # older generation: refused, nothing changes
    with pytest.raises(lh.LeaseLost):
        lease.beat("job-b", gen1)
    lease.beat("job-b", gen2)
    assert lease.release("job-b", gen2) is True


def test_stale_needs_an_old_heartbeat_and_a_dead_pid(tmp_path):
    lease = lh.Lease(tmp_path)
    gen = lease.acquire("job-c")
    backdate(tmp_path, "job-c", 400)                            # heartbeat old, but this process is alive
    assert lease.stale("job-c") is False
    with pytest.raises(lh.LeaseHeld):
        lease.acquire("job-c")
    lease.release("job-c", gen)
    assert lease.stale("job-c") is False                        # released is not stale
    assert lease.stale("never-existed") is False


def test_dead_pid_with_fresh_heartbeat_is_not_taken_over(tmp_path):
    path = tmp_path / "leases" / "job-d.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"job_id": "job-d", "pid": dead_pid(), "started": time.time(), "heartbeat": time.time(),
                                "generation": 4, "released": False}), encoding="utf-8")
    assert lh.Lease(tmp_path).stale("job-d") is False
    with pytest.raises(lh.LeaseHeld):
        lh.Lease(tmp_path).acquire("job-d")


def test_keep_alive_moves_the_heartbeat_forward(tmp_path):
    lease = lh.Lease(tmp_path)
    gen = lease.acquire("job-e")
    backdate(tmp_path, "job-e", 100)
    before = json.loads((tmp_path / "leases" / "job-e.json").read_text())["heartbeat"]
    stop = lease.keep_alive("job-e", gen, every_s=0.05)
    time.sleep(0.4)
    stop()
    after = json.loads((tmp_path / "leases" / "job-e.json").read_text())["heartbeat"]
    assert after > before + 50


# ---------------------------------------------------------------- processes: leases, fencing, two writers
def test_takeover_only_when_the_holder_is_stale_and_dead(tmp_path, workers):
    home = tmp_path / "home"
    holder, gen = workers("hold-lease", home, "job-f")
    assert gen == "1"
    with pytest.raises(lh.LeaseHeld):
        lh.Lease(home).acquire("job-f")                         # the holder process is alive
    holder.kill()
    holder.wait(30)
    with pytest.raises(lh.LeaseHeld):
        lh.Lease(home).acquire("job-f")                         # dead pid, but the heartbeat is fresh
    backdate(home, "job-f", 400)
    assert lh.Lease(home).acquire("job-f") == 2                 # stale heartbeat and dead pid: taken over


def test_stale_writer_is_refused_and_a_fresh_writer_wins(tmp_path, workers):
    home = tmp_path / "home"
    holder, gen1 = workers("hold-lease", home, "job-g")
    holder.kill()
    holder.wait(30)
    backdate(home, "job-g", 400)                                 # stale heartbeat + dead pid
    lease = lh.Lease(home)
    gen2 = lease.acquire("job-g")
    assert (gen1, gen2) == ("1", 2)

    target = str(tmp_path / "result.txt")
    _, stale_out = workers("write", home, "job-g", target, "stale", 1, 5)
    assert json.loads(stale_out) == ["refused"] * 5
    assert not Path(target).exists()                             # nothing from the stale loop reached the disk

    _, fresh_out = workers("write", home, "job-g", target, "fresh", 2, 3)
    assert json.loads(fresh_out) == ["ok"] * 3
    assert Path(target).read_text(encoding="utf-8") == "fresh-2"


def test_two_writers_at_once_one_of_them_stale(tmp_path, workers):
    home = tmp_path / "home"
    holder, _ = workers("hold-lease", home, "job-h")
    holder.kill()
    holder.wait(30)
    backdate(home, "job-h", 400)
    gen2 = lh.Lease(home).acquire("job-h")
    target = str(tmp_path / "shared.txt")
    stale, _ = workers("write", home, "job-h", target, "stale", 1, 40, out=False)
    fresh, fresh_line = workers("write", home, "job-h", target, "fresh", gen2, 40)
    stale_out = stale.stdout.read()
    stale.wait(60)
    fresh.wait(60)
    assert json.loads(stale_out.strip().splitlines()[-1]) == ["refused"] * 40
    assert json.loads(fresh_line) == ["ok"] * 40
    assert Path(target).read_text(encoding="utf-8") == "fresh-39"   # the last fresh write is the final content


# ---------------------------------------------------------------- claims
def test_claims_conflict_across_processes_and_are_all_or_nothing(tmp_path, workers):
    home = tmp_path / "home"
    holder, verdict = workers("hold-claim", home, "holder", "pieces/x.py")
    assert verdict == "True"
    lease = lh.Lease(home)
    gen = lease.acquire("other")
    claims = lh.Claims(home)
    ok, conflicts = claims.take("other", gen, ["pieces/y.py", "pieces/x.py"])
    assert ok is False and conflicts == ["pieces/x.py"]
    ok, conflicts = claims.take("third", lease.acquire("third"), ["pieces/y.py"])     # y was not written by the refused take
    assert ok is True and conflicts == []
    ok, conflicts = claims.take("other", gen, ["./pieces\\x.py"])                     # spelling variants are the same key
    assert ok is False and conflicts == ["pieces/x.py"]
    holder.kill()
    holder.wait(30)
    assert claims.take("other", gen, ["pieces/x.py"]) == (True, [])                 # a dead process frees its claim at once
    assert lh.Lease(home).stale("holder") is False                                  # its lease takeover still waits for staleness


def test_claims_refuse_paths_that_are_not_repo_relative(tmp_path):
    gen = lh.Lease(tmp_path).acquire("job-p")
    for bad in ("/etc/passwd", "C:/x.py", "../escape.py"):
        with pytest.raises(ValueError):
            lh.Claims(tmp_path).take("job-p", gen, [bad])


def test_readers_share_a_file_but_block_a_writer(tmp_path):
    home = tmp_path
    lease = lh.Lease(home)
    claims = lh.Claims(home)
    g1, g2, g3 = lease.acquire("r1"), lease.acquire("r2"), lease.acquire("w1")
    assert claims.take("r1", g1, [], reads=["spec.md"]) == (True, [])
    assert claims.take("r2", g2, [], reads=["spec.md"]) == (True, [])
    ok, conflicts = claims.take("w1", g3, ["spec.md"])
    assert ok is False and conflicts == ["spec.md"]
    claims.release("r1", g1)
    claims.release("r2", g2)
    assert claims.take("w1", g3, ["spec.md"]) == (True, [])


def test_release_frees_every_claim_of_the_job(tmp_path):
    lease, claims = lh.Lease(tmp_path), lh.Claims(tmp_path)
    gen = lease.acquire("owner")
    claims.take("owner", gen, ["a.py", "b.py"])
    assert claims.release("owner", gen) is True
    assert claims.take("other", lease.acquire("other"), ["a.py", "b.py"]) == (True, [])


def test_claims_from_many_processes_lose_nothing(tmp_path, workers):
    home = tmp_path / "home"
    procs = [workers("take-many", home, f"w{i}", 10, out=False)[0] for i in range(4)]
    for proc in procs:
        assert proc.stdout.readline().strip() == "done", proc.stderr.read()
    table = json.loads((home / "claims.json").read_text(encoding="utf-8"))["claims"]
    assert sum(len(v) for v in table.values()) == 40


# ---------------------------------------------------------------- registry
def test_registry_roundtrip_and_fencing(tmp_path):
    lease, registry = lh.Lease(tmp_path), lh.Registry(tmp_path)
    assert registry.lookup("req-1") is None
    gen = lease.acquire("runner")
    assert registry.begin("req-1", "runner", gen, "abc123") == "run"
    registry.record("req-1", "runner", gen, "passed", "abc123", "res999")
    assert registry.lookup("req-1")["state"] == "passed"
    assert registry.lookup("req-1")["result"] == "res999"
    assert lease.release("runner", gen) is True
    gen2 = lease.acquire("runner")
    with pytest.raises(lh.Fenced):
        registry.record("req-1", "runner", gen, "failed", "abc123", None)     # the old generation cannot overwrite
    assert registry.lookup("req-1")["state"] == "passed"
    assert gen2 == 2


def test_repeat_of_a_passed_request_reruns_only_its_tests(tmp_path):
    lease, registry = lh.Lease(tmp_path), lh.Registry(tmp_path)
    g1 = lease.acquire("first")
    registry.begin("req-2", "first", g1, "base-1")
    registry.record("req-2", "first", g1, "passed", "base-1", "res")
    g2 = lease.acquire("second")
    assert registry.begin("req-2", "second", g2, "base-1") == "tests-only"
    registry.record("req-2", "second", g2, "passed", "base-1", "res")
    assert registry.begin("req-2", "third", lease.acquire("third"), "base-2") == "run"   # another base is not the same request


def test_repeat_of_a_running_request_is_refused_while_its_owner_lives(tmp_path, workers):
    home = tmp_path / "home"
    holder, verdict = workers("hold-begin", home, "owner", "req-3")
    assert verdict == "run"
    registry = lh.Registry(home)
    gen = lh.Lease(home).acquire("again")
    with pytest.raises(lh.RequestRunning):
        registry.begin("req-3", "again", gen, "base1")
    holder.kill()
    holder.wait(30)
    assert registry.begin("req-3", "again", gen, "base1") == "run"                # owner process dead: nothing is running


def test_a_passed_request_whose_rerun_died_still_reruns_only_its_tests(tmp_path, workers):
    home = tmp_path / "home"
    lease, registry = lh.Lease(home), lh.Registry(home)
    g1 = lease.acquire("first")
    registry.begin("req-4", "first", g1, "base1")
    registry.record("req-4", "first", g1, "passed", "base1", "res")
    holder, verdict = workers("hold-begin", home, "rerun", "req-4")
    assert verdict == "tests-only"
    holder.kill()
    holder.wait(30)
    backdate(home, "rerun", 400)
    assert registry.begin("req-4", "after", lease.acquire("after"), "base1") == "tests-only"


# ---------------------------------------------------------------- gate and lock
def test_gate_slot_waits_then_times_out_while_the_holder_lives(tmp_path, workers):
    home = tmp_path / "home"
    holder, line = workers("hold-slot", home, "job-i", "nvidia-keys")
    assert line == "in"
    with pytest.raises(TimeoutError):
        with lh.Gate(home).slot("nvidia-keys", 1, 0.3):
            pass
    holder.kill()
    holder.wait(30)
    with lh.Gate(home).slot("nvidia-keys", 1, 2):                # the dead holder's slot is pruned
        pass


def test_gate_capacity_is_respected_in_one_process(tmp_path):
    gate = lh.Gate(tmp_path)
    with gate.slot("pool", 1, 1, capacity=2):
        with gate.slot("pool", 1, 1, capacity=2):
            with pytest.raises(TimeoutError):
                with gate.slot("pool", 1, 0.2, capacity=2):
                    pass
    with gate.slot("pool", 1, 1, capacity=2):                    # slots come back after release
        pass


def test_the_lock_file_is_one_os_lock_for_every_process(tmp_path, workers):
    home = tmp_path / "home"
    holder, line = workers("hold-lock", home, "unused")
    assert line == "locked"
    with pytest.raises(lh.LockTimeout):
        with lh._locked(home, timeout_s=0.3):
            pass
    holder.kill()
    holder.wait(30)
    with lh._locked(home, timeout_s=2):
        pass
    assert (home / "lock").exists()


def test_home_inside_a_git_worktree_is_refused(tmp_path, monkeypatch):
    """Track L: a home inside any git worktree is refused by home() itself (checkouts and merges can wipe it)."""
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    monkeypatch.setenv("SATVIK_HOME", str(repo / "satvik_home"))
    with pytest.raises(lh.LoopHomeError, match="worktree"):
        lh.home()
    monkeypatch.delenv("SATVIK_HOME")
    with pytest.raises(lh.LoopHomeError, match="worktree"):
        lh.home(fallback=repo / "_loop")
    assert not (repo / "satvik_home").exists()
