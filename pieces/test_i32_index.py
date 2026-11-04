"""I32 (docs/CLOSURE_CHECKLIST.md): history.db is a rebuildable index of the job files, and nothing rebuilt it, so a missing or corrupt index
stayed unusable (index_ok false) for good. The API now rebuilds it from the files at startup (monitor/api.py lifespan, job_history.ensure_index)
and `python scripts/source/job_history.py rebuild` does it by hand. Fakes only: a scripted run, no network, no model call, no secrets."""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
sys.path.insert(0, str(REPO / "pieces"))
sys.path.insert(0, str(REPO))

import job_history as jh  # noqa: E402
import fix_loop as fl  # noqa: E402
from test_fix_loop_wiring import JOB, FakeMerge, env_for  # noqa: E402
from test_p1_events import writes_events  # noqa: E402

CLI = REPO / "scripts" / "source" / "job_history.py"


def _drop_index(home):
    for suffix in ("", "-wal", "-shm"):
        try:
            (Path(home) / (jh.DB_NAME + suffix)).unlink()
        except FileNotFoundError:
            pass


@pytest.fixture
def archived(tmp_path):
    """A home with one archived job (results/<id>/) and its history.db built by the finish hook."""
    env = env_for(tmp_path)
    job_id = fl.run_launched(JOB, env.repo, env.api, env.launcher, merge_queue=FakeMerge(), run=writes_events("escalate"))["job_id"]
    assert jh.index_ok(env.home) is True                    # the finish hook built the index
    return SimpleNamespace(home=env.home, job_id=job_id, tmp=tmp_path)


@pytest.fixture
def api_env(archived, monkeypatch):
    """SATVIK_HOME on the archived home, and the API's external checks (docker, keys, the sandbox image) answered by fakes."""
    from monitor import api, views
    from monitor import config_api
    import openrouter_call as oc
    monkeypatch.setenv("SATVIK_HOME", str(archived.home))
    monkeypatch.setattr(views, "docker_status", lambda: "ok")
    monkeypatch.setattr(views, "sandbox_image_status", lambda: "ok")
    monkeypatch.setattr(oc, "load_keys", lambda *a, **k: {"free": "free-key-not-a-real-value"})
    return SimpleNamespace(api=api, client_cls=TestClient, **archived.__dict__)


def _job_ids(body):
    return {row["job_id"] for row in body["jobs"]}


def test_a_deleted_index_is_rebuilt_at_startup_and_the_job_is_listed(api_env):
    _drop_index(api_env.home)
    assert jh.index_ok(api_env.home) is False
    with api_env.client_cls(api_env.api.app) as client:                  # the lifespan runs: startup rebuilds the index
        body = client.get("/api/jobs").json()
        health = client.get("/api/system/health").json()
    assert body["index_ok"] is True and api_env.job_id in _job_ids(body)
    assert health["index_ok"] is True and health["satvik_home"] == str(api_env.home)
    assert jh.index_ok(api_env.home) is True


def test_a_corrupt_index_is_rebuilt_at_startup(api_env):
    _drop_index(api_env.home)
    (api_env.home / jh.DB_NAME).write_bytes(b"this is not a database" * 100)
    assert jh.index_state(api_env.home) == "corrupt"
    with api_env.client_cls(api_env.api.app) as client:
        body = client.get("/api/jobs").json()
    assert body["index_ok"] is True and api_env.job_id in _job_ids(body)
    conn = sqlite3.connect(str(api_env.home / jh.DB_NAME))
    try:
        assert conn.execute("SELECT count(*) FROM jobs WHERE job_id = ?", (api_env.job_id,)).fetchone()[0] == 1
    finally:
        conn.close()


def test_a_healthy_index_is_not_deleted_or_rebuilt(api_env):
    conn = sqlite3.connect(str(api_env.home / jh.DB_NAME))
    try:
        conn.execute("INSERT INTO jobs (job_id, name, source) VALUES ('sentinel-row', 'not-from-files', 'test')")   # a rebuild would drop it
        conn.commit()
    finally:
        conn.close()
    with api_env.client_cls(api_env.api.app) as client:
        assert client.get("/api/jobs").json()["index_ok"] is True
    conn = sqlite3.connect(str(api_env.home / jh.DB_NAME))
    try:
        assert conn.execute("SELECT count(*) FROM jobs WHERE job_id = 'sentinel-row'").fetchone()[0] == 1
    finally:
        conn.close()


def test_a_locked_index_is_left_alone(api_env, monkeypatch):
    monkeypatch.setattr(jh, "index_state", lambda home: "locked")
    monkeypatch.setattr(jh, "rebuild_index", lambda home: pytest.fail("a locked index must not be rebuilt"))
    assert jh.ensure_index(api_env.home) == "locked"


def test_unset_satvik_home_still_starts(api_env, monkeypatch):
    monkeypatch.delenv("SATVIK_HOME", raising=False)
    with api_env.client_cls(api_env.api.app) as client:
        response = client.get("/api/system/health")
    assert response.status_code == 200
    body = response.json()
    assert body["satvik_home"] is None and body["index_ok"] is False


def test_a_failing_rebuild_never_blocks_startup(api_env, monkeypatch):
    _drop_index(api_env.home)

    def boom(home):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(jh, "rebuild_index", boom)
    with api_env.client_cls(api_env.api.app) as client:
        response = client.get("/api/system/health")
    assert response.status_code == 200 and response.json()["index_ok"] is False


def _cli(home, *args):
    env = {k: v for k, v in os.environ.items() if k != "SATVIK_HOME"}
    if home is not None:
        env["SATVIK_HOME"] = str(home)
    return subprocess.run([sys.executable, str(CLI), *args], env=env, capture_output=True, text=True, timeout=120)


def test_the_cli_rebuilds_the_index_and_prints_the_job_count(archived):
    _drop_index(archived.home)
    done = _cli(archived.home, "rebuild")
    assert done.returncode == 0, done.stderr
    lines = dict(line.split(": ", 1) for line in done.stdout.strip().splitlines())
    assert lines["jobs"] == "1" and lines["index_ok"] == "true" and lines["event rows"] != "0"
    assert jh.index_ok(archived.home) is True


def test_the_cli_refuses_an_unset_home_and_a_bad_command(archived):
    unset = _cli(None, "rebuild")
    assert unset.returncode == 1 and "SATVIK_HOME" in unset.stderr
    assert _cli(archived.home, "frobnicate").returncode == 2
