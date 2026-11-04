"""I47: the merge carries the job's manifest. After the rebase the merge re-checks the job's files against the reviewed hash and the locked
acceptance files against their hashes, and reruns the job's own acceptance tests. Any difference refuses the merge. Real git on temp repos;
the suite is a fake. No Docker, no model call."""
import sys
from pathlib import Path
from types import SimpleNamespace

import launch
import merge_queue
import p0_gates
import sandbox_runner
import fix_loop as fl
from wp3_repos import git, make_repos

OK_BUDGET = {"free_requests": 1000, "review_spend_usd": 0.0}
TEN_LINES = "".join(f"L{i}\n" for i in range(1, 11))


def land_on_main(origin, root, rel, text, msg):
    """Another writer lands a change on origin main (a clone of its own)."""
    work = Path(root) / f"writer-{len(list(Path(root).glob('writer-*')))}"
    git(root, "clone", "-q", str(origin), str(work))
    git(work, "config", "user.name", "wp3-test")
    git(work, "config", "user.email", "wp3-test@localhost")
    (work / rel).write_text(text, encoding="utf-8")
    git(work, "add", rel)
    git(work, "commit", "-q", "-m", msg)
    git(work, "push", "-q", "origin", "HEAD:main")


def make_env(tmp_path, suite_ok=True):
    origin, repo = make_repos(tmp_path)
    api = launch.real_api(tmp_path / "home")
    launcher = launch.Launcher(repo, api, budget=lambda: dict(OK_BUDGET))
    env = SimpleNamespace(root=tmp_path, origin=origin, repo=repo, api=api, launcher=launcher, suite_calls=[])

    def run_suite(wt, acceptance=None):
        env.suite_calls.append(list(acceptance or []))
        return suite_ok, "fake suite"

    env.queue = merge_queue.MergeQueue(repo, api, launcher, run_suite)
    return env


def job_with_edit(env, name, rel, text, acceptance=()):
    """Starts a job, commits `text` as its file `rel` and submits it with the manifest its review would have recorded."""
    job = env.launcher.start(name, [rel])
    assert job["ok"], job
    wt = Path(job["worktree"])
    (wt / rel).write_text(text, encoding="utf-8")
    git(wt, "add", rel)
    git(wt, "commit", "-q", "-m", f"{name} work")
    manifest = {"files": [rel], "reviewed_hash": p0_gates.combined_hash(wt, [rel]), "acceptance_tests": list(acceptance),
                "acceptance_hashes": p0_gates.hash_paths(wt, list(acceptance))}
    env.queue.submit(job["job_id"], job["gen"], manifest=manifest)
    return job


def test_a_rebase_that_changes_a_reviewed_file_refuses_the_merge(tmp_path):
    env = make_env(tmp_path)
    land_on_main(env.origin, env.root, "a.txt", TEN_LINES, "base a.txt")
    job = job_with_edit(env, "alpha", "a.txt", TEN_LINES.replace("L10\n", "L10-job\n"))
    land_on_main(env.origin, env.root, "a.txt", TEN_LINES.replace("L1\n", "L1-main\n"), "main edits the top")
    out = env.queue.drain()
    assert out == [(job["job_id"], "reviewed-bytes-changed")]     # the rebase applied cleanly, but the bytes are not the reviewed ones
    assert env.suite_calls == []                                  # nothing was tested, nothing was pushed
    assert git(env.origin, "show", "main:a.txt") == TEN_LINES.replace("L1\n", "L1-main\n").rstrip("\n")


def test_the_jobs_own_locked_acceptance_tests_run_again_after_the_rebase(tmp_path):
    env = make_env(tmp_path)
    job = env.launcher.start("alpha", ["feature.txt"])
    wt = Path(job["worktree"])
    (wt / "feature.txt").write_text("F\n", encoding="utf-8")
    (wt / "feature_locked.py").write_text("def test_locked():\n    assert True\n", encoding="utf-8")
    git(wt, "add", "feature.txt", "feature_locked.py")
    git(wt, "commit", "-q", "-m", "alpha work")
    manifest = {"files": ["feature.txt"], "reviewed_hash": p0_gates.combined_hash(wt, ["feature.txt"]),
                "acceptance_tests": ["feature_locked.py"], "acceptance_hashes": p0_gates.hash_paths(wt, ["feature_locked.py"])}
    env.queue.submit(job["job_id"], job["gen"], manifest=manifest)
    assert env.queue.drain() == [(job["job_id"], "merged")]
    assert env.suite_calls == [["feature_locked.py"]]           # the job's own locked tests were handed to the suite


def test_locked_acceptance_bytes_that_changed_after_review_refuse_the_merge(tmp_path):
    env = make_env(tmp_path)
    job = env.launcher.start("alpha", ["feature.txt"])
    wt = Path(job["worktree"])
    (wt / "feature.txt").write_text("F\n", encoding="utf-8")
    (wt / "locked.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    git(wt, "add", "feature.txt", "locked.py")
    git(wt, "commit", "-q", "-m", "work")
    manifest = {"files": ["feature.txt"], "reviewed_hash": p0_gates.combined_hash(wt, ["feature.txt"]),
                "acceptance_tests": ["locked.py"], "acceptance_hashes": {"locked.py": "0" * 64}}   # not the bytes that were locked
    env.queue.submit(job["job_id"], job["gen"], manifest=manifest)
    assert env.queue.drain() == [(job["job_id"], "reviewed-bytes-changed")]
    assert env.suite_calls == []


def test_the_default_suite_runs_the_manifest_acceptance_tests_first(tmp_path, monkeypatch):
    seen = []

    class Recorder:
        def __init__(self, include=None, **kwargs):
            pass

        def selftest(self):
            return []

        def run(self, root, targets, **kwargs):
            seen.append(list(targets))
            return SimpleNamespace(timed_out=False, returncode=0, output="")

    monkeypatch.setattr(sandbox_runner, "DockerSandbox", Recorder)
    (tmp_path / "pieces").mkdir()
    (tmp_path / "pieces" / "test_x_acceptance.py").write_text("", encoding="utf-8")
    ok, _ = merge_queue.default_suite(tmp_path, acceptance=["tests/test_locked.py"])
    assert ok
    assert seen[0] == ["tests/test_locked.py", "pieces/test_x_acceptance.py"]
    assert seen[1] == ["pieces"]


def test_the_run_hands_the_merge_the_reviewed_manifest(tmp_path):
    origin, repo = make_repos(tmp_path)
    api = launch.real_api(tmp_path / "home")
    launcher = launch.Launcher(repo, api, budget=lambda: dict(OK_BUDGET), wait_s=0)
    env = SimpleNamespace(repo=repo, api=api, launcher=launcher)

    class Merge:
        manifests = []

        def submit(self, job_id, gen, manifest=None):
            self.manifests.append(manifest)

    merge = Merge()
    job = {"name": "pilot", "files": ["base.txt"], "acceptance_tests": []}

    def run(job, wt, **kwargs):
        return {"name": job["name"], "state": "passed", "round": 1}

    res = fl.run_launched(job, env.repo, env.api, env.launcher, merge_queue=merge, run=run)
    assert res["state"] == "passed"
    manifest = merge.manifests[0]
    assert manifest["files"] == ["base.txt"] and manifest["reviewed_hash"] and manifest["acceptance_tests"] == []
