"""Tests for pieces/p1_claims.py: tmp folders, fake clocks, no network, no model call."""
import json
import multiprocessing
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import p1_claims as pc

WIN = sys.platform == "win32"


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def hashes(n=14):
    return [pc.key_hash(f"key-{i}") for i in range(n)]


def stamps_total(folder):
    data = json.loads((Path(folder) / "key_ledger.json").read_text(encoding="utf-8"))
    return sum(len(v["stamps"]) for v in data["keys"].values()), data


# ---------------------------------------------------------------- module-level workers (spawn pickles them by name)
def _acquire_worker(folder, n):
    ledger = pc.KeyLedger(folder, per_key=10, offset=os.getpid() % 14)
    h = hashes()
    for _ in range(n):
        assert isinstance(ledger.choose(h), int)


def _claim_holder(folder, path, started):
    claim = pc.ClaimStore(folder).claim([path], "child")
    started.set()
    time.sleep(60)
    claim.release()


# ---------------------------------------------------------------- ledger
def test_offsets_spread_over_keys(tmp_path):
    picks = []
    for off in range(4):
        folder = tmp_path / f"d{off}"
        folder.mkdir()
        picks.append(pc.KeyLedger(folder, offset=off).choose(hashes()))
    assert picks == [0, 1, 2, 3]


def test_per_key_limit_and_wait(tmp_path):
    clock = Clock()
    ledger = pc.KeyLedger(tmp_path, per_key=3, clock=clock)
    h = hashes(1)
    assert [ledger.choose(h) for _ in range(3)] == [0, 0, 0]
    wait = ledger.choose(h)
    assert isinstance(wait, float) and wait == pytest.approx(60.0)
    clock.t += 61
    assert ledger.choose(h) == 0


@pytest.mark.parametrize("status", [429, 500, 0, 404])
def test_any_failure_cools_for_a_minute(tmp_path, status):
    clock = Clock()
    ledger = pc.KeyLedger(tmp_path, clock=clock)
    h = hashes(1)
    assert ledger.choose(h) == 0
    ledger.report(h[0], status)
    assert isinstance(ledger.choose(h), float)
    clock.t += 61
    assert ledger.choose(h) == 0


def test_success_changes_nothing(tmp_path):
    ledger = pc.KeyLedger(tmp_path, clock=Clock())
    h = hashes(1)
    ledger.choose(h)
    ledger.report(h[0], 200)
    assert ledger.choose(h) == 0


def test_dead_status_marker_and_long_cooldown(tmp_path):
    clock = Clock()
    ledger = pc.KeyLedger(tmp_path, clock=clock)
    h = hashes(2)
    assert ledger.choose(h) == 0
    ledger.report(h[0], 401)
    assert (tmp_path / f"dead_{h[0]}.mark").exists()
    _, data = stamps_total(tmp_path)
    assert data["keys"][h[0]]["cool_until"] == pytest.approx(1000.0 + 86400.0)
    other = pc.KeyLedger(tmp_path, clock=clock)                      # a second object sharing the folder
    assert all(other.choose(h) == 1 for _ in range(3))
    clock.t += 400000                                                # a dead key stays dead even after its cooldown
    with pytest.raises(pc.AllKeysUnavailable):
        pc.KeyLedger(tmp_path, clock=clock).choose(h[:1])


def test_report_for_unknown_hash_is_ignored(tmp_path):
    ledger = pc.KeyLedger(tmp_path, clock=Clock())
    h = hashes(1)
    ledger.choose(h)
    ledger.report("0123456789abcdef", 401)
    assert not list(tmp_path.glob("dead_*.mark"))
    assert "0123456789abcdef" not in json.loads((tmp_path / "key_ledger.json").read_text())["keys"]


def test_duplicate_paths_in_one_claim_call_are_one_claim(tmp_path):
    p = tmp_path / "t.py"
    with pc.ClaimStore(tmp_path).claim([p, p, str(p).upper() if WIN else p], "j"):
        pass


def test_all_keys_dead_raises(tmp_path):
    h = hashes(2)
    for x in h:
        (tmp_path / f"dead_{x}.mark").write_bytes(b"")
    with pytest.raises(pc.AllKeysUnavailable):
        pc.KeyLedger(tmp_path).choose(h)


def test_both_dead_writes_failing_calls_exit(tmp_path):
    calls = []

    def bad(*a):
        raise OSError("disk")

    ledger = pc.KeyLedger(tmp_path, clock=Clock(), exit_fn=calls.append)
    h = hashes(1)
    assert ledger.choose(h) == 0                                     # the key is known to the ledger
    ledger._write_json, ledger._make_marker = bad, bad               # now both dead-key writes fail
    with pytest.raises(pc.LedgerError):
        ledger.report(h[0], 403)
    assert calls == [2]


def test_one_dead_write_failing_poisons_pool(tmp_path):
    def bad_marker(path):
        raise OSError("marker")

    pool = pc.LedgerKeyPool(["k1"], tmp_path, offset=0, clock=Clock(), make_marker=bad_marker)
    assert pool.acquire() == (0, "k1")
    with pytest.raises(pc.LedgerError):
        pool.report(0, 401)
    with pytest.raises(pc.LedgerError, match="poisoned"):
        pool.acquire()


def test_corrupt_ledger_is_rebuilt_and_written_back(tmp_path):
    clock = Clock()
    (tmp_path / "key_ledger.json").write_text("{not json", encoding="utf-8")
    ledger = pc.KeyLedger(tmp_path, clock=clock)
    h = hashes(2)
    wait = ledger.choose(h)
    assert isinstance(wait, float) and wait == pytest.approx(60.0)
    json.loads((tmp_path / "key_ledger.json").read_text(encoding="utf-8"))          # valid JSON again
    clock.t += 61
    assert isinstance(ledger.choose(h), int)


@pytest.mark.skipif(not WIN, reason="same-process lock conflict is a Windows handle rule")
def test_lock_timeout(tmp_path):
    held = pc.FileLock(tmp_path / "key_ledger.lock")
    held.acquire()
    try:
        with pytest.raises(pc.LedgerLockTimeout):
            pc.KeyLedger(tmp_path, lock_timeout_s=0.2).choose(hashes(1))
    finally:
        held.release()


def test_pool_is_drop_in_and_never_writes_keys(tmp_path):
    keys = [f"SECRET-KEY-{i}" for i in range(3)]
    pool = pc.LedgerKeyPool(keys, tmp_path, offset=1, clock=Clock())
    index, key = pool.acquire()
    assert (index, key) == (1, "SECRET-KEY-1") and pool.live == 3
    pool.report(index, 429)
    pool.report(99, 500)                                             # unknown index is ignored
    pool.report(0, 403)
    assert pool.live == 2
    for f in tmp_path.iterdir():
        assert "SECRET-KEY" not in f.read_text(encoding="utf-8", errors="ignore")


def test_multiprocessing_contention_keeps_every_stamp(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_acquire_worker, args=(str(tmp_path), 15)) for _ in range(3)]
    try:
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=120)
            assert p.exitcode == 0
    finally:
        for p in procs:
            if p.is_alive():
                p.terminate()
            p.join(30)
    total, data = stamps_total(tmp_path)
    assert total == 45
    assert max(len(v["stamps"]) for v in data["keys"].values()) <= 10


# ---------------------------------------------------------------- claims
def test_claim_overlap_rollback_and_release(tmp_path):
    a, b, c = (tmp_path / n for n in ("a.py", "b.py", "c.py"))
    store = pc.ClaimStore(tmp_path)
    first = store.claim([a, b], "j1")
    with pytest.raises(pc.ClaimConflict) as err:
        store.claim([c, b], "j2")                                    # c is free, b is taken: c must be rolled back
    assert pc.canonical_path(b) in str(err.value)
    store.claim([c], "j3").release()                                 # c was released by the failed call
    first.release()
    with store.claim([a, b], "j4"):
        pass
    store.claim([a, b], "j5").release()                              # context manager freed them


def test_claim_spellings_conflict(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                                      # relative spelling must be on the same drive
    p = tmp_path / "x.py"
    held = pc.ClaimStore(tmp_path).claim([p], "j")
    variants = [str(p), str(p).replace("\\", "/"), "x.py", os.path.join(".", "x.py")]
    if WIN:
        variants.append(str(p).upper())
    for v in variants:
        with pytest.raises(pc.ClaimConflict):
            pc.ClaimStore(tmp_path).claim([v], "other")
    held.release()


@pytest.mark.skipif(not WIN, reason="OS byte-range locks are the Windows rule")
def test_claim_of_killed_child_becomes_free(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    started = ctx.Event()
    target = tmp_path / "t.py"
    child = ctx.Process(target=_claim_holder, args=(str(tmp_path), str(target), started))
    child.start()
    try:
        assert started.wait(60)
        with pytest.raises(pc.ClaimConflict):
            pc.ClaimStore(tmp_path).claim([target], "parent")
    finally:
        child.terminate()
        child.join(30)
    for _ in range(50):
        try:
            pc.ClaimStore(tmp_path).claim([target], "parent").release()
            return
        except pc.ClaimConflict:
            time.sleep(0.1)
    pytest.fail("claim was not freed after the holder died")


# ---------------------------------------------------------------- launcher
STUB = '''
import json, os, sys
from pathlib import Path
sys.path.insert(0, {pieces!r})
import p1_claims as pc
job = sys.argv[sys.argv.index("--job") + 1]
try:
    pc.run_job(job, {repo!r}, claims_dir={claims!r}, loop_script=sys.argv[0], environ={{}})
    again = "no conflict"
except pc.ClaimConflict as exc:
    again = "conflict"
Path({out!r}).write_text(json.dumps({{"argv": sys.argv, "opts": os.environ.get("PYTEST_ADDOPTS", ""), "ledger": os.environ.get("P1_LEDGER"), "again": again}}))
raise SystemExit(42)
'''


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "scripts" / "source").mkdir(parents=True)
    (repo / "_loop").mkdir()
    job = repo / "_loop" / "job.json"
    job.write_text(json.dumps({"name": "j", "files": ["m.py", "t.py"], "tests": ["t.py"], "fixers": ["nemotron"] * 3}), encoding="utf-8")
    out = tmp_path / "out.json"
    (repo / "scripts" / "source" / "fix_loop.py").write_text(
        STUB.format(pieces=str(Path(__file__).resolve().parent), repo=str(repo), claims=str(repo / "_loop"), out=str(out)),
        encoding="utf-8")
    return repo, job, out


def test_run_job_holds_claims_and_returns_exit_code(tmp_path, monkeypatch):
    repo, job, out = make_repo(tmp_path)
    for k in ("PYTEST_ADDOPTS", "P1_LEDGER", "P1_KEY_OFFSET"):
        monkeypatch.delenv(k, raising=False)
    assert pc.run_job(job, repo, environ=os.environ) == 42              # the stub runs in this process and reads the real environment
    seen = json.loads(out.read_text())
    assert seen["again"] == "conflict"                               # the same job could not be claimed while it ran
    assert "--job" in seen["argv"] and str(repo) in seen["argv"]
    assert "-p no:cacheprovider" in seen["opts"] and seen["ledger"] == "1"      # set INSIDE the run
    assert "P1_LEDGER" not in os.environ and "PYTEST_ADDOPTS" not in os.environ and "P1_KEY_OFFSET" not in os.environ   # nothing leaks afterwards
    pc.ClaimStore(repo / "_loop").claim([job, repo / "m.py", repo / "t.py"], "later").release()   # all released afterwards


def test_run_job_conflict_before_the_loop_runs(tmp_path):
    repo, job, out = make_repo(tmp_path)
    held = pc.ClaimStore(repo / "_loop").claim([repo / "m.py"], "someone")
    try:
        with pytest.raises(pc.ClaimConflict):
            pc.run_job(job, repo, environ={})
        assert not out.exists()
        assert pc.main(["run", str(job), str(repo)]) == 4
    finally:
        held.release()


def test_main_returns_the_loop_exit_code(tmp_path, capsys, monkeypatch):
    repo, job, out = make_repo(tmp_path)
    monkeypatch.delenv("P1_LEDGER", raising=False)
    assert pc.main(["run", str(job), str(repo)]) == 42
    assert "P1_LEDGER" not in os.environ
    assert pc.main(["bogus"]) == 2


def test_run_job_refuses_a_job_without_nemotron_only_fixers(tmp_path):
    repo, job, out = make_repo(tmp_path)
    for fixers in (None, [], ["nemotron", "codex"], ["haiku"], "nemotron"):
        data = json.loads(job.read_text())
        data.pop("fixers", None)
        if fixers is not None:
            data["fixers"] = fixers
        job.write_text(json.dumps(data))
        with pytest.raises(ValueError, match="nemotron"):
            pc.run_job(job, repo, environ={})
    assert not out.exists()


def test_report_on_a_corrupt_ledger_does_not_rewrite_it_with_one_key(tmp_path):
    clock = Clock()
    ledger = pc.KeyLedger(tmp_path, clock=clock)
    h = hashes(3)
    ledger.choose(h)
    (tmp_path / "key_ledger.json").write_text("{broken", encoding="utf-8")
    ledger.report(h[0], 500)                                         # must not replace the file with a one-key ledger
    assert (tmp_path / "key_ledger.json").read_text(encoding="utf-8") == "{broken"
    ledger.report(h[1], 401)                                         # a dead key still gets its marker
    assert (tmp_path / f"dead_{h[1]}.mark").exists()
    wait = ledger.choose([x for x in h])                             # the next choose rebuilds with EVERY key cooled
    assert isinstance(wait, float)


def test_run_job_claims_the_status_folder_name_too(tmp_path):
    repo, job, out = make_repo(tmp_path)
    held = pc.ClaimStore(repo / "_loop").claim([repo / "_loop" / "j"], "other job with the same name")
    try:
        with pytest.raises(pc.ClaimConflict):
            pc.run_job(job, repo, environ={})
    finally:
        held.release()


def test_run_job_exit_code_mapping_and_pytest_options(tmp_path, monkeypatch):
    repo, job, out = make_repo(tmp_path)
    script = repo / "scripts" / "source" / "fix_loop.py"
    for body, expected in (("raise SystemExit(None)", 0), ("raise SystemExit('boom')", 1), ("pass", 0)):
        script.write_text(body + "\n", encoding="utf-8")
        assert pc.run_job(job, repo, environ={}) == expected
    seen = tmp_path / "seen_opts.txt"
    code = "import os\nopen(%r, 'a').write(os.environ.get('PYTEST_ADDOPTS', '') + '\\n')\n" % str(seen)
    script.write_text(code, encoding="utf-8")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-q")
    pc.run_job(job, repo, environ=os.environ)
    pc.run_job(job, repo, environ=os.environ)                        # second call must not duplicate the option
    assert seen.read_text().splitlines() == ["-q -p no:cacheprovider"] * 2
    assert os.environ["PYTEST_ADDOPTS"] == "-q"                      # restored after each run


def test_lock_open_failure_is_a_ledger_error(tmp_path):
    ledger = pc.KeyLedger(tmp_path, lock_timeout_s=0.2)
    ledger._lock = pc.FileLock(tmp_path / "no_such_folder" / "key_ledger.lock")      # os.open fails
    with pytest.raises(pc.LedgerError):
        ledger.choose(hashes(1))


def test_a_crashing_loop_is_still_logged_and_the_claims_are_released(tmp_path):
    repo, job, out = make_repo(tmp_path)
    (repo / "docs").mkdir()
    (repo / "scripts" / "source" / "fix_loop.py").write_text("raise OSError('status write failed')\n", encoding="utf-8")
    assert pc.run_job(job, repo, environ={}) == 1
    assert "exit 1" in (repo / "docs" / "LOOP_LOG.md").read_text(encoding="utf-8")
    pc.ClaimStore(repo / "_loop").claim([job, repo / "m.py"], "later").release()
