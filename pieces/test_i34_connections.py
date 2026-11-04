"""I34 (docs/CLOSURE_CHECKLIST.md): on Windows, rebuild_index could not delete history.db after safe_index had hit a corrupt index: connect()
opened the connection, the PRAGMAs and executescript raised on the corrupt file, and the connection was never closed, so the file stayed
locked ([WinError 32]). Windows locks what Linux does not, so this test counts the connections instead: every connection the index code
opens must be closed by the code itself, on every path, corrupt index included. Fakes only: no network, no model call, no secrets."""
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
sys.path.insert(0, str(REPO / "pieces"))

import fix_loop as fl  # noqa: E402
import job_history as jh  # noqa: E402
from test_fix_loop_wiring import JOB, FakeMerge, env_for  # noqa: E402
from test_p1_events import writes_events  # noqa: E402


class Tracked(sqlite3.Connection):
    """A connection that records whether its own close() was called."""

    closed_by_code = False

    def close(self):
        self.closed_by_code = True
        super().close()


@pytest.fixture
def corrupt_home(tmp_path, monkeypatch):
    """A home with two archived jobs, then history.db replaced by bytes that are not a database. Yields the home and the list of every
    connection opened by sqlite3.connect while the test runs (the tracking starts after the corruption, so the setup is not counted)."""
    env = env_for(tmp_path)
    ids = [fl.run_launched(JOB, env.repo, env.api, env.launcher, merge_queue=FakeMerge(), run=writes_events(state))["job_id"]
           for state in ("escalate", "review-open")]
    assert jh.index_ok(env.home) is True
    for suffix in ("", "-wal", "-shm"):
        try:
            (env.home / (jh.DB_NAME + suffix)).unlink()
        except FileNotFoundError:
            pass
    (env.home / jh.DB_NAME).write_bytes(b"this is not a database" * 100)
    assert jh.index_state(env.home) == "corrupt"

    opened = []
    real_connect = sqlite3.connect

    def tracking_connect(*args, **kwargs):
        kwargs.setdefault("factory", Tracked)
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", tracking_connect)
    yield SimpleNamespace(home=env.home, ids=ids, opened=opened)


def assert_all_closed(opened):
    assert opened, "the index code opened no connection: the test proves nothing"
    left = [conn for conn in opened if not conn.closed_by_code]
    assert not left, f"{len(left)} of {len(opened)} connections to history.db were never closed (Windows keeps the file locked)"


def test_safe_index_on_a_corrupt_index_closes_every_connection(corrupt_home):
    assert jh.safe_index(corrupt_home.home, corrupt_home.ids[0]) is None
    assert_all_closed(corrupt_home.opened)


def test_list_jobs_and_job_view_on_a_corrupt_index_close_every_connection(corrupt_home):
    listing = jh.list_jobs(corrupt_home.home)
    assert listing["index_ok"] is False and len(listing["jobs"]) == 2
    assert jh.job_view(corrupt_home.home, corrupt_home.ids[0])["index_ok"] is False
    assert_all_closed(corrupt_home.opened)


def test_index_job_and_attempts_on_a_corrupt_index_close_every_connection(corrupt_home):
    with pytest.raises(sqlite3.DatabaseError):
        jh.index_job(corrupt_home.home, corrupt_home.ids[0])
    with pytest.raises(sqlite3.DatabaseError):
        jh.attempts(corrupt_home.home)
    assert_all_closed(corrupt_home.opened)


def test_sync_running_and_index_state_on_a_corrupt_index_close_every_connection(corrupt_home):
    assert jh.index_state(corrupt_home.home) == "corrupt"
    jh.sync_running(corrupt_home.home)
    assert_all_closed(corrupt_home.opened)


def test_rebuild_after_safe_index_on_a_corrupt_index_leaves_the_file_unlocked(corrupt_home):
    home = corrupt_home.home
    assert jh.safe_index(home, corrupt_home.ids[0]) is None
    assert_all_closed(corrupt_home.opened)
    assert sorted(jh.rebuild_index(home)) == sorted(corrupt_home.ids)
    assert_all_closed(corrupt_home.opened)
    assert jh.index_ok(home) is True
