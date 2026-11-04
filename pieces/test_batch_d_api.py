"""Batch D (API and UI, codex audit 2026-11-04): I45, I58, I59, I60. Fakes only: the monitor API on a temp home (test_api_contract's world),
a scripted verifier, a flaky file replacer. No network, no model call.

I45 one authoritative stage validator (config_api.authoritative_problems) runs at PUT /stages, POST /jobs and PATCH /loops/{id}/stages,
    as POST /stages/validate does: an unverified model is refused with the same code everywhere.
I59 an operation intent is recorded before the effects; a retry reconciles (replay, 409 operation-in-progress, 422 key reused, 409 corrupt).
I60 a slow model verify runs in a worker thread without the effects lock: STOP and GET /system/health answer while it is in progress.
"""
import json
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
sys.path.insert(0, str(REPO / "pieces"))
sys.path.insert(0, str(REPO))
from test_api_contract import world, scripted_running, _unverify_in_catalog, JOB_ID_COMPLETED  # noqa: E402,F401
import api_idempotency as ai  # noqa: E402
import model_catalog as mc  # noqa: E402
from monitor import actions  # noqa: E402
from monitor.store import ApiError  # noqa: E402

DOTS = "dots-studio/dots-3-note-preview:free"
FL_TOP = "docs/pilot/four-loop/fl-top.json"


def _queue_files(w):
    queue = w.home / "queue"
    return sorted(p.name for p in queue.glob("*.json")) if queue.is_dir() else []


# ---------------------------------------------------------------- I45

def test_I45_put_stages_refuses_an_unverified_model_with_the_validate_code(world):
    _unverify_in_catalog(world, DOTS)
    changes = [{"stage": "writer_a_write", "settings": {"temperature": 0.2}}]
    validate = world.client.post("/api/stages/validate", json={"profile": "test", "changes": changes})
    assert validate.status_code == 400
    revision = world.client.get("/api/stages", params={"profile": "test"}).json()["revision"]
    saved = world.client.put("/api/stages", json={"profile": "test", "revision": revision, "changes": changes},
                             headers={"Idempotency-Key": "put-unverified-01"})
    assert saved.status_code == 400
    assert saved.json()["error"]["code"] == validate.json()["error"]["code"] == "contract-not-verified"
    assert world.client.get("/api/stages", params={"profile": "test"}).json()["revision"] == revision   # nothing was written


def test_I45_queue_refuses_an_unverified_model_with_the_validate_code_and_queues_nothing(world):
    _unverify_in_catalog(world, DOTS)
    changes = [{"stage": "writer_a_write", "settings": {"temperature": 0.2}}]
    validate = world.client.post("/api/stages/validate", json={"profile": "test", "changes": changes})
    queued = world.client.post("/api/jobs", json={"job_path": FL_TOP, "stages": changes},
                               headers={"Idempotency-Key": "queue-unverified-01"})
    assert queued.status_code == 400
    assert queued.json()["error"]["code"] == validate.json()["error"]["code"] == "contract-not-verified"
    assert _queue_files(world) == []


def test_I45_future_stage_refuses_an_unverified_model_with_the_validate_code(world):
    scripted_running(world, lease="alive", waiting=True)
    change = {"settings": {"temperature": 0.2}}
    row = next(r for r in world.client.get("/api/stages", params={"profile": "test"}).json()["stages"] if r["stage"] == "writer_b_write")
    assert row["via"] == "openrouter"
    _unverify_in_catalog(world, row["model"])                  # the model this stage really runs on in the test profile
    validate = world.client.post("/api/stages/validate", json={"profile": "test", "changes": [{"stage": "writer_b_write", **change}]})
    patched = world.client.patch(f"/api/loops/{JOB_ID_COMPLETED}/stages", json={"stage": "writer_b_write", "change": change, "revision": 1},
                                 headers={"Idempotency-Key": "patch-unverified-01"})
    assert validate.status_code == 400 and validate.json()["error"]["code"] == "contract-not-verified"
    assert patched.status_code == 422
    assert patched.json()["error"]["code"] == "contract-not-verified"
    assert world.client.get("/api/stages").json()["revision"] == 1      # the roster is untouched


# ---------------------------------------------------------------- I59: operation intents

def _fp(body):
    return ai.request_fingerprint("POST", "/api/system/stop", body)


def test_I59_a_retry_with_the_same_key_and_request_replays_and_runs_once(tmp_path):
    store = ai.OperationStore(tmp_path / "idem", clock=lambda: 100.0)
    assert store.begin("op-key-000001", _fp({"on": True})) == ("run", None)
    assert store.finish("op-key-000001", _fp({"on": True}), 200, {"stop": True}) == (200, {"stop": True})
    assert store.begin("op-key-000001", _fp({"on": True})) == ("replay", (200, {"stop": True}))


def test_I59_the_intent_is_on_disk_before_the_effect_and_a_started_record_is_never_run_again(tmp_path):
    store = ai.OperationStore(tmp_path / "idem", clock=lambda: 100.0)
    store.begin("op-key-000002", _fp({"on": True}))             # the process stops here: no outcome is recorded
    record = json.loads((tmp_path / "idem" / "op-key-000002.json").read_text())
    assert record["state"] == "started" and record["fingerprint"] == _fp({"on": True})
    with pytest.raises(ai.OperationConflict) as exc:
        store.begin("op-key-000002", _fp({"on": True}))
    assert (exc.value.status, exc.value.code) == (409, "operation-in-progress")
    store._clock = lambda: 10**9                                 # never expires on its own: an operator reconciles it
    with pytest.raises(ai.OperationConflict) as later:
        store.begin("op-key-000002", _fp({"on": True}))
    assert later.value.code == "operation-in-progress"


def test_I59_the_same_key_with_another_request_is_422_and_runs_nothing(tmp_path):
    store = ai.OperationStore(tmp_path / "idem", clock=lambda: 100.0)
    store.begin("op-key-000003", _fp({"on": True}))
    store.finish("op-key-000003", _fp({"on": True}), 200, {"stop": True})
    with pytest.raises(ai.OperationConflict) as exc:
        store.begin("op-key-000003", _fp({"on": False}))
    assert (exc.value.status, exc.value.code) == (422, "idempotency-key-reused")


def test_I59_a_corrupt_record_is_a_conflict_never_an_absent_key(tmp_path):
    folder = tmp_path / "idem"
    store = ai.OperationStore(folder, clock=lambda: 100.0)
    for junk in ("not json{{{", '{"key": "op-key-000004"}', '{"key": "op-key-000004", "fingerprint": "f", "state": "finished"}', "[]"):
        (folder / "op-key-000004.json").write_text(junk)
        with pytest.raises(ai.OperationConflict) as exc:
            store.begin("op-key-000004", _fp({"on": True}))
        assert (exc.value.status, exc.value.code) == (409, "idempotency-record-corrupt"), junk
    assert (folder / "op-key-000004.json").read_text() == "[]"  # the corrupt record is left for the operator, not overwritten


def test_I59_a_finished_record_expires_after_retention_and_the_key_is_free_again(tmp_path):
    now = [100.0]
    store = ai.OperationStore(tmp_path / "idem", retention_s=50, clock=lambda: now[0])
    store.begin("op-key-000005", _fp({"on": True}))
    store.finish("op-key-000005", _fp({"on": True}), 200, {"n": 1})
    now[0] = 150.0
    assert store.begin("op-key-000005", _fp({"on": True})) == ("replay", (200, {"n": 1}))
    now[0] = 151.0
    assert store.begin("op-key-000005", _fp({"on": True})) == ("run", None)   # past retention: runs as new


def test_I59_an_aborted_intent_frees_its_key_and_a_bad_key_is_refused(tmp_path):
    store = ai.OperationStore(tmp_path / "idem", clock=lambda: 100.0)
    store.begin("op-key-000006", _fp({"on": True}))
    store.abort("op-key-000006")
    assert store.begin("op-key-000006", _fp({"on": True})) == ("run", None)
    with pytest.raises(ValueError):
        store.begin("short", _fp({"on": True}))


def test_I59_the_api_replays_once_refuses_reuse_and_reconciles_a_crashed_intent(world, monkeypatch):
    calls = []

    def fake_stop(on):
        calls.append(on)
        return {"stop": on}
    real_stop = actions.set_stop
    monkeypatch.setattr(actions, "set_stop", fake_stop)
    try:
        first = world.client.post("/api/system/stop", json={"on": True}, headers={"Idempotency-Key": "stop-replay-0001"})
        again = world.client.post("/api/system/stop", json={"on": True}, headers={"Idempotency-Key": "stop-replay-0001"})
        other = world.client.post("/api/system/stop", json={"on": False}, headers={"Idempotency-Key": "stop-replay-0001"})
        assert first.status_code == again.status_code == 200 and again.json() == first.json()
        assert other.status_code == 422 and other.json()["error"]["code"] == "idempotency-key-reused"
        assert calls == [True]
        # a process that stopped between the intent and the outcome: its record is started
        ai.OperationStore(world.home / "api" / "idempotency").begin("stop-crash-00001", _fp({"on": True}))
        stuck = world.client.post("/api/system/stop", json={"on": True}, headers={"Idempotency-Key": "stop-crash-00001"})
        assert stuck.status_code == 409 and stuck.json()["error"]["code"] == "operation-in-progress"
        assert calls == [True]                                   # not run again
    finally:
        monkeypatch.setattr(actions, "set_stop", real_stop)


def test_I59_the_intent_is_recorded_while_the_effect_runs(world, monkeypatch):
    seen = {}

    def fake_stop(on):
        seen["record"] = json.loads((world.home / "api" / "idempotency" / "stop-during-0001.json").read_text())
        return {"stop": on}
    real_stop = actions.set_stop
    monkeypatch.setattr(actions, "set_stop", fake_stop)
    try:
        assert world.client.post("/api/system/stop", json={"on": True}, headers={"Idempotency-Key": "stop-during-0001"}).status_code == 200
    finally:
        monkeypatch.setattr(actions, "set_stop", real_stop)
    assert seen["record"]["state"] == "started"
    final = json.loads((world.home / "api" / "idempotency" / "stop-during-0001.json").read_text())
    assert final["state"] == "finished" and final["status"] == 200


def test_I59_a_5xx_is_aborted_so_the_same_key_may_run_again(world, monkeypatch):
    def broken(on):
        raise ApiError(503, "engine-unavailable", "the engine cannot be reached")
    real_stop = actions.set_stop
    monkeypatch.setattr(actions, "set_stop", broken)
    try:
        failed = world.client.post("/api/system/stop", json={"on": True}, headers={"Idempotency-Key": "stop-5xx-000001"})
    finally:
        monkeypatch.setattr(actions, "set_stop", real_stop)
    assert failed.status_code == 503
    retried = world.client.post("/api/system/stop", json={"on": True}, headers={"Idempotency-Key": "stop-5xx-000001"})
    assert retried.status_code == 200 and retried.json() == {"stop": True}
    world.client.post("/api/system/stop", json={"on": False})
