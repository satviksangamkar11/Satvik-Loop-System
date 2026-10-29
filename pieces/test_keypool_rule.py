"""Owner rule P1-D14 on the pool the loop really uses (our copy of scripts/image_trials/nvidia_trial.py): ANY failed request cools the key
for one window; 401/402/403 retire it; a success changes nothing. No network, no model call."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "image_trials"))
import nvidia_trial as nt


@pytest.fixture(autouse=True)
def _clean_ledger_env(monkeypatch):
    monkeypatch.delenv("P1_LEDGER", raising=False)               # a loop's own pytest run has P1_LEDGER=1: these tests must use the local pool (P1-D36)


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


@pytest.mark.parametrize("status", [429, 500, 502, 503, 0, 404, 400])
def test_any_failure_cools_the_key_then_another_key_is_used(status):
    clock = Clock()
    pool = nt.KeyPool(["a", "b"], clock=clock, sleep=lambda s: None)
    first, _ = pool.acquire()
    pool.report(first, status)
    second, _ = pool.acquire()
    assert second != first                                            # switched to the other key
    clock.t += 61
    pool2 = nt.KeyPool(["a"], clock=clock, sleep=lambda s: setattr(clock, "t", clock.t + s))
    idx, _ = pool2.acquire()
    pool2.report(idx, status)
    before = clock.t
    pool2.acquire()                                                   # only key: it has to wait out the 60 s cooldown
    assert clock.t - before >= 59


@pytest.mark.parametrize("status", [200, 201])
def test_success_does_not_cool(status):
    clock = Clock()
    pool = nt.KeyPool(["a"], clock=clock, sleep=lambda s: pytest.fail("must not wait"))
    idx, _ = pool.acquire()
    pool.report(idx, status)
    pool.acquire()


def test_rejected_statuses_retire_the_key():
    pool = nt.KeyPool(["a", "b"], clock=Clock(), sleep=lambda s: None)
    idx, _ = pool.acquire()
    pool.report(idx, 401)
    assert pool.live == 1


# ---- chat_stream retry policy (P1-D16): only transient failures switch keys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "source"))
import draft_source as ds


def _run(monkeypatch, replies):
    calls = []
    seq = iter(replies)

    def fake_once(key, body):
        calls.append(key)
        return next(seq)

    monkeypatch.setattr(ds, "_stream_once", fake_once)
    clock = Clock()
    pool = nt.KeyPool([f"k{i}" for i in range(6)], clock=clock, sleep=lambda s: setattr(clock, "t", clock.t + s))   # waiting moves the fake clock
    result = ds.chat_stream(pool, [], "m", "sys", "user")
    return calls, result


@pytest.mark.parametrize("reply", [
    (0, "", {"finish": "length", "error": "incomplete stream (finish=length)"}),
    (0, "", {"finish": "content_filter", "error": "incomplete stream"}),
    (0, "", {"finish": "none", "error": "incomplete stream (finish=none)"}),
    (0, "", {"finish": "deadline", "error": "deadline"}),
    (0, "", {"finish": "too_large", "error": "stream too large"}),
    (400, "", {"error": "HTTP 400"}),
    (404, "", {"error": "HTTP 404"}),
    (413, "", {"error": "HTTP 413"}),
])
def test_replies_that_fail_the_same_everywhere_are_not_retried(monkeypatch, reply):
    calls, _ = _run(monkeypatch, [reply] * 50)
    assert len(calls) == 1


@pytest.mark.parametrize("reply", [(429, "", {"error": "HTTP 429"}), (500, "", {"error": "HTTP 500"}), (503, "", {"error": "HTTP 503"}),
                                   (0, "", {"error": "URLError"}), (401, "", {"error": "HTTP 401"})])
def test_transient_failures_move_to_another_key(monkeypatch, reply):
    good = (200, "text", {"finish": "stop", "usage": None})
    calls, (content, info) = _run(monkeypatch, [reply, good])
    assert len(calls) == 2 and calls[0] != calls[1] and content == "text"


def test_an_incomplete_stream_does_not_cool_the_healthy_key(monkeypatch):
    clock = Clock()
    pool = nt.KeyPool(["a"], clock=clock, sleep=lambda s: pytest.fail("a healthy key must not be cooled"))
    monkeypatch.setattr(ds, "_stream_once", lambda key, body: (0, "", {"finish": "length", "error": "incomplete"}))
    ds.chat_stream(pool, [], "m", "s", "u")
    pool.acquire()                                                    # would sleep (and fail the test) if the key had been cooled


def test_retries_are_bounded_when_every_key_keeps_failing(monkeypatch):
    calls, _ = _run(monkeypatch, [(500, "", {"error": "HTTP 500"})] * 100)
    assert len(calls) <= 3 * 6 + 5


# ---- ledger wiring (P1-D20): env P1_LEDGER=1 swaps the real pool for the shared ledger pool; default is unchanged
def test_default_pool_is_the_local_one(monkeypatch):
    monkeypatch.delenv("P1_LEDGER", raising=False)
    assert type(nt.KeyPool(["a"])).__name__ == "_LocalKeyPool"


def test_ledger_pool_is_shared_spread_and_cools(monkeypatch, tmp_path):
    monkeypatch.setenv("P1_LEDGER", "1")
    monkeypatch.setenv("P1_LEDGER_DIR", str(tmp_path))
    keys = [f"SECRET-{i}" for i in range(4)]
    monkeypatch.setenv("P1_KEY_OFFSET", "0")
    first = nt.KeyPool(keys)
    monkeypatch.setenv("P1_KEY_OFFSET", "2")
    second = nt.KeyPool(keys)                                         # a second "process" sharing the folder, different start key
    assert type(first).__name__ == "_LedgerPool"
    i1, _ = first.acquire()
    i2, _ = second.acquire()
    assert (i1, i2) == (0, 2)
    first.report(i1, 500)                                             # cooled for everybody, not only for this object
    assert all(second.acquire()[0] != 0 for _ in range(3))
    for f in tmp_path.iterdir():
        assert "SECRET-" not in f.read_text(encoding="utf-8", errors="ignore")


def test_ledger_pool_all_dead_raises_the_loops_own_exception(monkeypatch, tmp_path):
    monkeypatch.setenv("P1_LEDGER", "1")
    monkeypatch.setenv("P1_LEDGER_DIR", str(tmp_path))
    pool = nt.KeyPool(["a", "b"])
    for _ in range(2):
        idx, _ = pool.acquire()
        pool.report(idx, 403)
    with pytest.raises(nt.AllKeysUnavailable):
        pool.acquire()


def test_the_image_and_vision_commands_of_the_copied_trial_tool_are_disabled():
    for name in ("cmd_run", "cmd_probe", "cmd_check_key"):
        with pytest.raises(SystemExit, match="DISABLED"):
            getattr(nt, name)(None)


def test_a_refused_block_is_not_replaced_by_the_old_file():
    import fix_loop as fl_
    src = Path(fl_.__file__).read_text(encoding="utf-8")
    assert "refused_paths" in src and "rel not in refused_paths" in src
