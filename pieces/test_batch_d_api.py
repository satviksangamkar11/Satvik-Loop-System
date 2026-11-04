"""Batch D (API and UI, codex audit 2026-11-04): I45, I58, I59, I60. Fakes only: the monitor API on a temp home (test_api_contract's world),
a scripted verifier, a flaky file replacer. No network, no model call.

I45 one authoritative stage validator (config_api.authoritative_problems) runs at PUT /stages, POST /jobs and PATCH /loops/{id}/stages,
    as POST /stages/validate does: an unverified model is refused with the same code everywhere.
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
sys.path.insert(0, str(REPO / "pieces"))
from test_api_contract import world, scripted_running, _unverify_in_catalog, JOB_ID_COMPLETED  # noqa: E402,F401
import stage_config as sc  # noqa: E402

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
