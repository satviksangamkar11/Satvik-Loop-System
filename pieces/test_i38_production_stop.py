"""I38 (2026-11-04): the STOP switch reaches the production ladder. fix_loop.run_job's production dispatch passes its stop_check into the P1b runner,
which checks it before each new model step and before each retry or wait. The in-flight call finishes; nothing new starts; the job ends
"stopped" and its files go back to the originals.

These tests run the REAL production path (fix_loop.run_job -> p1b_production.run -> p1b_routing.walk -> the real openrouter_call adapter)
against the scripted HTTP transport of pieces/prod_harness.py: a fake client, not a fake runner. The sandbox is the real Docker one, so the
tests skip where Docker is not available (the same rule as test_p1b_production_run.py)."""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
import fix_loop as fl  # noqa: E402
import openrouter_call as oc  # noqa: E402
import p1b_routing as p1b  # noqa: E402
import prod_harness as ph  # noqa: E402
from test_p0_acceptance import make_prod_root, CONSTANT_CODE, CONSTANT_TEST  # noqa: E402
from test_p1b_production_run import p1b_job, load, needs_docker  # noqa: E402

NL = "\n"


@pytest.fixture(autouse=True)
def _production_profile(profile):
    profile("production")


def roles():
    return oc.roster_state()["ROLES"]


def stop_on(home):
    """Turns the operator's switch on: home/STOP exists, which launch.stop_requested reads."""
    (home / "STOP").write_text("", encoding="utf-8")


class StopOnFirstPost(ph.Provider):
    """The first writer POST raises a network error after the operator turns STOP on: the adapter's retry path then reaches its wait."""

    def __init__(self, script, home):
        super().__init__(script)
        self.home = home
        self.raised = False

    def __call__(self, method, url, headers, body, timeout):
        if method == "POST" and url.endswith("/chat/completions") and not self.raised:
            self.raised = True
            self.http.append((method, url, headers))
            stop_on(self.home)
            raise ConnectionResetError("connection reset by peer")
        return super().__call__(method, url, headers, body, timeout)


@needs_docker
def test_stop_turned_on_during_a_writer_call_ends_the_job_before_its_repair_step(tmp_path):
    """A red first write is followed by a repair. The operator turns STOP on while the first write is in flight: the write finishes, the
    repair (a new model step) is never sent, no other model is called, and the job's files are restored."""
    root = make_prod_root(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    job = load(root, p1b_job())

    def write_then_stop(request):
        stop_on(home)                                                  # the operator flips the switch during this call
        return ph.file_reply(("pieces/calc.py", CONSTANT_CODE), ("pieces/test_calc.py", CONSTANT_TEST))

    provider = ph.Provider({roles()["writer_a"]: [write_then_stop, ph.file_reply(("pieces/calc.py", CONSTANT_CODE),
                                                                                  ("pieces/test_calc.py", CONSTANT_TEST))]})
    result = fl.run_job(job, root, openrouter=ph.client(tmp_path, provider, budget=oc.JobBudget(40, 0.05)), scrub_keys=[],
                        stop_check=fl._stop_switch(home))

    assert result["state"] == "stopped", result
    assert provider.models() == [roles()["writer_a"]], provider.models()     # the one call in flight finished; no repair, no other model
    # the job's files go back to the originals: this repo had no pieces/calc.py, so the candidate is removed from the checkout
    assert not (root / "pieces" / "calc.py").exists() and not (root / "pieces" / "test_calc.py").exists()
    status = json.loads((root / "_loop" / "calc" / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "stopped" and "stop switch is on" in status["reason"], status["reason"]


@needs_docker
def test_stop_turned_on_while_a_retry_waits_means_the_retry_is_never_sent(tmp_path):
    """The first call fails at the network; the operator turns STOP on; the adapter's retry wait begins and the wait hook raises StopRequested
    before its sleep. Exactly one POST was sent: the retry is never sent."""
    root = make_prod_root(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    job = load(root, p1b_job())
    provider = StopOnFirstPost({roles()["writer_a"]: [ph.file_reply(("pieces/calc.py", CONSTANT_CODE), ("pieces/test_calc.py", CONSTANT_TEST))]},
                               home)
    result = fl.run_job(job, root, openrouter=ph.client(tmp_path, provider, budget=oc.JobBudget(40, 0.05)), scrub_keys=[],
                        stop_check=fl._stop_switch(home))

    assert result["state"] == "stopped", result
    posts = [m for m, url, _h in provider.http if m == "POST"]
    assert len(posts) == 1, f"the retry was sent after the STOP: {len(posts)} POSTs"
    assert provider.models() == [], "no completion was received; the failed call is the only request"


@needs_docker
def test_with_the_switch_off_the_same_production_path_runs_the_ladder_as_before(tmp_path):
    """The control: with no STOP file the job is not stopped; the first write is green and the job reaches its review."""
    root = make_prod_root(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    job = load(root, p1b_job())
    provider = ph.Provider({roles()["writer_a"]: [ph.file_reply(("pieces/calc.py", CONSTANT_CODE), ("pieces/test_calc.py", CONSTANT_TEST))]})
    result = fl.run_job(job, root, openrouter=ph.client(tmp_path, provider, budget=oc.JobBudget(40, 0.05)), scrub_keys=[],
                        stop_check=fl._stop_switch(home))
    assert result["state"] != "stopped", result
    assert provider.models()[0] == roles()["writer_a"]


def test_a_model_step_checks_the_stop_before_any_request_is_sent():
    """The guard is the production object's own: with the stop check raising, _ask raises before it reads the stage or touches the client."""
    import p1b_production as pm

    class NoClient:
        def call(self, *args, **kwargs):
            raise AssertionError("no request may be sent after STOP")

    prod = pm.Production.__new__(pm.Production)            # the real class; only the stop check and the client are set
    prod.stop_check, prod.client = lambda: (_ for _ in ()).throw(p1b.StopRequested("stop switch is on")), NoClient()
    with pytest.raises(p1b.StopRequested):
        prod._ask("model-x", "system", "user", kind="writer", mode="write")
