"""I35 (docs/CLOSURE_CHECKLIST.md): the Recent Jobs table on the Windows PC showed Unknown for the finish time of older merged jobs, although the
registry row of every job holds its finish time (loop_home.Registry.record writes `finished`). The view read the finish time only from the
finish event and the merge outcome, so a job without them had none. The view now falls back to the registry's `finished`. The end state,
tests and review verdict of such a job have no source in the files (see docs/ARCHITECTURE_REVISION_4.md, Known observations), so they stay
unknown. Fakes only: a scripted run, no network, no model call, no secrets."""
import json
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
sys.path.insert(0, str(REPO / "pieces"))
sys.path.insert(0, str(REPO))

import event_log  # noqa: E402
import fix_loop as fl  # noqa: E402
import job_history as jh  # noqa: E402
from monitor import views  # noqa: E402
from test_fix_loop_wiring import JOB, FakeMerge, env_for  # noqa: E402
from test_p1_events import writes_events  # noqa: E402


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(epoch)))


def _merged_job(tmp_path):
    """A job that passed and was merged: its registry row is the merge outcome (state merged), as merge_queue leaves it."""
    env = env_for(tmp_path)
    job_id = fl.run_launched(JOB, env.repo, env.api, env.launcher, merge_queue=FakeMerge(), run=writes_events("passed"))["job_id"]
    key = next(r["key"] for r in env.api.registry.recent(50) if r["job"] == job_id)
    row = env.api.registry.lookup(key)
    env.api.registry.record(key, job_id, row["gen"], "merged", row["base"], {"head": "a" * 40, "attempts": 1})
    return env, job_id


def test_a_merged_job_with_no_finish_event_shows_its_registry_finish_time(tmp_path):
    env, job_id = _merged_job(tmp_path)
    # The shape of a job from before the finish event and status file were kept: the worktree is gone, and the archive has neither.
    shutil.rmtree(env.home / "wt" / job_id, ignore_errors=True)
    for path in (env.home / "results" / job_id).glob("*/" + event_log.FILE):
        kept = [line for line in path.read_text(encoding="utf-8").splitlines() if json.loads(line).get("type") != "finish"]
        path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    for path in (env.home / "results" / job_id).glob("*/status.json"):
        path.unlink()

    registry_finished = env.api.registry.lookup(next(r["key"] for r in env.api.registry.recent(50) if r["job"] == job_id))["finished"]
    view = jh.job_view(env.home, job_id)
    assert view["finish"] is None and view["derived_state"] == "merged:merged"
    base = views.summary(env.home, view)
    assert base["merge_state"] == "merged"
    assert base["end_state"] is None                                     # no source for the run's end: stays unknown, never made up
    assert base["finished"] == _iso(registry_finished)
    assert views.job_list(env.home)["jobs"][0]["finished"] == _iso(registry_finished)


def test_a_job_with_a_finish_event_still_shows_the_finish_event_time(tmp_path):
    env, job_id = _merged_job(tmp_path)
    finish_ts = next(json.loads(line)["ts"] for path in (env.home / "results" / job_id).glob("*/" + event_log.FILE)
                     for line in path.read_text(encoding="utf-8").splitlines() if json.loads(line).get("type") == "finish")
    base = views.summary(env.home, jh.job_view(env.home, job_id))
    assert base["end_state"] == "passed"
    assert base["finished"] == finish_ts
