"""I43 (2026-11-04): an API-queued job (queued with its frozen stage table) never runs on the current defaults. A missing, corrupt or
unmoved stage sidecar refuses the job; the drainers (fix_loop.drain_queue and monitor/loops.py) report it "refused" and run nothing.
A job queued without a table (the legacy path) is unchanged: it reads the roster at start, as before. No model call, no network."""
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
import fix_loop as fl  # noqa: E402
import stage_config as sc  # noqa: E402
from test_fix_loop_wiring import env_for, fake_run, json_loader, write_job  # noqa: E402


def table(profile="test"):
    """A stage table of the shape stage_config.snapshot writes (the fields the drainer checks)."""
    return {"schema_version": sc.SNAPSHOT_SCHEMA, "revision": 1, "profile": profile, "resolved": {"writer_a_write": {"effort": "low"}}}


def queued_api_job(tmp_path, env, name="alpha", stages=None):
    """Queues a job through the API path (with its table) and returns the queue file's path."""
    return Path(fl.enqueue(write_job(tmp_path, name), env.home, stages=table() if stages is None else stages))


def drain(env, calls, loader=json_loader):
    return fl.drain_queue(env.api, env.launcher, run=fake_run("passed", calls), loader=loader)


def test_an_api_queued_job_is_tagged_and_its_table_travels_with_it(tmp_path):
    env = env_for(tmp_path)
    queued = queued_api_job(tmp_path, env)
    assert queued.name.endswith("-alpha.json") and "-api-" in queued.name          # the job name still ends the file name
    assert fl.requires_snapshot(queued)
    taken = fl.take_next(env.home)
    assert taken is not None and fl.requires_snapshot(taken)
    assert fl.frozen_stage_table(taken) == table()


def test_a_job_queued_without_a_table_is_not_tagged_and_has_no_table_to_require(tmp_path):
    env = env_for(tmp_path)
    queued = Path(fl.enqueue(write_job(tmp_path, "legacy"), env.home))
    assert not fl.requires_snapshot(queued)
    assert fl.frozen_stage_table(fl.take_next(env.home)) is None


def test_a_legacy_job_without_a_table_still_drains_and_runs(tmp_path):
    env = env_for(tmp_path)
    queued = Path(fl.enqueue(write_job(tmp_path, "legacy"), env.home))
    calls = []
    out = drain(env, calls)
    assert out == [(queued.name, "passed")] and calls == [("legacy", True)]


def test_an_api_queued_job_with_its_table_runs_with_exactly_that_table(tmp_path):
    env = env_for(tmp_path)
    queued_api_job(tmp_path, env)
    seen = []

    def run(job, wt, **kwargs):
        seen.append(kwargs.get("stage_snapshot"))
        return {"name": job["name"], "state": "passed", "round": 1}

    out = fl.drain_queue(env.api, env.launcher, run=run, loader=json_loader)
    assert [state for _, state in out] == ["passed"]
    assert seen == [table()]


def test_a_missing_sidecar_refuses_the_api_queued_job_and_runs_nothing(tmp_path, capsys):
    env = env_for(tmp_path)
    queued = queued_api_job(tmp_path, env)
    fl.stages_file(queued).unlink()                                    # the table is gone before the job is taken
    calls = []
    out = drain(env, calls)
    assert out == [(queued.name, "refused")], out
    assert calls == [], "the job ran on the current defaults"
    assert "missing, corrupt or was not moved" in capsys.readouterr().out


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", "{}", '{"schema_version": 999, "resolved": {}, "profile": "test"}',
                                     '{"schema_version": %d, "profile": "test"}' % sc.SNAPSHOT_SCHEMA])
def test_a_corrupt_or_wrong_shaped_sidecar_refuses_the_job(tmp_path, content):
    env = env_for(tmp_path)
    queued = queued_api_job(tmp_path, env)
    fl.stages_file(queued).write_text(content, encoding="utf-8")
    calls = []
    out = drain(env, calls)
    assert out == [(queued.name, "refused")], (content, out)
    assert calls == []


def test_a_sidecar_that_fails_to_move_refuses_the_job_at_start(tmp_path, monkeypatch):
    """take_next moves the job, then its table. When the table's move fails (a Windows lock, say) the job is taken without its table, so the
    drainer refuses it; the table stays in the queue folder."""
    env = env_for(tmp_path)
    queued = queued_api_job(tmp_path, env)
    real_replace = os.replace

    def locked_sidecar(src, dst):
        if str(src).endswith(".stages"):
            raise PermissionError("sharing violation (the table is locked)")
        return real_replace(src, dst)

    monkeypatch.setattr(fl.os, "replace", locked_sidecar)
    calls = []
    out = drain(env, calls)
    assert out == [(queued.name, "refused")], out
    assert calls == [], "a job whose table did not move never runs"
    assert fl.stages_file(fl.queue_dir(env.home) / queued.name).is_file(), "the table is still in the queue folder, not with the job"


def test_the_monitor_loop_path_refuses_the_same_way(tmp_path):
    """monitor/loops.py starts a job through frozen_stage_table, the same helper the drainer uses: a missing table raises SnapshotRequired."""
    env = env_for(tmp_path)
    queued = queued_api_job(tmp_path, env)
    fl.stages_file(queued).unlink()
    taken = fl.take_next(env.home)
    with pytest.raises(fl.SnapshotRequired):
        fl.frozen_stage_table(taken)
