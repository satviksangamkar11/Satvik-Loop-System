"""Control Center API contract tests (P2, docs/API_CONTRACT.md). FastAPI's TestClient on a temp SATVIK_HOME with a scripted job tree and a
temp copy of config/. No network, no model call: the OpenRouter listing, the verify calls and the planner are fakes.

Shapes: every monitor/fixtures file is compared with the response that builds the same scenario (shape_diff: fixture keys present with
the same JSON types; null on either side is unknown and matches anything; no unexpected keys except the optional ones named below).
api_shape, api_budget and api_idempotency are the loop's helpers (scripts/source/), used as they are."""
import io
import json
import os
import shutil
import sys
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "monitor" / "fixtures"
sys.path.insert(0, str(REPO / "scripts" / "source"))

import claude_code as cc  # noqa: E402
import event_log  # noqa: E402
import fix_loop  # noqa: E402
import job_history  # noqa: E402
import launch  # noqa: E402
import limits_config  # noqa: E402
import loop_home  # noqa: E402
import model_catalog as mc  # noqa: E402
import openrouter_call as oc  # noqa: E402
import stage_config as sc  # noqa: E402

OPTIONAL_KEYS = {"note", "merge"}        # documented extras: a fixture may omit them and a response may carry them
FREEFORM_KEYS = {"settings"}             # a stage's settings: free-form per model, compared by type only


# ---------------------------------------------------------------- shape comparison (the loop's api_shape)

from api_shape import shape_diff as _api_shape_diff  # noqa: E402


def shape_diff(expected, actual):
    """api_shape.shape_diff with the optional and free-form keys removed from both sides (a fixture or a response may omit an optional
    key; a stage's settings are free-form per model). [] when the shapes match."""
    return _api_shape_diff(_strip(expected), _strip(actual))


def _strip(value):
    """The optional and free-form keys removed from both sides before comparing (the same rule as the local stub)."""
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in OPTIONAL_KEYS and k not in FREEFORM_KEYS}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def world(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "config").mkdir(parents=True)
    for name in ("roster.json", "models.json", "limits.json"):
        shutil.copy(REPO / "config" / name, repo / "config" / name)
    job_dir = repo / "docs" / "pilot" / "four-loop"
    job_dir.mkdir(parents=True)
    shutil.copy(REPO / "docs" / "pilot" / "four-loop" / "fl-top.json", job_dir / "fl-top.json")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("SATVIK_HOME", str(home))
    monkeypatch.setenv("SATVIK_REPO", str(repo))
    monkeypatch.setattr(oc, "ROSTER_PATH", repo / "config" / "roster.json")
    monkeypatch.setattr(mc, "CATALOG_PATH", repo / "config" / "models.json")
    monkeypatch.setattr(limits_config, "PATH", repo / "config" / "limits.json")
    monkeypatch.setattr(oc, "load_keys", lambda *a, **k: {"free": "free-key-not-a-real-value"})
    from monitor import config_api
    from monitor import views
    LISTING[:] = BASE_LISTING
    monkeypatch.setattr(config_api, "_LISTING", {"fetch": lambda: LISTING})
    monkeypatch.setattr(views, "docker_status", lambda: "ok")
    monkeypatch.setattr(views, "sandbox_image_status", lambda: "ok")
    from monitor import api
    client = TestClient(api.app)
    return SimpleNamespace(repo=repo, home=home, client=client, tmp=tmp_path, monkeypatch=monkeypatch)


BASE_LISTING = [
    {"id": "nvidia/nemotron-3-ultra-550b-a55b:free", "pricing": {"prompt": "0", "completion": "0"}, "context_length": 262144,
     "top_provider": {"max_completion_tokens": 32768}, "supported_parameters": ["reasoning", "temperature"]},
    {"id": "acme/extra-model:free", "pricing": {"prompt": "0", "completion": "0"}, "context_length": 32000, "top_provider": {}, "supported_parameters": []},
    {"id": "acme/paid-model", "pricing": {"prompt": "0.000001", "completion": "0.000002"}, "context_length": 64000,
     "top_provider": {"max_completion_tokens": 8000}, "supported_parameters": ["temperature"]},
]
LISTING = list(BASE_LISTING)              # the fake OpenRouter listing; each test starts from BASE_LISTING
JOB_ID_COMPLETED = "fl-top-20261103-2309-7d15"
JOB_ID_ESCALATE = "fl-top-20261103-2248-2ed3"
JOB_ID_REVIEW = "calc-20261103-1414-2ed8"
JOB_ID_RECOVERED = "tl-index-20261103-2220-94a5"
BASE = "a" * 40


# ---------------------------------------------------------------- scripted job tree

def _snapshot(profile="test"):
    snap, _ = sc.snapshot(profile)
    return snap


def add_job(w, job_id, name="fl-top", *, where="results", events=(), state="passed", end=None, merge=None, started=(),
            lease="released", waiting=None, findings=None, failing=(), tests_passed=True, verdict=None, reason=None, files=None,
            registry=None, artifacts=(), status_state=None, snapshot=True):
    """One job: its folder (the archive or the worktree), its events, status.json, stage table and stage markers, its lease and its
    registry row. lease: released | alive | stale (pid dead, heartbeat 400 s old: orphan) | dead (pid dead, heartbeat now: stale-warning) |
    old (pid alive, heartbeat 200 s old: stale-warning)."""
    base_dir = (w.home / "results" / job_id) if where == "results" else (w.home / "wt" / job_id / "_loop")
    folder = base_dir / name
    folder.mkdir(parents=True, exist_ok=True)
    spec_files = files or ["scripts/source/fl_top.py", "pieces/test_fl_top.py"]
    job = {"name": name, "spec": "docs/pilot/four-loop/FL_TOP_SPEC.md", "files": spec_files, "tests": ["pieces/test_fl_top.py"],
           "profile": "production", "budget": {"free_requests": 40, "review_usd": 0.05, "paid_usd": 0}}
    (base_dir / f"{name}.job.json").write_text(json.dumps(job))
    for event in events:
        event_log.append_event(folder, dict(event, job_id=job_id))
    if snapshot:
        sc.write_snapshot(folder, _snapshot())
    if started:
        (folder / sc.STARTED_FILE).write_text(json.dumps({"started": {s: {"revision": 1, "at": "2026-11-03T17:40:00Z"} for s in started}}))
    status = {"name": name, "schema_version": 1, "state": status_state or ("running" if end is None else state), "profile": "test",
              "tests_passed": tests_passed, "review_verdict": verdict, "reason": reason, "failing_tests": list(failing), "steps": [],
              "coder_calls": 0, "reviewer_calls": {},
              "evidence": {"budget": {"free_requests_used": 3, "free_requests": 40, "review_usd_spent": 0.0, "review_usd": 0,
                                      "repair_calls": 0, "repair_enabled": False},
                           "tested_hash": "43d1" + "0" * 60, "reviewed_hash": "43d1" + "0" * 60, "final_hash": "43d1" + "0" * 60,
                           "stage_snapshot": {}},
              "updated": "2026-11-03T17:43:00Z"}
    if waiting is not None:
        status["waiting"] = waiting
    if findings is not None:
        status["findings"] = findings
    (folder / "status.json").write_text(json.dumps(status))
    (folder / "lineage.json").write_text("{}")
    for rel in artifacts:
        target = folder / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")
    if merge is not None:
        (w.home / "results" / job_id).mkdir(parents=True, exist_ok=True)
        (w.home / "results" / job_id / "merge.json").write_text(json.dumps(dict({"job_id": job_id}, **merge)))
    _lease(w, job_id, lease)
    _registry_row(w, job_id, name, registry or (end or "running"), job_id)
    return folder


def _lease(w, job_id, kind):
    leases = w.home / "leases"
    leases.mkdir(exist_ok=True)
    now = time.time()
    if kind == "released" or kind is None:
        data = {"job_id": job_id, "pid": 999999, "started": now, "heartbeat": now - 1000, "generation": 1, "released": True}
    elif kind == "alive":
        data = {"job_id": job_id, "pid": os.getpid(), "started": now, "heartbeat": now, "generation": 1, "released": False}
    elif kind == "old":
        data = {"job_id": job_id, "pid": os.getpid(), "started": now, "heartbeat": now - 200, "generation": 1, "released": False}
    elif kind == "dead":
        data = {"job_id": job_id, "pid": 999999, "started": now, "heartbeat": now, "generation": 1, "released": False}
    else:                                              # stale
        data = {"job_id": job_id, "pid": 999999, "started": now, "heartbeat": now - 400, "generation": 1, "released": False}
    (leases / f"{job_id}.json").write_text(json.dumps(data))


def _registry_row(w, job_id, name, state, base):
    path = w.home / "registry.json"
    table = json.loads(path.read_text()) if path.is_file() else {}
    table[job_id] = {"job": job_id, "gen": 1, "state": state, "base": base, "result": None, "started": time.time(), "finished": None,
                     "seq": len(table) + 1}
    path.write_text(json.dumps(table))


def _set_waiting_without_reason(w, job_id):
    """The job says it waits, and nothing says why (status.waiting with why null): derived waiting-unknown."""
    status_path = w.home / "wt" / job_id / "_loop" / "fl-top" / "status.json"
    status = json.loads(status_path.read_text())
    status["waiting"] = {"why": None, "since": "2026-11-03T17:41:02Z", "model": None}
    status_path.write_text(json.dumps(status))


def reindex(w):
    job_history.rebuild_index(w.home)


def scripted_completed(w):
    events = [
        {"type": "job-start", "name": "fl-top", "base": BASE, "branch": f"job/{JOB_ID_COMPLETED}", "gen": 3, "key": "fl-top"},
        {"type": "attempt-start", "attempt_id": "a1", "stage": "writer_a_write", "role": "writer_a", "mode": "write",
         "model": "dots-studio/dots-3-note-preview:free", "via": "openrouter", "effort": "high"},
        {"type": "attempt-end", "attempt_id": "a1", "status": "ok", "failed": False, "prompt_tokens": 1013, "completion_tokens": 812,
         "reasoning_tokens": 4100, "finish_reason": "stop", "seconds": 95.3, "error": None},
        {"type": "tests", "green": True, "failing": []},
        {"type": "review", "reviewer": "reviewer_b", "verdict": "READY", "evidence_ok": True},
        {"type": "review", "reviewer": "reviewer_a", "verdict": "READY", "evidence_ok": True},
        {"type": "finish", "state": "passed", "reason": "READY from: nvidia/nemotron-3.5-super-vl:free"},
        {"type": "merge", "state": "merged", "base": BASE, "head": "2b80095", "attempts": 1},
    ]
    folder = add_job(w, JOB_ID_COMPLETED, events=events, end="passed", merge={"state": "merged", "base": BASE, "head": "2b80095", "attempts": 1},
                     started=("writer_a_write", "reviewer_b", "reviewer_a"), registry="merged", verdict="READY",
                     reason="READY from: nvidia/nemotron-3.5-super-vl:free", lease="released")
    reindex(w)
    return folder


def scripted_running(w, job_id=JOB_ID_COMPLETED, lease="alive", waiting=True):
    events = [
        {"type": "job-start", "name": "fl-top", "base": BASE, "branch": f"job/{job_id}", "gen": 3, "key": "fl-top"},
        {"type": "attempt-start", "attempt_id": "a1", "stage": "writer_a_write", "role": "writer_a", "mode": "write",
         "model": "dots-studio/dots-3-note-preview:free", "via": "openrouter", "effort": "high"},
        {"type": "attempt-end", "attempt_id": "a1", "status": "ok", "failed": False, "prompt_tokens": 1013, "completion_tokens": 812,
         "reasoning_tokens": 4100, "finish_reason": "stop", "seconds": 95.3, "error": None},
        {"type": "tests", "green": True, "failing": []},
    ] + ([{"type": "wait-start", "why": "model-slot", "model": "nvidia/nemotron-3.5-super-vl:free"}] if waiting else [])
    waiting_obj = {"why": "model-slot", "since": "2026-11-03T17:41:02Z", "model": "nvidia/nemotron-3.5-super-vl:free"} if waiting else None
    add_job(w, job_id, where="wt", events=events, end=None, started=("writer_a_write", "reviewer_b"), lease=lease, waiting=waiting_obj,
            registry="running", status_state="running", tests_passed=True)
    reindex(w)


def scripted_review_open(w):
    events = [
        {"type": "job-start", "name": "pilot-loop-status", "base": BASE, "branch": f"job/{JOB_ID_REVIEW}", "gen": 3, "key": "pilot-loop-status"},
        {"type": "attempt-start", "attempt_id": "a1", "stage": "writer_a_write", "role": "writer_a", "mode": "write",
         "model": "dots-studio/dots-3-note-preview:free", "via": "openrouter", "effort": "high"},
        {"type": "attempt-end", "attempt_id": "a1", "status": "ok", "failed": False, "prompt_tokens": 900, "completion_tokens": 300,
         "reasoning_tokens": 0, "finish_reason": "stop", "seconds": 40.0, "error": None},
        {"type": "tests", "green": True, "failing": []},
        {"type": "review", "reviewer": "reviewer_b", "verdict": "FIX FIRST", "evidence_ok": True},
        {"type": "finish", "state": "review-open", "reason": "FIX FIRST repair round 1 left the tests red"},
    ]
    add_job(w, JOB_ID_REVIEW, name="pilot-loop-status", events=events, end="review-open", state="review-open", verdict="FIX FIRST",
            findings=["scripts/source/loop_status.py:79 | SPEC 9: main(argv) takes an optional loop folder | default _loop is never used"],
            started=("writer_a_write", "reviewer_b", "review_fix_repair"), registry="review-open", lease="released",
            reason="FIX FIRST repair round 1 by dots left the tests red")
    reindex(w)


def scripted_escalate(w):
    events = [
        {"type": "job-start", "name": "fl-top", "base": BASE, "branch": f"job/{JOB_ID_ESCALATE}", "gen": 2, "key": "fl-top"},
        {"type": "attempt-start", "attempt_id": "a1", "stage": "writer_a_write", "role": "writer_a", "mode": "write",
         "model": "dots-studio/dots-3-note-preview:free", "via": "openrouter", "effort": "high"},
        {"type": "attempt-end", "attempt_id": "a1", "status": "ok", "failed": False, "prompt_tokens": 1000, "completion_tokens": 500,
         "reasoning_tokens": 0, "finish_reason": "stop", "seconds": 30.0, "error": None},
        {"type": "tests", "green": False, "failing": ["tests/acceptance/fl_top_acceptance.py::test_counts_come_from_fl_wordcount"]},
        {"type": "finish", "state": "escalate", "reason": "red after the ladder"},
    ]
    add_job(w, JOB_ID_ESCALATE, events=events, end="escalate", state="escalate", tests_passed=False,
            failing=["tests/acceptance/fl_top_acceptance.py::test_counts_come_from_fl_wordcount"], started=("writer_a_write",),
            artifacts=["ESCALATE.md", "history/call_01.json"], registry="escalate", lease="released", reason="red after the ladder")
    reindex(w)


def scripted_recovered(w):
    events = [
        {"type": "job-start", "name": "tl-index", "base": BASE, "branch": f"job/{JOB_ID_RECOVERED}", "gen": 3, "key": "tl-index"},
        {"type": "attempt-start", "attempt_id": "a1", "stage": "writer_a_write", "role": "writer_a", "mode": "write",
         "model": "dots-studio/dots-3-note-preview:free", "via": "openrouter", "effort": "high"},
        {"type": "attempt-end", "attempt_id": "a1", "status": "ok", "failed": False, "prompt_tokens": 1000, "completion_tokens": 500,
         "reasoning_tokens": 0, "finish_reason": "stop", "seconds": 30.0, "error": None},
        {"type": "finish", "state": "passed", "reason": "READY"},
        {"type": "merge", "state": "merged", "base": BASE, "head": "2b80095", "attempts": 2},
    ]
    add_job(w, JOB_ID_RECOVERED, name="tl-index", events=events, end="passed", started=("writer_a_write",),
            merge={"state": "merged", "base": BASE, "head": "2b80095", "attempts": 2}, registry="merged", lease="released", verdict="READY")
    reindex(w)


def queue_file(w, name="fl-top"):
    """A queued job file with its frozen stage table beside it (as POST /jobs writes them)."""
    fix_loop.enqueue(w.repo / "docs" / "pilot" / "four-loop" / "fl-top.json", w.home, stages=_snapshot())


# ---------------------------------------------------------------- 1. every response shape matches its fixture

def test_overview_shape_matches_fixture(world):
    scripted_running(world, lease="alive", waiting=True)
    body = world.client.get("/api/overview").json()
    assert shape_diff(fixture("overview.json"), body) == []
    assert body["index_ok"] is True and body["stop"] is False


def test_models_shape_matches_fixture(world):
    body = world.client.get("/api/models").json()
    shapes = fixture("models.json")
    assert set(shapes) == {"models", "index_ok"}
    by_id = {m["model_id"]: m for m in body["models"]}
    for expected in shapes["models"]:
        assert expected["model_id"] in by_id, expected["model_id"]
        assert shape_diff(expected, by_id[expected["model_id"]]) == []
    assert body["index_ok"] is True


def test_ui_and_api_contract_tests_read_the_same_fixture_files(world):
    """B4: the UI contract tests and this file read the same monitor/fixtures files, and the sample fields the checklist names are present:
    GET /system/limits revision, GET /jobs {jobs, index_ok} (jobs.json), GET /system/health profile."""
    ui_test = (REPO / "ui" / "tests" / "contract.test.ts").read_text(encoding="utf-8")
    for name in ("models.json", "overview.json", "stages.json", "system_health.json", "running_job.json", "results_escalate.json", "jobs.json"):
        assert f"../../monitor/fixtures/{name}" in ui_test, name
        assert (FIXTURES / name).is_file(), name
    assert set(fixture("jobs.json")) == {"jobs", "index_ok"}
    assert isinstance(fixture("system_health.json")["limits"]["revision"], int)
    assert "profile" in fixture("system_health.json")["health"]
    contract = (REPO / "docs" / "API_CONTRACT.md").read_text(encoding="utf-8")
    assert '"revision": 1' in contract and "### Job list (`GET /jobs`)" in contract and '"profile": "test|production"' in contract


def test_the_contract_places_contracts_and_reasoning_mode_under_capabilities(world):
    """M7: the prose and the example agree with the fixture and the API: contracts and reasoning_mode are under `capabilities`."""
    doc = (REPO / "docs" / "API_CONTRACT.md").read_text(encoding="utf-8")
    assert "`capabilities.contracts.<contract>" in doc and "`capabilities.reasoning_mode`" in doc
    assert "`contracts.<contract> = contract_state`" not in doc
    for model in fixture("models.json")["models"]:
        assert set(model["capabilities"]) >= {"contracts", "reasoning_mode"}
        assert "contracts" not in model and "reasoning_mode" not in model
    body = world.client.get("/api/models").json()["models"][0]
    assert "contracts" in body["capabilities"] and "reasoning_mode" in body["capabilities"]


def test_stages_shape_matches_fixture_and_lists_ten_stages(world):
    body = world.client.get("/api/stages", params={"profile": "test"}).json()
    expected = fixture("stages.json")
    assert body["profile"] == "test" and body["revision"] >= 1
    assert [row["stage"] for row in body["stages"]] == list(sc.STAGE_NAMES) and len(body["stages"]) == 10
    rows = {row["stage"]: row for row in body["stages"]}
    for row in expected["stages"]:
        assert shape_diff(row, rows[row["stage"]]) == [], row["stage"]
    assert rows["reviewer_reask"]["model"] is None and rows["reviewer_reask"]["model_selectable"] is False
    assert rows["reviewer_a"]["model_selectable"] is False and rows["reviewer_a"]["model"] is None


def test_health_shape_matches_fixture(world):
    body = world.client.get("/api/system/health").json()
    assert shape_diff(fixture("system_health.json")["health"], body) == []
    assert body["backend"] == "ok" and body["stop"] is False


def test_limits_shape_matches_fixture(world):
    scripted_running(world, lease="alive", waiting=True)
    body = world.client.get("/api/system/limits").json()
    assert shape_diff(fixture("system_health.json")["limits"], body) == []


def test_completed_job_detail_matches_fixture(world):
    scripted_completed(world)
    body = world.client.get(f"/api/loops/{JOB_ID_COMPLETED}").json()
    assert shape_diff(fixture("completed_job.json"), body) == []
    assert body["derived_state"] == "merged" and body["end_state"] == "passed" and body["merge_state"] == "merged"
    assert body["archive"] == {"available": True, "archive_failed": False}


def test_untriggered_stages_are_listed_and_not_triggered(world):
    scripted_completed(world)
    body = world.client.get(f"/api/loops/{JOB_ID_COMPLETED}").json()
    expected = fixture("untriggered_stage_job.json")
    stages = {s["stage"]: s for s in body["stages"]}
    assert len(stages) == 10
    assert expected["note"]                                             # the fixture says the same: only three stages ran
    for name in ["planner", "writer_a_repair", "writer_b_write", "writer_b_repair", "specialist_repair", "reviewer_reask",
                 "review_fix_repair"]:
        assert stages[name]["status"] == "not-triggered" and stages[name]["attempts"] == 0
    assert stages["writer_a_write"]["status"] == "ran" and stages["reviewer_b"]["status"] == "ran"
    assert stages["reviewer_a"]["status"] == "ran"


def test_running_job_detail_matches_fixture_and_its_events(world):
    scripted_running(world, lease="alive", waiting=True)
    body = world.client.get(f"/api/loops/{JOB_ID_COMPLETED}").json()
    expected = fixture("running_job.json")
    expected_events = expected.pop("events")
    assert shape_diff(expected, body) == []
    assert body["derived_state"] == "waiting" and body["waiting"]["why"] == "model-slot"
    assert body["lease"]["held"] is True and body["heartbeat_age_s"] is not None
    stages = {s["stage"]: s for s in body["stages"]}
    assert stages["reviewer_b"]["status"] == "running" and stages["writer_a_write"]["status"] == "ran"
    events = world.client.get(f"/api/loops/{JOB_ID_COMPLETED}/events", params={"after": 0}).json()
    assert events["next_after"] == 5 and events["index_ok"] is True
    got = {e["seq"]: e for e in events["events"]}
    for want in expected_events["events"]:
        if want["type"].startswith("attempt-"):
            assert shape_diff(want, got[want["seq"]]) == [], want["seq"]
    later = world.client.get(f"/api/loops/{JOB_ID_COMPLETED}/events", params={"after": 3}).json()
    assert [e["seq"] for e in later["events"]] == [4, 5]


def test_attempt_end_status_is_the_attempt_status_not_the_http_code(world):
    """M1: the engine's attempt-end `status` is the HTTP code of its last request. The API shows the contract's attempt_status instead:
    ok when the call did not fail, rejected when a 2xx reply could not be used, failed for any other failure. No HTTP code, no failed flag."""
    job_id = "fl-top-20261104-0900-a1a1"
    start = {"type": "attempt-start", "stage": "writer_a_write", "role": "writer_a", "mode": "write",
             "model": "dots-studio/dots-3-note-preview:free", "via": "openrouter", "effort": "high"}
    end = {"type": "attempt-end", "prompt_tokens": 10, "completion_tokens": 5, "reasoning_tokens": None, "finish_reason": "stop",
           "seconds": 1.5, "requests": 1}
    events = [
        {"type": "job-start", "name": "fl-top", "base": BASE, "branch": f"job/{job_id}", "gen": 1, "key": "fl-top"},
        dict(start, attempt_id="a1"), dict(end, attempt_id="a1", status=200, failed=False, error=None),
        dict(start, attempt_id="a2"), dict(end, attempt_id="a2", status=200, failed=True, error="the reply has no file blocks"),
        dict(start, attempt_id="a3"), dict(end, attempt_id="a3", status=429, failed=True, error="HTTP 429: rate limited"),
    ]
    add_job(world, job_id, events=events, end="passed", state="passed", started=("writer_a_write",), registry="passed")
    reindex(world)
    rows = world.client.get(f"/api/loops/{job_id}/events", params={"after": 0}).json()["events"]
    ends = {e["attempt_id"]: e for e in rows if e["type"] == "attempt-end"}
    assert {k: v["status"] for k, v in ends.items()} == {"a1": "ok", "a2": "rejected", "a3": "failed"}
    assert ends["a3"]["error"] == "HTTP 429: rate limited" and ends["a2"]["error"] == "the reply has no file blocks"
    for row in rows:
        if row["type"].startswith("attempt-"):
            assert not isinstance(row["status"], int) and row["status"] in ("running", "ok", "failed", "rejected")
            assert "failed" not in row and "requests" not in row
    assert shape_diff(fixture("running_job.json")["events"]["events"][2], ends["a1"]) == []


def test_attempt_end_carries_its_attempt_fields_from_the_start_even_after_a_cursor(world):
    """M2: an attempt-end that lacks stage, role, mode, model, via or effort takes them from the attempt-start with the same attempt id,
    also when the start is before the poll's `after` cursor."""
    job_id = "fl-top-20261104-0900-b2b2"
    events = [
        {"type": "job-start", "name": "fl-top", "base": BASE, "branch": f"job/{job_id}", "gen": 1, "key": "fl-top"},
        {"type": "attempt-start", "attempt_id": "a1", "stage": "reviewer_b", "role": "reviewer_b", "mode": "review",
         "model": "nvidia/nemotron-3.5-super-vl:free", "via": "openrouter", "effort": "low"},
        {"type": "attempt-end", "attempt_id": "a1", "status": 200, "failed": False, "prompt_tokens": 10099, "completion_tokens": 420,
         "reasoning_tokens": 300, "finish_reason": "stop", "seconds": 41.2, "error": None},
    ]
    add_job(world, job_id, events=events, end="passed", state="passed", started=("reviewer_b",), registry="passed")
    reindex(world)
    later = world.client.get(f"/api/loops/{job_id}/events", params={"after": 1}).json()
    assert [e["seq"] for e in later["events"]] == [2, 3]
    end = later["events"][1]
    assert end["type"] == "attempt-end" and end["attempt_id"] == "a1"
    assert {k: end[k] for k in ("stage", "role", "mode", "model", "via", "effort")} == {
        "stage": "reviewer_b", "role": "reviewer_b", "mode": "review", "model": "nvidia/nemotron-3.5-super-vl:free", "via": "openrouter",
        "effort": "low"}
    assert end["status"] == "ok" and end["prompt_tokens"] == 10099
    whole = world.client.get(f"/api/loops/{job_id}/events", params={"after": 0}).json()["events"]
    assert all(e["stage"] == "reviewer_b" for e in whole if e["type"].startswith("attempt-"))


def test_an_archived_job_shows_its_spec_and_files_from_the_archived_job_file(world):
    """M5: GET /loops/{id} reads spec and files from the job file in the archive (results/<id>/<name>.job.json), after the worktree is gone.
    An archive that has no job file (made before the archive kept one) shows them as null: not guessed."""
    job = {"name": "fl-top", "spec": "docs/pilot/four-loop/FL_TOP_SPEC.md", "files": ["scripts/source/fl_top.py"], "tests": [],
           "profile": "test", "budget": {"free_requests": 40, "review_usd": 0.05, "paid_usd": 0}}
    job_id = "fl-top-20261104-0900-d4d4"
    events = [{"type": "job-start", "name": "fl-top", "base": BASE, "branch": f"job/{job_id}", "gen": 1, "key": "fl-top"},
              {"type": "finish", "state": "passed", "reason": "READY"}]
    add_job(world, job_id, where="wt", events=events, end="passed", state="passed", registry="passed", lease="released")
    wt_job_file = world.home / "wt" / job_id / "_loop" / "fl-top.job.json"
    wt_job_file.unlink()                                             # the run wrote no copy of its job file
    assert launch.archive_job(world.home, job_id, job=job) == "archived"
    shutil.rmtree(world.home / "wt" / job_id)                        # the worktree is gone: the archive alone answers
    reindex(world)
    body = world.client.get(f"/api/loops/{job_id}").json()
    assert body["spec"] == job["spec"] and body["files"] == job["files"]
    assert body["archive"] == {"available": True, "archive_failed": False}
    old = "fl-top-20261104-0900-e5e5"
    add_job(world, old, where="results", events=events, end="passed", state="passed", registry="passed", lease="released")
    (world.home / "results" / old / "fl-top.job.json").unlink()      # an older archive: no job file at all
    reindex(world)
    older = world.client.get(f"/api/loops/{old}").json()
    assert older["spec"] is None and older["files"] is None and older["end_state"] == "passed"


def test_failed_review_job_matches_fixture(world):
    scripted_review_open(world)
    body = world.client.get(f"/api/loops/{JOB_ID_REVIEW}").json()
    assert shape_diff(fixture("failed_review_job.json"), body) == []
    assert body["end_state"] == "review-open" and body["review_verdict"] == "FIX FIRST"
    assert body["hashes"]["delivered"] is None


def test_recovered_merge_job_matches_fixture(world):
    scripted_recovered(world)
    body = world.client.get(f"/api/loops/{JOB_ID_RECOVERED}").json()
    expected = fixture("recovered_merge_job.json")
    assert shape_diff(expected, body) == []
    assert body["merge"]["merge_state"] == "merged" and body["merge"]["attempts"] == 2


def test_results_escalate_matches_fixture(world):
    scripted_escalate(world)
    body = world.client.get(f"/api/results/{JOB_ID_ESCALATE}").json()
    assert shape_diff(fixture("results_escalate.json"), body) == []
    assert body["end_state"] == "escalate" and body["tests"]["passed"] is False
    assert body["tests"]["failing"][0]["file"] == "tests/acceptance/fl_top_acceptance.py"
    assert "fl-top/ESCALATE.md" in body["artifacts"]


def test_jobs_list_shape_and_filters(world):
    scripted_completed(world)
    scripted_escalate(world)
    body = world.client.get("/api/jobs").json()
    assert body["index_ok"] is True and len(body["jobs"]) == 2
    for job in body["jobs"]:
        assert set(job) >= {"job_id", "name", "derived_state", "end_state", "merge_state", "active_stage", "active_model", "waiting",
                            "heartbeat_age_s", "started", "finished", "free_requests_used", "review_usd_spent"}
    failed = world.client.get("/api/jobs", params={"filter": "failed"}).json()["jobs"]
    assert [j["job_id"] for j in failed] == [JOB_ID_ESCALATE]
    assert world.client.get("/api/jobs", params={"filter": "running"}).json()["jobs"] == []
    assert world.client.get("/api/jobs", params={"filter": "nope"}).status_code == 400


def test_jobs_list_matches_the_jobs_fixture(world):
    """GET /jobs is {jobs: [JobSummary], index_ok}: each fixture job appears with the same shape and the same derived state."""
    scripted_completed(world)
    scripted_escalate(world)
    scripted_running(world, job_id="fl-top-20261103-2310-9a1c", lease="alive", waiting=True)
    expected = fixture("jobs.json")
    body = world.client.get("/api/jobs").json()
    assert set(body) == {"jobs", "index_ok"} and body["index_ok"] is True
    got = {j["job_id"]: j for j in body["jobs"]}
    for want in expected["jobs"]:
        assert shape_diff(want, got[want["job_id"]]) == [], want["job_id"]
        assert got[want["job_id"]]["derived_state"] == want["derived_state"]
        assert got[want["job_id"]]["end_state"] == want["end_state"]


def test_error_envelope_matches_error_examples(world):
    examples = fixture("error_examples.json")["examples"]
    assert {e["error"]["code"] for e in examples} >= {"invalid-job", "duplicate-pending", "stage-started", "already-completed",
                                                       "paid-in-test", "engine-unavailable"}
    body = world.client.get("/api/loops/does-not-exist").json()
    assert set(body) == {"error"} and set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == "unknown-job"


# ---------------------------------------------------------------- 2. the error codes of the contract

def test_invalid_job_is_400_with_problems(world):
    r = world.client.post("/api/jobs/validate", json={"job_path": "../escape.json"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid-job"
    assert r.json()["error"]["details"]["problems"]


def test_valid_job_passes_validation(world):
    r = world.client.post("/api/jobs/validate", json={"job_path": "docs/pilot/four-loop/fl-top.json"})
    assert r.status_code == 200 and r.json() == {"valid": True, "problems": []}


def test_duplicate_pending_and_already_completed_and_stop(world):
    r = world.client.post("/api/jobs", json={"job_path": "docs/pilot/four-loop/fl-top.json"})
    assert r.status_code == 201 and r.json()["queued"] is True
    assert r.json()["stage_snapshot"]["revision"] == 1 and len(r.json()["stage_snapshot"]["sha256"]) == 64
    again = world.client.post("/api/jobs", json={"job_path": "docs/pilot/four-loop/fl-top.json"})
    assert again.status_code == 409 and again.json()["error"]["code"] == "duplicate-pending"
    # a passed job is refused until rerun=true
    (world.home / "registry.json").write_text(json.dumps({"fl-top": {"job": "fl-top-x", "gen": 1, "state": "passed", "base": BASE,
                                                                    "result": None, "started": 0, "finished": 0, "seq": 1}}))
    for f in (world.home / "queue").glob("*.json"):
        f.unlink()
    done = world.client.post("/api/jobs", json={"job_path": "docs/pilot/four-loop/fl-top.json"})
    assert done.status_code == 422 and done.json()["error"]["code"] == "already-completed"
    again_run = world.client.post("/api/jobs", json={"job_path": "docs/pilot/four-loop/fl-top.json", "rerun": True})
    assert again_run.status_code == 201
    world.client.post("/api/system/stop", json={"on": True}, headers={"Idempotency-Key": "stop-on-0001"})
    stopped = world.client.post("/api/jobs", json={"job_path": "docs/pilot/four-loop/fl-top.json", "rerun": True})
    assert stopped.status_code == 409 and stopped.json()["error"]["code"] == "stop-on"


def test_queue_refuses_a_duplicate_running_job(world):
    (world.home / "registry.json").write_text(json.dumps({"fl-top": {"job": "fl-top-run", "gen": 1, "state": "running", "base": BASE,
                                                                    "result": None, "started": 0, "finished": None, "seq": 1}}))
    lease = world.home / "leases"
    lease.mkdir(exist_ok=True)
    (lease / "fl-top-run.json").write_text(json.dumps({"job_id": "fl-top-run", "pid": os.getpid(), "started": 0, "heartbeat": time.time(),
                                                       "generation": 1, "released": False}))
    r = world.client.post("/api/jobs", json={"job_path": "docs/pilot/four-loop/fl-top.json"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "duplicate-running"


def test_stage_started_and_stale_revision_on_a_running_job(world):
    scripted_running(world, lease="alive", waiting=True)
    r = world.client.patch(f"/api/loops/{JOB_ID_COMPLETED}/stages", json={"stage": "writer_a_write", "change": {"effort": "low"}, "revision": 1},
                           headers={"Idempotency-Key": "patch-started-01"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "stage-started"
    stale = world.client.patch(f"/api/loops/{JOB_ID_COMPLETED}/stages", json={"stage": "writer_b_write", "change": {"effort": "low"}, "revision": 9},
                               headers={"Idempotency-Key": "patch-stale-0001"})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale-revision"


def test_future_stage_edit_writes_a_new_revision_for_that_job_only(world):
    scripted_running(world, lease="alive", waiting=True)
    ok = world.client.patch(f"/api/loops/{JOB_ID_COMPLETED}/stages", json={"stage": "writer_b_write", "change": {"effort": "low"}, "revision": 1},
                            headers={"Idempotency-Key": "patch-ok-000001"})
    assert ok.status_code == 200 and ok.json() == {"ok": True, "revision": 2}
    bad = world.client.patch(f"/api/loops/{JOB_ID_COMPLETED}/stages", json={"stage": "reviewer_reask", "change": {"model": "x"}, "revision": 2},
                             headers={"Idempotency-Key": "patch-bad-00001"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "model-not-selectable"
    assert world.client.get("/api/stages").json()["revision"] == 1      # the roster is untouched


def test_budget_and_stage_validation_codes(world):
    over = world.client.post("/api/budget/validate", json={"stages": {}, "paid_budget_usd": 0.5})
    assert over.status_code == 422 and over.json()["error"]["code"] == "paid-in-test"
    bad = world.client.post("/api/stages/validate", json={"profile": "test", "changes": [{"stage": "writer_a_write", "model": "x/not-in-pool:free",
                                                                                          "via": "openrouter"}]})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "model-not-selectable"


def test_over_max_output_is_refused_at_stage_validation(world):
    data = json.loads(mc.CATALOG_PATH.read_text())
    entry = next(e for e in data["openrouter"] if e["model_id"] == "dots-studio/dots-3-note-preview:free")
    entry["max_output_tokens"] = 32000
    entry["contracts"]["writer_file_blocks"] = True            # the writer stage's contract is verified (contract-not-verified is checked first)
    mc.CATALOG_PATH.write_text(json.dumps(data))
    r = world.client.post("/api/stages/validate", json={"profile": "test", "changes": [
        {"stage": "writer_a_write", "settings": {"max_tokens": 10**7}}]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "over-max-output"
    ok = world.client.post("/api/stages/validate", json={"profile": "test", "changes": [
        {"stage": "writer_a_write", "settings": {"max_tokens": 32000}}]})
    assert ok.status_code == 200 and ok.json() == {"valid": True, "problems": []}


def test_budget_worst_case_and_cap_under_production(world):
    """Under the production profile the paid reviewer's worst case is priced from the catalog (the prompt at the assumed size plus the whole
    output allowance). A budget below it is 400 budget-over-cap; one above it is valid."""
    world.monkeypatch.setenv("SATVIK_PROFILE", "production")
    none = world.client.post("/api/budget/validate", json={"stages": {}, "paid_budget_usd": 0})
    assert none.status_code == 400 and none.json()["error"]["code"] == "budget-over-cap"    # the paid reviewer costs something
    tiny = world.client.post("/api/budget/validate", json={"stages": {}, "paid_budget_usd": 0.000001})
    assert tiny.status_code == 400 and tiny.json()["error"]["code"] == "budget-over-cap"
    assert tiny.json()["error"]["details"]["worst_case_usd"] > 0
    big = world.client.post("/api/budget/validate", json={"stages": {}, "paid_budget_usd": 0.05})
    assert big.status_code == 200 and big.json()["valid"] is True and big.json()["worst_case_usd"] <= 0.05


def test_pool_refusals_and_removals(world):
    listing = world.client.post("/api/models", json={"model_id": "nvidia/nemotron-3-ultra-550b-a55b:free"})
    assert listing.status_code == 409 and listing.json()["error"]["code"] == "already-in-pool"
    unknown = world.client.post("/api/models", json={"model_id": "nobody/none:free"})
    assert unknown.status_code == 422 and unknown.json()["error"]["code"] == "not-in-catalog"
    paid = world.client.post("/api/models", json={"model_id": "acme/paid-model"})
    assert paid.status_code == 422 and paid.json()["error"]["code"] == "paid-without-pin"
    used = world.client.delete("/api/models/nvidia/nemotron-3.5-super-vl:free")
    assert used.status_code == 409 and used.json()["error"]["code"] == "model-in-use"
    assert used.json()["error"]["details"]["stages"]
    missing = world.client.delete("/api/models/acme/nothing:free")
    assert missing.status_code == 404


def test_pool_min_and_max_limits(world):
    """The pool starts at 10 (the minimum): a removal is refused until an add raises it. The pool stops growing at 20 (pool-full)."""
    catalog_path = mc.CATALOG_PATH
    count = lambda: len(json.loads(catalog_path.read_text())["openrouter"])     # noqa: E731
    assert count() == 10
    spare = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"       # used by no stage of either profile
    refused = world.client.delete(f"/api/models/{spare}", headers={"Idempotency-Key": "remove-min-00001"})
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "pool-min"
    LISTING.append({"id": "acme/extra-model:free", "pricing": {"prompt": "0", "completion": "0"}})
    assert world.client.post("/api/models", json={"model_id": "acme/extra-model:free"}).status_code == 201
    assert count() == 11
    gone = world.client.delete(f"/api/models/{spare}", headers={"Idempotency-Key": "remove-one-0001"})
    assert gone.status_code == 204 and count() == 10
    for i in range(10):
        LISTING.append({"id": f"acme/grow-{i}:free", "pricing": {"prompt": "0", "completion": "0"}})
        assert world.client.post("/api/models", json={"model_id": f"acme/grow-{i}:free"}).status_code == 201
    assert count() == 20
    LISTING.append({"id": "acme/over-cap:free", "pricing": {"prompt": "0", "completion": "0"}})
    full = world.client.post("/api/models", json={"model_id": "acme/over-cap:free"})
    assert full.status_code == 422 and full.json()["error"]["code"] == "pool-full" and count() == 20


def test_limits_revision_range_and_stale(world):
    stale = world.client.put("/api/system/limits", json={"revision": 0, "sandbox_slots": 2}, headers={"Idempotency-Key": "limits-stale-01"})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale-revision"
    out = world.client.put("/api/system/limits", json={"revision": 1, "sandbox_slots": 9}, headers={"Idempotency-Key": "limits-range-01"})
    assert out.status_code == 422 and out.json()["error"]["code"] == "out-of-range"
    ok = world.client.put("/api/system/limits", json={"revision": 1, "sandbox_slots": 2}, headers={"Idempotency-Key": "limits-ok-00001"})
    assert ok.status_code == 200 and ok.json() == {"revision": 2}
    assert limits_config.sandbox_slots() == 2                       # the engine reads it at each start
    assert json.loads((world.repo / "config" / "limits.json").read_text())["revision"] == 2
    assert world.client.get("/api/system/limits").json()["revision"] == 2        # the UI's next PUT carries this revision
    assert world.client.put("/api/system/limits", json={"revision": 2, "sandbox_slots": 3}).status_code == 200
    assert world.client.get("/api/system/limits").json()["revision"] == 3


def test_health_names_the_active_profile(world):
    assert world.client.get("/api/system/health").json()["profile"] == "test"
    world.monkeypatch.setenv("SATVIK_PROFILE", "production")
    assert world.client.get("/api/system/health").json()["profile"] == "production"


def test_limits_change_waits_for_free_sandbox_slots(world):
    scripted_running(world, lease="alive", waiting=True)
    gate = world.home / "gates.json"
    gate.write_text(json.dumps({"sandbox": [{"pid": os.getpid(), "gen": 1, "token": "t"}]}))
    r = world.client.put("/api/system/limits", json={"revision": 1, "sandbox_slots": 3})
    assert r.status_code == 409 and r.json()["error"]["code"] == "jobs-running"


def test_start_refusal_codes_match_the_quota_fixture(world):
    scripted_running(world, job_id="fl-top-20261103-2301-aaa1", lease="alive", waiting=False)
    scripted_running(world, job_id="fl-top-20261103-2302-aaa2", lease="alive", waiting=False)
    scripted_running(world, job_id="fl-top-20261103-2303-aaa3", lease="alive", waiting=False)
    queue_file(world)
    world.monkeypatch.setattr(fix_loop, "ledger_budget", lambda: {"free_requests": 180, "review_spend_usd": 0.0})
    r = world.client.post("/api/loops/start", json={"loops": 1})
    expected = fixture("quota_refusal.json")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == expected["error"]["code"] == "free-requests"
    assert r.json()["error"]["details"]["free_left"] == 180 and r.json()["error"]["details"]["needed"] == 220
    assert shape_diff(expected, r.json()) == []


def test_start_loops_needs_a_queue_and_no_stop(world):
    world.monkeypatch.setattr(fix_loop, "ledger_budget", lambda: {"free_requests": 1000, "review_spend_usd": 0.0})
    empty = world.client.post("/api/loops/start", json={"loops": 1})
    assert empty.status_code == 422 and empty.json()["error"]["code"] == "queue-empty"
    queue_file(world)
    spawned = []
    from monitor import actions
    world.monkeypatch.setattr(actions, "spawn_loop", lambda home, root: spawned.append(root))
    ok = world.client.post("/api/loops/start", json={"loops": 2})
    assert ok.status_code == 202 and ok.json() == {"started": 2} and len(spawned) == 2
    assert world.client.post("/api/loops/start", json={"loops": 5}).status_code == 400
    world.client.post("/api/system/stop", json={"on": True}, headers={"Idempotency-Key": "stop-on-0002"})
    assert world.client.post("/api/loops/start", json={"loops": 1}).json()["error"]["code"] == "stop-on"


def test_engine_unavailable_without_satvik_home(world):
    world.monkeypatch.delenv("SATVIK_HOME")
    r = world.client.get("/api/overview")
    assert r.status_code == 503 and r.json()["error"]["code"] == "engine-unavailable"
    health = world.client.get("/api/system/health")
    assert health.status_code == 200 and health.json()["satvik_home"] is None


def test_results_not_finished_and_not_archived(world):
    scripted_running(world, lease="alive", waiting=True)
    r = world.client.get(f"/api/results/{JOB_ID_COMPLETED}")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not-finished"
    d = world.client.get(f"/api/results/{JOB_ID_COMPLETED}/diff")
    assert d.status_code == 404
    z = world.client.get(f"/api/results/{JOB_ID_COMPLETED}/download")
    assert z.status_code == 404 and z.json()["error"]["code"] == "not-archived"


def test_download_zips_the_archive(world):
    scripted_completed(world)
    r = world.client.get(f"/api/results/{JOB_ID_COMPLETED}/download")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert f"{JOB_ID_COMPLETED}/fl-top/status.json" in names
    assert all(n.startswith(f"{JOB_ID_COMPLETED}/") for n in names)


def test_unknown_job_is_404(world):
    assert world.client.get("/api/loops/nope-20261103-0000-0000").status_code == 404
    assert world.client.get("/api/loops/nope-20261103-0000-0000/events").status_code == 404


def test_planner_refused_in_test(world):
    r = world.client.post("/api/plans", json={"name": "plan-one", "request": "make it", "files": []})
    assert r.status_code == 422 and r.json()["error"]["code"] == "planner-refused-in-test"


def test_plan_approval_refusals(world):
    from monitor import actions
    import plan_job
    plan_dir = world.repo / "_loop" / "plan-one" / "plan"
    plan_dir.mkdir(parents=True)
    shutil.copy(world.repo / "docs" / "pilot" / "four-loop" / "fl-top.json", plan_dir / "job.json")
    (plan_dir / "PLAN.md").write_text("# Plan" + chr(10), encoding="utf-8")                                   # the whole package is approved (I52)
    (plan_dir / "SPEC.md").write_text("- ASSUMED: the default is 3" + chr(10), encoding="utf-8")
    (plan_dir / "expected_lines.json").write_text("{}", encoding="utf-8")
    sha = plan_job.file_sha256(plan_dir / "job.json")
    rec = {"plan_id": "p1", "name": "plan-one", "request": "r", "files": [], "state": "planned", "job_sha256": sha,
           "plan_sha256": plan_job.package_sha256(plan_job.package_members(plan_dir)),
           "assumed": ["ASSUMED: the default is 3"], "reason": None, "plan_dir": str(plan_dir)}
    actions._save_plan(world.home, rec)
    assert world.client.get("/api/plans/p1").json()["state"] == "planned"
    assumed = world.client.post("/api/plans/p1/approve", json={"job_sha256": sha, "approve_assumed": False})
    assert assumed.status_code == 422 and assumed.json()["error"]["code"] == "assumed-not-approved"
    changed = world.client.post("/api/plans/p1/approve", json={"job_sha256": "0" * 64, "approve_assumed": True})
    assert changed.status_code == 409 and changed.json()["error"]["code"] == "plan-changed"
    world.monkeypatch.setattr(actions, "spawn_loop", lambda home, root: None)
    ok = world.client.post("/api/plans/p1/approve", json={"job_sha256": sha, "approve_assumed": True})
    assert ok.status_code == 202 and ok.json()["job_id"]


def test_search_and_add_with_a_fake_listing(world):
    found = world.client.get("/api/models/search", params={"q": "acme/extra"}).json()["results"]
    assert [m["model_id"] for m in found] == ["acme/extra-model:free"]
    added = world.client.post("/api/models", json={"model_id": "acme/extra-model:free"}, headers={"Idempotency-Key": "add-model-0001"})
    assert added.status_code == 201
    body = added.json()
    assert body["capabilities"]["contracts"]["writer_file_blocks"] == "unverified"
    assert body["paid"] is False and body["price_in_usd_per_mtok"] == 0


def test_listing_failure_is_503(world):
    from monitor import config_api

    def boom():
        raise RuntimeError("no network")
    world.monkeypatch.setattr(config_api, "_LISTING", {"fetch": boom})
    r = world.client.get("/api/models/search", params={"q": "x"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "provider-unavailable"


def test_verify_reasoning_one_call_per_effort_and_mode(world):
    from monitor import config_api
    calls = []

    class Client:
        def call(self, model, system, user, kind="text", max_tokens=None, timeout_s=None, effort=None, **kw):
            calls.append(effort)
            if effort == "off":
                raise oc.ReplyRejected("reasoning cannot be turned off")
            return ("OK", {})

        def own_job(self, name, fallback):
            pass

        def release_job(self):
            return False
    world.monkeypatch.setattr(config_api, "make_client", lambda home: Client())
    r = world.client.post("/api/models/verify-reasoning", json={"model_id": "nvidia/nemotron-3-ultra-550b-a55b:free",
                                                              "efforts": ["off", "low", "high"]},
                          headers={"Idempotency-Key": "verify-r-000001"})
    assert r.status_code == 200 and r.json()["reasoning_mode"] == "mandatory"
    assert r.json()["efforts_verified"] == ["low", "high"]
    assert calls == ["off", "low", "high"]                       # one free call per effort
    model = world.client.get("/api/models").json()["models"]
    row = next(m for m in model if m["model_id"] == "nvidia/nemotron-3-ultra-550b-a55b:free")
    assert row["capabilities"]["reasoning_mode"] == "mandatory"


def test_verify_contract_fail_path_is_a_fail_not_an_error(world):
    from monitor import config_api
    world.monkeypatch.setattr(mc, "verify_model", lambda client, m, c, **k: {"passed": False, "reason": "parse"})
    world.monkeypatch.setattr(config_api, "make_client", lambda home: SimpleNamespace(own_job=lambda *a: None, release_job=lambda: False))
    r = world.client.post("/api/models/verify", json={"model_id": "nvidia/nemotron-3-ultra-550b-a55b:free", "contract": "writer_file_blocks"})
    assert r.status_code == 200 and r.json()["result"] == "fail"


def test_key_values_never_appear_in_a_response(world):
    body = world.client.get("/api/system/health").text
    assert "free-key-not-a-real-value" not in body
    assert "free-key-not" not in world.client.get("/api/models").text
    keys = world.client.get("/api/system/health").json()["keys"]
    assert keys == [{"alias": "free", "present": True}, {"alias": "paid", "present": False}]


# ---------------------------------------------------------------- 3. idempotent repeats

def test_repeat_with_the_same_key_returns_the_first_response_and_runs_nothing(world):
    key = {"Idempotency-Key": "stop-repeat-0001"}
    first = world.client.post("/api/system/stop", json={"on": True}, headers=key)
    assert first.status_code == 200 and first.json() == {"stop": True}
    (world.home / "STOP").unlink()                                      # a second run would set it again
    second = world.client.post("/api/system/stop", json={"on": True}, headers=key)
    assert second.json() == {"stop": True}
    assert not (world.home / "STOP").exists()                           # nothing ran again


def test_repeat_of_a_pool_add_creates_one_model(world):
    key = {"Idempotency-Key": "add-repeat-0001"}
    assert world.client.post("/api/models", json={"model_id": "acme/extra-model:free"}, headers=key).status_code == 201
    again = world.client.post("/api/models", json={"model_id": "acme/extra-model:free"}, headers=key)
    assert again.status_code == 201
    ids = [e["model_id"] for e in json.loads(mc.CATALOG_PATH.read_text())["openrouter"]]
    assert ids.count("acme/extra-model:free") == 1


def test_bad_idempotency_key_is_400(world):
    r = world.client.post("/api/system/stop", json={"on": True}, headers={"Idempotency-Key": "x"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid-idempotency-key"


# ---------------------------------------------------------------- 4. the frozen stage snapshot, used at start

def test_queued_job_freezes_its_stage_table_and_the_start_uses_it(world):
    stage_change = [{"stage": "writer_b_write", "effort": "low"}]
    r = world.client.post("/api/jobs", json={"job_path": "docs/pilot/four-loop/fl-top.json", "stages": stage_change})
    assert r.status_code == 201
    sha = r.json()["stage_snapshot"]["sha256"]
    queued = next((world.home / "queue").glob("*.json"))
    sidecar = fix_loop.stages_file(queued)
    assert sidecar.is_file() and not list((world.home / "queue").glob("*.stages.json"))   # the sidecar never matches *.json
    frozen = json.loads(sidecar.read_text())
    assert sc.snapshot_evidence(frozen)["sha256"] == sha
    assert sc.snapshot_evidence(frozen)["revision"] == 1
    # the roster changes after the queue: the frozen table does not
    edit = world.client.put("/api/stages", json={"profile": "test", "revision": 1, "changes": [{"stage": "writer_b_write", "effort": "high"}]},
                            headers={"Idempotency-Key": "save-stage-0001"})
    assert edit.status_code == 200 and edit.json() == {"revision": 2}
    stages = {s["stage"]: s for s in world.client.get("/api/stages").json()["stages"]}
    assert stages["writer_b_write"]["effort"] == "high"
    # the loop takes the job: the frozen table comes with it, and a run gets it
    taken = fix_loop.take_next(world.home)
    assert taken is not None and fix_loop.read_stages(taken)["resolved"]["writer_b_write"]["effort"] == "low"
    seen = {}

    class Launcher:
        repo = world.repo

        def start(self, name, files, key=None, job=None):
            wt = world.home / "wt" / "fl-top-test-0001"
            wt.mkdir(parents=True, exist_ok=True)
            return {"ok": True, "job_id": "fl-top-test-0001", "gen": 1, "base": BASE, "worktree": str(wt), "branch": "job/x"}

        def finish(self, job_id, gen, state, base, result=None):
            return state
    api = SimpleNamespace(home=world.home, lease=SimpleNamespace(keep_alive=lambda j, g: (lambda: None)))

    def run(job, wt, **kw):
        seen.update(kw)
        return {"name": job["name"], "state": "passed", "round": 1}
    job = fix_loop.load_job(taken)
    world.monkeypatch.setattr(fix_loop, "write_change_doc", lambda *a, **k: None)     # the fake worktree has no repository
    res = fix_loop.run_launched(job, world.repo, api, Launcher(), run=run, stage_snapshot=fix_loop.read_stages(taken))
    assert res["state"] == "passed"
    assert sc.snapshot_evidence(seen["stage_snapshot"])["sha256"] == sha
    assert seen["stage_snapshot"]["resolved"]["writer_b_write"]["effort"] == "low"


def test_production_run_uses_the_frozen_table_not_the_roster(monkeypatch):
    """p1b_production.run hands its frozen table to _run, and _run writes that table (not a fresh read) as the job's snapshot."""
    import p1b_production
    got = {}
    monkeypatch.setattr(p1b_production, "_run", lambda *a, **kw: got.update(kw) or {"name": "x", "state": "passed"})
    p1b_production.run(job={}, root=".", status_dir_job=Path("."), sandbox=None, protected=None, acceptance_lock=None, originals=None,
                       evidence={}, client=SimpleNamespace(), parse_files=None, check_path=None, content_problem=None,
                       frozen_snapshot={"profile": "test"})
    assert got["frozen_snapshot"] == {"profile": "test"}


# ---------------------------------------------------------------- 5. pool, limits, STOP, job states (more)

def test_stop_switch_on_and_off(world):
    on = world.client.post("/api/system/stop", json={"on": True}, headers={"Idempotency-Key": "stop-on-00001"})
    assert on.json() == {"stop": True} and (world.home / "STOP").exists()
    assert world.client.get("/api/overview").json()["stop"] is True
    assert world.client.get("/api/system/health").json()["stop"] is True
    off = world.client.post("/api/system/stop", json={"on": False}, headers={"Idempotency-Key": "stop-off-00001"})
    assert off.json() == {"stop": False} and not (world.home / "STOP").exists()


def test_waiting_unknown_and_stale_warning_and_orphan_come_through(world):
    scripted_running(world, job_id="fl-top-20261103-2311-bbb1", lease="alive", waiting=False)      # then waiting with no reason below
    _set_waiting_without_reason(world, "fl-top-20261103-2311-bbb1")
    scripted_running(world, job_id="fl-top-20261103-2312-bbb2", lease="old", waiting=True)          # heartbeat late, pid alive
    scripted_running(world, job_id="fl-top-20261103-2313-bbb3", lease="stale", waiting=False)       # pid dead, heartbeat stale
    reindex(world)
    by_id = {j["job_id"]: j for j in world.client.get("/api/jobs", params={"filter": "running"}).json()["jobs"]}
    assert by_id["fl-top-20261103-2311-bbb1"]["derived_state"] == "waiting-unknown"
    assert by_id["fl-top-20261103-2311-bbb1"]["waiting"] is None
    assert by_id["fl-top-20261103-2312-bbb2"]["derived_state"] == "stale-warning"
    everything = {j["job_id"]: j for j in world.client.get("/api/jobs", params={"filter": "all"}).json()["jobs"]}
    assert everything["fl-top-20261103-2313-bbb3"]["derived_state"] == "orphan"
    assert world.client.get("/api/overview").json()["active_errors"] == 1


def test_index_missing_is_reported_not_an_error(world):
    scripted_completed(world)
    (world.home / job_history.DB_NAME).unlink(missing_ok=True)
    body = world.client.get("/api/jobs").json()
    assert body["index_ok"] is False and len(body["jobs"]) == 1
    assert world.client.get(f"/api/loops/{JOB_ID_COMPLETED}").json()["derived_state"] == "merged"


def test_every_contract_route_is_registered(world):
    from monitor import api
    have = {(m, r.path) for r in api.app.routes if hasattr(r, "methods") for m in r.methods}
    must = [("GET", "/api/overview"), ("GET", "/api/jobs"), ("GET", "/api/loops/{job_id}"), ("GET", "/api/loops/{job_id}/events"),
            ("GET", "/api/models"), ("GET", "/api/models/search"), ("GET", "/api/stages"), ("GET", "/api/results/{job_id}"),
            ("GET", "/api/results/{job_id}/diff"), ("GET", "/api/results/{job_id}/download"), ("GET", "/api/system/health"),
            ("GET", "/api/system/limits"), ("POST", "/api/jobs/validate"), ("POST", "/api/jobs"), ("POST", "/api/loops/start"),
            ("POST", "/api/plans"), ("POST", "/api/plans/{plan_id}/approve"), ("PATCH", "/api/loops/{job_id}/stages"),
            ("POST", "/api/models"), ("DELETE", "/api/models/{model_id:path}"), ("POST", "/api/models/verify-reasoning"),
            ("POST", "/api/budget/validate"), ("PUT", "/api/system/limits"), ("POST", "/api/stages/validate"), ("PUT", "/api/stages"),
            ("POST", "/api/models/verify"), ("POST", "/api/models/refresh"), ("POST", "/api/system/stop")]
    for method, path in must:
        assert (method, path) in have, (method, path)
    forbidden = [p for _, p in have if any(w in p for w in ("cancel", "resume", "merge", "retry"))]
    assert forbidden == []


def test_requirements_file_and_docs_mention_it():
    assert "fastapi" in (REPO / "monitor" / "requirements.txt").read_text()
    assert "monitor/requirements.txt" in (REPO / "docs" / "API_CONTRACT.md").read_text()


# ---------------------------------------------------------------- I2: required contract per stage; I16: Claude Code contracts; I15: limits; I17: one worker

def _unverify_in_catalog(world, model_id):
    """Clears the contracts of a catalog model in the test's copy of config/models.json (the real file holds the recorded verify results)."""
    data = json.loads(mc.CATALOG_PATH.read_text())
    e = next(e for e in data["openrouter"] if e["model_id"] == model_id)
    e["contracts"] = {c: None for c in mc.CONTRACTS}
    e["verified_at"] = None
    mc.CATALOG_PATH.write_text(json.dumps(data))


def _unverify_claude_code_in_catalog(world, model):
    """Clears the contracts of a Claude Code model in the test's copy of config/models.json (the real file holds the recorded verify results)."""
    data = json.loads(mc.CATALOG_PATH.read_text())
    e = next(e for e in data["claude_code"] if e["model"] == model)
    e["contracts"] = {c: None for c in e["contracts"]}
    e["verified_at"] = None
    mc.CATALOG_PATH.write_text(json.dumps(data))


def _verify_in_catalog(world, model_id, **contracts):
    """Records verified (true) contracts for a catalog model in the test's copy of config/models.json (what POST /models/verify writes)."""
    data = json.loads(mc.CATALOG_PATH.read_text())
    next(e for e in data["openrouter"] if e["model_id"] == model_id)["contracts"].update(contracts)
    mc.CATALOG_PATH.write_text(json.dumps(data))


def test_every_stage_row_carries_its_required_contract_and_fallback(world):
    rows = {r["stage"]: r for r in world.client.get("/api/stages", params={"profile": "test"}).json()["stages"]}
    assert set(sc.CONTRACT_OF) == set(sc.STAGE_NAMES)
    for stage, row in rows.items():
        required, fallback = sc.CONTRACT_OF[stage]
        assert row["required_contract"] == required and row["fallback_contract"] == fallback, stage
    for row in fixture("stages.json")["stages"]:                          # the fixture says the same
        assert row["required_contract"] == sc.CONTRACT_OF[row["stage"]][0], row["stage"]
        assert row["fallback_contract"] == sc.CONTRACT_OF[row["stage"]][1], row["stage"]
    assert rows["planner"]["required_contract"] == "planner_json"
    assert {rows[s]["required_contract"] for s in ("writer_a_write", "writer_b_write")} == {"writer_file_blocks"}
    repairs = ("writer_a_repair", "writer_b_repair", "review_fix_repair", "specialist_repair")
    assert {rows[s]["required_contract"] for s in repairs} == {"repair_edit_blocks"}
    assert {rows[s]["fallback_contract"] for s in repairs} == {"repair_whole_file"}
    assert {rows[s]["required_contract"] for s in ("reviewer_b", "reviewer_a", "reviewer_reask")} == {"reviewer_verdict"}
    assert rows["writer_a_write"]["fallback_contract"] is None and rows["reviewer_b"]["fallback_contract"] is None


def test_a_stage_change_is_refused_until_its_model_has_the_required_contract_verified(world):
    _unverify_in_catalog(world, "dots-studio/dots-3-note-preview:free")          # the real catalog verifies it; this test starts from none
    write = {"profile": "test", "changes": [{"stage": "writer_a_write", "settings": {"temperature": 0.2}}]}
    bad = world.client.post("/api/stages/validate", json=write)
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "contract-not-verified"
    problem = bad.json()["error"]["details"]["problems"][0]
    assert problem["stage"] == "writer_a_write" and "writer_file_blocks" in problem["message"]
    repair = {"profile": "test", "changes": [{"stage": "writer_a_repair", "settings": {"temperature": 0.2}}]}
    _verify_in_catalog(world, "dots-studio/dots-3-note-preview:free", writer_file_blocks=True)
    assert world.client.post("/api/stages/validate", json=write).json() == {"valid": True, "problems": []}
    refused = world.client.post("/api/stages/validate", json=repair)               # a repair needs repair_edit_blocks, not writer_file_blocks
    assert refused.status_code == 400 and refused.json()["error"]["code"] == "contract-not-verified"
    assert "repair_edit_blocks" in refused.json()["error"]["details"]["problems"][0]["message"]
    _verify_in_catalog(world, "dots-studio/dots-3-note-preview:free", repair_edit_blocks=True)
    assert world.client.post("/api/stages/validate", json=repair).json() == {"valid": True, "problems": []}


def test_a_locked_row_sent_with_a_null_or_unchanged_model_is_accepted_and_ignored(world):
    """M3: a locked row (model_selectable false in GET /stages) sent with model null, or with the model it already shows, is accepted by
    POST /stages/validate, POST /jobs and PUT /stages (the value is ignored). A changed model on it is model-not-selectable. An unlocked
    row still refuses a null model (bad-model)."""
    stages = {row["stage"]: row for row in world.client.get("/api/stages", params={"profile": "test"}).json()["stages"]}
    locked = {name for name, row in stages.items() if not row["model_selectable"]}
    assert {"reviewer_reask", "review_fix_repair", "reviewer_a"} <= locked
    nulls = [{"stage": name, "model": None} for name in sorted(locked)]
    assert world.client.post("/api/stages/validate", json={"profile": "test", "changes": nulls}).json() == {"valid": True, "problems": []}
    shown = [{"stage": name, "model": stages[name]["model"], "effort": stages[name]["effort"]} for name in sorted(locked)]
    assert world.client.post("/api/stages/validate", json={"profile": "test", "changes": shown}).json() == {"valid": True, "problems": []}
    queued = world.client.post("/api/jobs", json={"job_path": "docs/pilot/four-loop/fl-top.json",
                                                  "stages": {"reviewer_reask": {"model": None, "effort": "low"}, "reviewer_a": {"model": None}}})
    assert queued.status_code == 201 and queued.json()["queued"] is True
    revision = world.client.get("/api/stages", params={"profile": "test"}).json()["revision"]
    saved = world.client.put("/api/stages", json={"profile": "test", "revision": revision, "changes": nulls},
                             headers={"Idempotency-Key": "stages-locked-0001"})
    assert saved.status_code == 200 and saved.json() == {"revision": revision + 1}
    changed = world.client.post("/api/stages/validate", json={"profile": "test", "changes": [
        {"stage": "reviewer_a", "model": "dots-studio/dots-3-note-preview:free"}]})
    assert changed.status_code == 400 and changed.json()["error"]["code"] == "model-not-selectable"
    assert changed.json()["error"]["details"]["problems"][0]["stage"] == "reviewer_a"
    fixed = world.client.post("/api/stages/validate", json={"profile": "test", "changes": [
        {"stage": "review_fix_repair", "via": "openrouter"}]})
    assert fixed.status_code == 400 and fixed.json()["error"]["code"] == "model-not-selectable"
    queued_changed = world.client.post("/api/jobs", json={"job_path": "docs/pilot/four-loop/fl-top.json", "rerun": True,
                                                          "stages": {"reviewer_reask": {"model": "dots-studio/dots-3-note-preview:free"}}})
    assert queued_changed.status_code == 400 and queued_changed.json()["error"]["code"] == "model-not-selectable"
    unlocked = world.client.post("/api/stages/validate", json={"profile": "test", "changes": [{"stage": "writer_a_write", "model": None}]})
    assert unlocked.status_code == 400 and unlocked.json()["error"]["code"] == "bad-model"


def test_a_claude_code_model_is_not_a_valid_stage_model_while_its_contract_is_unverified(world):
    change = {"profile": "production", "changes": [{"stage": "planner", "model": "haiku", "via": "claude_code", "effort": "high"}]}
    r = world.client.post("/api/stages/validate", json=change)
    assert r.status_code == 400 and r.json()["error"]["code"] == "contract-not-verified"
    assert "planner_json unverified" in r.json()["error"]["details"]["problems"][0]["message"]


def test_a_fresh_claude_code_entry_shows_its_contracts_unverified_with_text_validated(world):
    _unverify_claude_code_in_catalog(world, "haiku")                       # the real catalog verifies reviewer_verdict; this test starts from none
    rows = {m["model_id"]: m for m in world.client.get("/api/models").json()["models"]}
    haiku = rows["haiku"]["capabilities"]
    assert haiku["text_validated"] is True
    contracts = haiku["contracts"]
    assert set(contracts) == {"planner_json", "writer_file_blocks", "repair_edit_blocks", "repair_whole_file", "reviewer_verdict",
                              "native_structured_output"}
    text = {k: v for k, v in contracts.items() if k != "native_structured_output"}
    assert text and set(text.values()) == {"unverified"}
    assert contracts["native_structured_output"] == "not-applicable"
    shown = next(m for m in fixture("models.json")["models"] if m["model_id"] == "haiku")["capabilities"]["contracts"]
    assert shown == contracts                                              # the fixture matches the response


FAKE_CLAUDE = REPO / "pieces" / "fake_claude.py"


def _fake_claude_reply(world, text, is_error=False):
    """Points the Claude Code runner at the fake claude, whose one reply is `text` (a JSON envelope, as `claude -p --output-format json`).
    Returns the fake's log file: one line per run, so a test can see whether a process started at all."""
    out = world.tmp / "fake_reply.json"
    out.write_text(json.dumps({"type": "result", "is_error": is_error, "result": text, "usage": {"input_tokens": 7, "output_tokens": 3}}),
                   encoding="utf-8")
    log = world.tmp / "fake_claude.log"
    world.monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    world.monkeypatch.setenv("FAKE_CLAUDE_OUTPUT", str(out))
    world.monkeypatch.setattr(cc, "CLAUDE_CMD", [sys.executable, str(FAKE_CLAUDE)])
    return log


def _verify(world, model_id, contract, n, **extra):
    return world.client.post("/api/models/verify", json={"model_id": model_id, "contract": contract, **extra},
                             headers={"Idempotency-Key": f"verify-{model_id}-{n:04d}"})


def test_verify_a_claude_code_model_runs_the_probe_through_the_fake_claude_and_records_a_pass(world):
    _unverify_claude_code_in_catalog(world, "haiku")                       # the real catalog verifies reviewer_verdict; this test starts from none
    log = _fake_claude_reply(world, "FILE: hello.py\n```python\nx = 2\n```")
    r = _verify(world, "haiku", "writer_file_blocks", 1)
    assert r.status_code == 200 and r.json()["result"] == "pass" and r.json()["verified_at"]
    run = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert run["args"][run["args"].index("--model") + 1] == "haiku" and run["args"][run["args"].index("--effort") + 1] == "high"
    assert run["api_key_in_env"] is False and run["guarded_env"] == []            # the isolated call: no API key, no OpenRouter variable
    models = {m["model_id"]: m for m in world.client.get("/api/models").json()["models"]}
    contracts = models["haiku"]["capabilities"]["contracts"]
    assert contracts["writer_file_blocks"] == "verified" and contracts["repair_whole_file"] == "verified"
    assert contracts["repair_edit_blocks"] == "unverified" and contracts["reviewer_verdict"] == "unverified"
    assert contracts["planner_json"] == "unverified" and models["haiku"]["verified_at"] is not None
    assert models["sonnet"]["capabilities"]["contracts"]["writer_file_blocks"] == "unverified"       # only haiku was checked
    stored = next(e for e in json.loads(mc.CATALOG_PATH.read_text())["claude_code"] if e["model"] == "haiku")
    assert stored["contracts"]["writer_file_blocks"] is True and stored["verified_at"]["writer_file_blocks"] == r.json()["verified_at"]


def test_a_claude_code_reply_that_fails_the_contract_records_a_fail(world):
    _fake_claude_reply(world, "Sure, here is a plain sentence with no file block.")
    r = _verify(world, "haiku", "writer_file_blocks", 2)
    assert r.status_code == 200 and r.json()["result"] == "fail" and r.json()["verified_at"]
    models = {m["model_id"]: m for m in world.client.get("/api/models").json()["models"]}
    assert models["haiku"]["capabilities"]["contracts"]["writer_file_blocks"] == "failed"


def test_a_claude_code_call_that_gives_no_reply_records_nothing(world):
    _fake_claude_reply(world, "", is_error=True)
    r = _verify(world, "haiku", "writer_file_blocks", 3)
    assert r.status_code == 503 and r.json()["error"]["code"] == "provider-unavailable"
    models = {m["model_id"]: m for m in world.client.get("/api/models").json()["models"]}
    assert models["haiku"]["capabilities"]["contracts"]["writer_file_blocks"] == "unverified"


def test_the_test_profile_verifies_only_haiku_at_high_and_refuses_the_rest_before_any_call(world):
    world.monkeypatch.setenv("SATVIK_PROFILE", "test")
    log = _fake_claude_reply(world, "FILE: hello.py\n```python\nx = 2\n```")
    for n, (model, effort) in enumerate((("sonnet", "high"), ("haiku", "low"), ("opus", None)), start=1):
        extra = {} if effort is None else {"effort": effort}
        r = _verify(world, model, "writer_file_blocks", 10 + n, **extra)
        assert r.status_code == 422 and r.json()["error"]["code"] == "not-allowed-in-test", (model, effort)
    assert not log.exists()                                                       # no claude process started for any of them
    assert _verify(world, "haiku", "writer_file_blocks", 20, effort="high").status_code == 200


def test_a_claude_code_verify_refuses_an_unknown_effort_and_contract_without_a_call(world):
    log = _fake_claude_reply(world, "FILE: hello.py\n```python\nx = 2\n```")
    assert _verify(world, "haiku", "writer_file_blocks", 30, effort="ultra").json()["error"]["code"] == "bad-effort"
    assert _verify(world, "haiku", "planner_json", 31).json()["error"]["code"] == "unknown-contract"
    assert not log.exists()


def test_a_verified_claude_code_model_can_be_put_on_a_stage_and_an_unverified_one_cannot(world):
    change = {"profile": "production", "changes": [{"stage": "writer_a_write", "model": "haiku", "via": "claude_code", "effort": "high"}]}
    refused = world.client.post("/api/stages/validate", json=change)
    assert refused.status_code == 400 and refused.json()["error"]["code"] == "contract-not-verified"
    _fake_claude_reply(world, "FILE: hello.py\n```python\nx = 2\n```")
    assert _verify(world, "haiku", "writer_file_blocks", 40).status_code == 200
    assert world.client.post("/api/stages/validate", json=change).json() == {"valid": True, "problems": []}


def test_a_job_review_usd_other_than_the_engine_value_is_refused_and_not_stored(world):
    bad = world.client.put("/api/system/limits", json={"revision": 1, "job_review_usd": 0.01}, headers={"Idempotency-Key": "limits-jrv-000001"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "not-applied-by-engine"
    message = bad.json()["error"]["message"]
    assert "0.05" in message and "production budget gate" in message and "0.01" in message
    assert bad.json()["error"]["details"] == {"required": 0.05, "given": 0.01}
    zero = world.client.put("/api/system/limits", json={"revision": 1, "job_review_usd": 0}, headers={"Idempotency-Key": "limits-jrv-000002"})
    assert zero.status_code == 422 and zero.json()["error"]["code"] == "not-applied-by-engine"
    assert limits_config.load()["revision"] == 1 and limits_config.job_review_usd() == 0.05          # nothing was written
    ok = world.client.put("/api/system/limits", json={"revision": 1, "job_review_usd": 0.05, "sandbox_slots": 2},
                          headers={"Idempotency-Key": "limits-jrv-000003"})
    assert ok.status_code == 200 and ok.json() == {"revision": 2}


def test_job_review_usd_out_of_range_is_422_out_of_range_and_in_range_elsewhere_is_not_applied(world):
    """M4: a value outside the limit's bounds (job_review_usd 0..0.05) is 422 out-of-range; an in-range value other than the engine's 0.05
    is 422 not-applied-by-engine. Nothing is stored for either. The bounds are the contract's (section 3, Update limits)."""
    for number, value in (("out-high", 0.06), ("out-low", -0.01)):
        r = world.client.put("/api/system/limits", json={"revision": 1, "job_review_usd": value},
                             headers={"Idempotency-Key": f"limits-range-{number}"})
        assert r.status_code == 422 and r.json()["error"]["code"] == "out-of-range", (value, r.json())
    inside = world.client.put("/api/system/limits", json={"revision": 1, "job_review_usd": 0.04},
                              headers={"Idempotency-Key": "limits-range-inside"})
    assert inside.status_code == 422 and inside.json()["error"]["code"] == "not-applied-by-engine"
    assert limits_config.load()["revision"] == 1 and limits_config.job_review_usd() == 0.05
    contract = (REPO / "docs" / "API_CONTRACT.md").read_text(encoding="utf-8")
    assert "422 `out-of-range`" in contract and "job_review_usd` other than the engine's 0.05" in contract


def test_the_run_command_pins_one_worker_in_the_docs_the_requirements_and_the_app(world):
    command = "python -m uvicorn monitor.api:app --port 8765 --workers 1"
    assert command in (REPO / "monitor" / "requirements.txt").read_text(encoding="utf-8")
    assert command in (REPO / "monitor" / "api.py").read_text(encoding="utf-8")
    contract = (REPO / "docs" / "API_CONTRACT.md").read_text(encoding="utf-8")
    section_0 = contract.split("## 0. Conventions", 1)[1].split("## 1. Enums", 1)[0]
    assert command in section_0 and "single worker" in section_0.lower()
