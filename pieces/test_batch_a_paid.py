"""Batch A (paid-call safety, codex audit 2026-11-04): I36, I44, I48, I49, I50. Fakes only: a scripted OpenRouter transport, a fake model slot,
a ledger in a temp SATVIK_HOME, and the monitor API on a temp home. No network, no model call.

I36 the billing class, pin and prices of a stage are the selected model's (config/models.json, the roster); a mismatch is refused.
I44 a job with a paid stage (or a paid budget) is validated; its approved ceiling is frozen with it and the engine refuses beyond it.
I48 the account pace is taken after the model slot, immediately before the POST.
I49 the paid worst case is a byte bound on the prompt, priced at the higher of the roster's and the live endpoint's price; a stale price is read again.
I50 each paid reservation is its own hold (call id, owner pid and start time); a dead owner's hold is released as spent once stale; no midnight clear.
"""
import contextlib
import json
import os
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
sys.path.insert(0, str(REPO / "pieces"))
import loop_home as lh  # noqa: E402
import openrouter_call as oc  # noqa: E402
import shared_limits as sl  # noqa: E402
import stage_config as sc  # noqa: E402
from test_openrouter_call import (DOTS, GLM_PAID, KEYS, REVIEW_OK, WRITER_OK, Clock, Fake, client, ok, paid_fake)  # noqa: E402
from test_api_contract import world  # noqa: E402,F401  (the monitor API on a temp home)

ENDPOINTS_HIGHER = {"data": {"endpoints": [{"tag": "z-ai/fp8", "provider_name": "Z.AI",
                                            "pricing": {"prompt": "0.0000003", "completion": "0.0000005"}}]}}


def codes(problems):
    return {p["code"] for p in problems}


@pytest.fixture
def home(tmp_path, monkeypatch):
    folder = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setenv("SATVIK_HOME", str(folder))
    return folder


def production_stage(stage_name="reviewer_a"):
    """The engine's own call spec for a production stage (a snapshot's, not a hand-written one)."""
    snap, _ = sc.snapshot("production")
    return sc.call_spec(sc.profile_of(snap), stage_name)


# ---------------------------------------------------------------- I36: billing from the model
def test_I36_a_paid_model_on_a_free_stage_fails_validation(profile):
    profile("production")
    problems = sc.validate({"writer_a_write": {"model": GLM_PAID}}, "production")
    assert {"billing-mismatch", "paid-model-stage"} <= codes(problems), problems


def test_I36_a_stage_spec_carries_its_models_billing_not_its_roles(profile):
    profile("production")
    glm = production_stage("reviewer_a")
    assert glm["paid"] is True and glm["provider"] == {"name": "Z.AI", "slug": "z-ai"} and glm["pricing"]["prompt"] == pytest.approx(0.15e-6)
    free = production_stage("reviewer_b")
    assert free["paid"] is False and free["provider"] is None and free["pricing"] is None


def test_I36_openrouter_refuses_a_stage_whose_paid_flag_is_not_the_models(tmp_path, profile, home):
    profile("production")
    fake = paid_fake(ok(REVIEW_OK))
    built, _ = client(tmp_path, fake)
    spec = production_stage("reviewer_a")
    lying = dict(spec, paid=False, provider=None, pricing=None)              # sent as free: the paid model on the free key
    with pytest.raises(oc.ModelRefused, match="paid"):
        built.call("reviewer_a", "system", "user", kind="reviewer", stage=lying)
    assert fake.posts() == [] and fake.calls == []


# ---------------------------------------------------------------- I44: the approved ceiling
def _production_job(world):
    """A copy of the four-loop test job in the production shape: the owner's review budget (0.05), so it passes the production gates. Its
    stages take the reviewer_a roster role, the paid GLM reviewer."""
    job = json.loads((world.repo / "docs" / "pilot" / "four-loop" / "fl-top.json").read_text(encoding="utf-8"))
    job["budget"] = {"free_requests": 40, "review_usd": 0.05, "paid_usd": 0}
    path = world.repo / "docs" / "pilot" / "four-loop" / "fl-prod.json"
    path.write_text(json.dumps(job), encoding="utf-8")
    return "docs/pilot/four-loop/fl-prod.json"


def test_I44_a_job_with_a_paid_stage_is_refused_without_an_approved_budget(world):
    world.monkeypatch.setenv("SATVIK_PROFILE", "production")
    r = world.client.post("/api/jobs", json={"job_path": _production_job(world), "paid_budget_usd": 0})
    assert r.status_code == 400 and r.json()["error"]["code"] == "budget-over-cap", r.text
    assert not list((world.home / "queue").glob("*.json"))


def test_I44_an_approved_ceiling_is_frozen_with_the_queued_job(world):
    import fix_loop
    world.monkeypatch.setenv("SATVIK_PROFILE", "production")
    r = world.client.post("/api/jobs", json={"job_path": _production_job(world), "paid_budget_usd": 0.02})
    assert r.status_code == 201, r.text
    queued = next((world.home / "queue").glob("*.json"))
    assert fix_loop.read_stages(queued)["paid_ceiling_usd"] == pytest.approx(0.02)


def test_I44_an_unpriced_paid_approval_refuses_the_job(world, monkeypatch):
    import monitor.actions as actions
    import monitor.config_api as config_api
    from monitor.store import ApiError
    world.monkeypatch.setenv("SATVIK_PROFILE", "production")
    monkeypatch.setattr(config_api, "validate_budget", lambda *a, **k: {"valid": False, "approved_usd": None, "problems": [{"code": "unknown-price"}]})
    with pytest.raises(ApiError) as caught:
        actions.approve_paid({}, 0.02)
    assert caught.value.status == 422 and caught.value.code == "budget-unknown"


def test_I44_the_engine_refuses_a_paid_call_beyond_the_snapshot_ceiling(tmp_path, profile):
    profile("production")
    fake = paid_fake(ok(REVIEW_OK))
    built, _ = client(tmp_path, fake)
    spec = dict(production_stage("reviewer_a"), ceiling_usd=0.00001)
    with pytest.raises(oc.ReviewBudgetExceeded):
        built.call("reviewer_a", "system", "user", kind="reviewer", stage=spec)
    assert fake.posts() == []


# ---------------------------------------------------------------- I48: the pace is taken after the slot, right before the POST
def test_I48_the_pace_is_taken_inside_the_model_slot_right_before_the_post(tmp_path, home, monkeypatch):
    events = []

    @contextlib.contextmanager
    def slot(model, gen, timeout_s, home_dir=None):
        events.append("slot-in")
        yield
        events.append("slot-out")

    real_pace = sl.pace

    def pace(**kwargs):
        events.append("pace")
        return real_pace(**kwargs)

    monkeypatch.setattr(sl, "model_slot", slot)
    monkeypatch.setattr(sl, "pace", pace)
    fake = Fake()
    fake.on("/chat/completions", ok(WRITER_OK))
    built, _ = client(tmp_path, fake)
    built.set_job("i48-job", 1)
    orig = fake.__call__

    def post(method, url, headers, body, timeout):
        events.append("post")
        return orig(method, url, headers, body, timeout)

    built.transport = post
    built.call("writer_a", "system", "user", kind="writer")
    assert events == ["slot-in", "pace", "post", "slot-out"]


def test_I48_a_free_call_that_waits_for_the_pace_sends_nothing_and_counts_no_send(tmp_path, home, monkeypatch):
    monkeypatch.setattr(sl, "PER_MINUTE", 1)
    sl.reserve_free("i48-filler", 1)                                  # the account pace is full this minute
    fake = Fake()
    fake.on("/chat/completions", ok(WRITER_OK))
    built, clock = client(tmp_path, fake)
    built.set_job("i48-job", 1)
    built.set_job_deadline(5)
    with pytest.raises(oc.ProviderUnavailable, match="minute window"):
        built.call("writer_a", "system", "user", kind="writer")
    assert fake.calls == []                                            # nothing posted: the refused pace is not a send


def test_I48_a_paid_call_refused_for_pace_releases_its_hold(tmp_path, home, monkeypatch, profile):
    profile("production")
    monkeypatch.setattr(sl, "PER_MINUTE", 1)
    sl.reserve_free("i48-filler", 1)
    fake = paid_fake(ok(REVIEW_OK))
    built, _ = client(tmp_path, fake)
    built.set_job("i48-job", 1)
    built.set_job_deadline(5)
    with pytest.raises(oc.ProviderUnavailable, match="pace"):
        built.call("reviewer_a", "system", "user", kind="reviewer")
    assert sl.usage()["review_reserved_usd"] == pytest.approx(0.0)
    assert not [c for c in fake.calls if c["url"].endswith("/chat/completions")]


# ---------------------------------------------------------------- I49: the worst case
def test_I49_the_prompt_bound_is_one_token_per_byte_not_chars_over_three():
    prices = {"prompt": 1e-6, "completion": 0.0}
    assert oc.review_reservation("", "é" * 100, 0, prices) == pytest.approx((200 + oc.PROMPT_FRAMING_TOKENS) * 1e-6)


def test_I49_a_paid_reservation_uses_the_higher_live_price(tmp_path, profile):
    profile("production")
    seen = {}
    fake = Fake()
    fake.on("/endpoints", (200, {}, json.dumps(ENDPOINTS_HIGHER).encode()))

    def check(request):
        seen["reserved"] = built.budget.review_reserved
        return ok(REVIEW_OK)(request)

    fake.on("/chat/completions", check)
    built, _ = client(tmp_path, fake)
    built.call("reviewer_a", "system", "user", kind="reviewer")
    tokens = oc.roster_state()["SETTINGS"][GLM_PAID]["max_tokens"]
    live = {"prompt": 3e-7, "completion": 5e-7}                        # the endpoint is twice the roster's prompt price
    assert seen["reserved"] == pytest.approx(oc.review_reservation("system", "user", tokens, live))


def test_I49_a_stale_endpoint_price_is_read_again(tmp_path, profile):
    profile("production")
    clock = Clock()
    fake = paid_fake(ok(REVIEW_OK))
    fake.on("/endpoints", (200, {}, json.dumps(ENDPOINTS_HIGHER).encode()))
    fake.on("/chat/completions", ok(REVIEW_OK))
    built, clock = client(tmp_path, fake, clock=clock)
    built.call("reviewer_a", "system", "user", kind="reviewer")
    clock.now += oc.ENDPOINT_MAX_AGE_S + 1
    built.call("reviewer_a", "system", "user", kind="reviewer")
    reads = [c for c in fake.calls if c["url"].endswith("/endpoints")]
    assert len(reads) == 2


# ---------------------------------------------------------------- I50: per-call holds, reconciliation, no midnight clear
def _ledger(home):
    return json.loads((home / "ledger" / "limits.json").read_text(encoding="utf-8"))


def _write_ledger(home, data):
    (home / "ledger").mkdir(parents=True, exist_ok=True)
    (home / "ledger" / "limits.json").write_text(json.dumps(data), encoding="utf-8")


def test_I50_each_paid_hold_has_its_own_call_id_and_settles_alone(home):
    first = sl.reserve_review("job-x", 1, 0.1)
    second = sl.reserve_review("job-x", 1, 0.1)
    assert first and second and first != second
    sl.settle_review("job-x", 1, 0.1, 0.05, call_id=first)
    usage = sl.usage()
    assert usage["review_reserved_usd"] == pytest.approx(0.1) and usage["review_spent_usd"] == pytest.approx(0.05)
    assert usage["open_holds"] == 1


def test_I50_a_hold_of_a_dead_owner_is_released_as_spent_once_stale(home):
    call_id = sl.reserve_review("job-x", 1, 0.1)
    data = _ledger(home)
    data["holds"][call_id].update(pid=4000000, start=None, at=time.time() - 600)      # no such process, and older than the stale window
    _write_ledger(home, data)
    usage = sl.usage()
    assert usage["review_reserved_usd"] == pytest.approx(0.0) and usage["review_spent_usd"] == pytest.approx(0.1)


def test_I50_a_young_hold_of_a_dead_owner_is_kept(home):
    call_id = sl.reserve_review("job-x", 1, 0.1)
    data = _ledger(home)
    data["holds"][call_id].update(pid=4000000, start=None)
    _write_ledger(home, data)
    assert sl.usage()["review_reserved_usd"] == pytest.approx(0.1)


def test_I50_a_hold_older_than_the_max_age_is_released_even_with_a_live_owner(home):
    call_id = sl.reserve_review("job-x", 1, 0.1)
    data = _ledger(home)
    data["holds"][call_id]["at"] = time.time() - sl.HOLD_MAX_AGE_S - 10
    _write_ledger(home, data)
    assert sl.usage()["review_reserved_usd"] == pytest.approx(0.0)


def test_I50_open_holds_survive_the_day_change(home):
    """A hold taken a minute before the 05:30 IST day change and read a minute after it: the day's counts reset, the open hold does not."""
    from datetime import datetime
    boundary = datetime(2026, 11, 2, 5, 30, tzinfo=sl.IST).timestamp()
    call_id = sl.reserve_review("job-x", 1, 0.1)
    data = _ledger(home)
    data["holds"][call_id]["at"] = boundary - 60
    data["day"] = sl.day_key(boundary - 60)
    _write_ledger(home, data)
    st = sl._state(home, boundary + 60)
    assert st["day"] != sl.day_key(boundary - 60)
    assert st["review_reserved"] == 100_000 and st["review_spent"] == 0


def test_I50_the_job_ceiling_holds_across_a_settle(home):
    first = sl.reserve_review("job-x", 1, 0.02, ceiling_usd=0.03)
    sl.settle_review("job-x", 1, 0.02, 0.02, call_id=first)
    with pytest.raises(sl.JobCeilingReached):
        sl.reserve_review("job-x", 1, 0.02, ceiling_usd=0.03)                  # 0.02 booked + 0.02 would pass 0.03
    assert sl.reserve_review("job-y", 1, 0.02, ceiling_usd=0.03)                # another job has its own ceiling


def test_I50_a_late_settle_after_reconciliation_books_nothing_twice(home):
    call_id = sl.reserve_review("job-x", 1, 0.1)
    data = _ledger(home)
    data["holds"][call_id].update(pid=4000000, start=None, at=time.time() - 600)
    _write_ledger(home, data)
    sl.settle_review("job-x", 1, 0.1, 0.03, call_id=call_id)                    # the call finished after all: nothing more is booked
    assert sl.usage()["review_spent_usd"] == pytest.approx(0.1)


def test_I50_a_free_request_is_counted_without_a_send_record(home):
    sl.reserve_free_day("job-x", 1)
    usage = sl.usage()
    assert usage["free_today"] == 1 and usage["sent_last_60s"] == 0


def test_I50_process_start_identifies_this_process():
    if not Path("/proc/self/stat").is_file():
        pytest.skip("no /proc on this platform")
    assert lh.process_start(os.getpid()) is not None
    assert lh.process_start(4000000) is None
