"""F10 tests: a reader never sees a half-written file, and a reader and a writer of the same file exclude each other.
tmp folders only, no network, no model call."""
import importlib.util
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import p1_claims as pc

REPO = Path(__file__).resolve().parents[1]


def load_fix_loop():
    spec = importlib.util.spec_from_file_location("fix_loop_race", REPO / "scripts" / "source" / "fix_loop.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def store(tmp_path):
    claims = tmp_path / "claims"
    claims.mkdir()
    return pc.ClaimStore(claims), tmp_path


def test_two_readers_of_the_same_file_coexist(store):
    cs, tmp = store
    target = tmp / "ref.md"
    target.write_text("x", encoding="utf-8")
    a = cs.claim([], "a", reads=[target])
    b = cs.claim([], "b", reads=[target])                  # a second reader is fine
    a.release()
    b.release()


def test_a_reader_blocks_a_writer_and_the_writer_then_gets_in(store):
    cs, tmp = store
    target = tmp / "m.py"
    target.write_text("x", encoding="utf-8")
    reader = cs.claim([], "reader", reads=[target])
    with pytest.raises(pc.ClaimConflict):
        cs.claim([target], "writer")
    reader.release()
    cs.claim([target], "writer").release()                  # after the reader left the writer gets in
    assert not [n for n in os.listdir(cs.dir) if n.startswith("read_")]       # no reader lock file left behind


def test_a_writer_blocks_a_reader(store):
    cs, tmp = store
    target = tmp / "m.py"
    target.write_text("x", encoding="utf-8")
    writer = cs.claim([target], "writer")
    with pytest.raises(pc.ClaimConflict):
        cs.claim([], "reader", reads=[target])
    writer.release()
    cs.claim([], "reader", reads=[target]).release()


def test_a_job_that_writes_and_reads_the_same_file_does_not_block_itself(store):
    cs, tmp = store
    target = tmp / "m.py"
    target.write_text("x", encoding="utf-8")
    cs.claim([target], "job", reads=[target]).release()


def test_a_dead_readers_leftover_lock_file_does_not_block_a_writer(store):
    cs, tmp = store
    target = tmp / "m.py"
    target.write_text("x", encoding="utf-8")
    canon = pc.canonical_path(target)
    stale = Path(cs.dir) / f"read_{cs._hash(canon)}_99999.lock"
    stale.write_bytes(b"")                                 # nobody holds an OS lock on it: its process is gone
    cs.claim([target], "writer").release()
    assert not stale.exists()


def test_a_failed_claim_leaves_no_locks_or_reader_files(store):
    cs, tmp = store
    held = tmp / "held.py"
    free = tmp / "free.py"
    ref = tmp / "ref.md"
    for p in (held, free, ref):
        p.write_text("x", encoding="utf-8")
    first = cs.claim([held], "first")
    with pytest.raises(pc.ClaimConflict):
        cs.claim([free], "second", reads=[ref, held])      # the read of `held` conflicts after `free` and `ref` were taken
    assert not [n for n in os.listdir(cs.dir) if n.startswith("read_")]
    cs.claim([free], "third", reads=[ref]).release()       # `free` and `ref` really were released again
    first.release()


def test_atomic_write_never_exposes_a_half_written_file_to_concurrent_readers(tmp_path):
    fl = load_fix_loop()
    target = tmp_path / "shared.py"
    size = 400_000
    fl._atomic_write(target, "A" * size)
    stop = threading.Event()
    bad = []

    def writer():
        flip = 0
        while not stop.is_set():
            fl._atomic_write(target, ("B" if flip % 2 == 0 else "A") * size)
            flip += 1

    def reader():
        while not stop.is_set():
            try:
                text = target.read_text(encoding="utf-8")
            except OSError:
                continue                                    # a momentary sharing refusal is not a partial read
            if len(text) != size or len(set(text)) != 1:
                bad.append((len(text), set(text)))
                return

    threads = [threading.Thread(target=writer)] + [threading.Thread(target=reader) for _ in range(4)]
    for t in threads:
        t.start()
    time.sleep(2.0)
    stop.set()
    for t in threads:
        t.join(timeout=20)
    assert bad == []
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_write_creates_the_folder_and_replaces_the_text(tmp_path):
    fl = load_fix_loop()
    fl._atomic_write(tmp_path / "sub" / "a.txt", "one")
    fl._atomic_write(tmp_path / "sub" / "a.txt", "two")
    assert (tmp_path / "sub" / "a.txt").read_text(encoding="utf-8") == "two"
    assert [p.name for p in (tmp_path / "sub").iterdir()] == ["a.txt"]


def test_a_reader_in_another_process_blocks_a_writer_until_it_dies(store):
    cs, tmp = store
    target = tmp / "m.py"
    target.write_text("x", encoding="utf-8")
    ready = tmp / "ready.flag"
    code = (
        "import sys, time, pathlib; sys.path.insert(0, %r); import p1_claims as pc\n"
        "cs = pc.ClaimStore(%r); c = cs.claim([], 'other', reads=[%r]); pathlib.Path(%r).write_text('1'); time.sleep(60)\n"
        % (str(Path(__file__).resolve().parent), cs.dir, str(target), str(ready))
    )
    proc = subprocess.Popen([sys.executable, "-c", code])
    try:
        end = time.monotonic() + 30
        while not ready.exists() and time.monotonic() < end:
            time.sleep(0.05)
        assert ready.exists(), "the reader process did not start"
        with pytest.raises(pc.ClaimConflict):
            cs.claim([target], "writer")
    finally:
        proc.kill()
        proc.wait(timeout=20)
    cs.claim([target], "writer").release()                  # the killed reader's lock is gone: the stale file is cleaned and the writer gets in


def test_reconcile_stale_marks_dead_jobs_stopped_and_leaves_live_and_finished_ones(tmp_path):
    import json
    repo = tmp_path / "repo"
    (repo / "_loop" / "jobs").mkdir(parents=True)
    for name, state in (("dead", "running"), ("live", "running"), ("done", "passed")):
        (repo / "_loop" / name).mkdir()
        (repo / "_loop" / name / "status.json").write_text(json.dumps({"name": name, "state": state, "round": 2, "rounds": [{"round": 0}]}), encoding="utf-8")
    (repo / "_loop" / "odd").mkdir()
    (repo / "_loop" / "odd" / "status.json").write_text("[1]", encoding="utf-8")                     # not an object: ignored
    cs = pc.ClaimStore(repo / "_loop")
    live = cs.claim([repo / "_loop" / "live"], "live")                                               # a live process holds this job
    try:
        assert pc.reconcile_stale(repo, note="stopped by the supervisor") == ["dead"]
    finally:
        live.release()
    dead = json.loads((repo / "_loop" / "dead" / "status.json").read_text(encoding="utf-8"))
    assert dead["state"] == "stopped" and "supervisor" in dead["stopped_reason"] and dead["round"] == 2 and dead["rounds"] == [{"round": 0}]
    assert json.loads((repo / "_loop" / "live" / "status.json").read_text(encoding="utf-8"))["state"] == "running"
    assert json.loads((repo / "_loop" / "done" / "status.json").read_text(encoding="utf-8"))["state"] == "passed"
    assert pc.reconcile_stale(repo) == ["live"]                                                      # once the live job is gone it is reconciled too
    assert not list((repo / "_loop").rglob("*.tmp"))
