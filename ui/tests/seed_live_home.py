#!/usr/bin/env python3
"""Seeds a temporary SATVIK_HOME (and SATVIK_REPO) for the UI's LIVE e2e run against the real FastAPI backend.

Usage (from anywhere):  python ui/tests/seed_live_home.py <seed-dir>

Creates, after wiping <seed-dir>:
  <seed-dir>/home/                      SATVIK_HOME
    results/<job_id>/                   one real archived job, copied from git (see ARCHIVE_REF below)
    registry.json                       one registry row: the archived job, state passed
    leases/<job_id>.json                an expired, released lease (pid of a process that has exited)
    queue/<n>-0-live-e2e-pending.json   one queued job file (plus its .stages sidecar is NOT written: the stage table is read as null)
  <seed-dir>/repo/                      SATVIK_REPO: the job files the API validates (job_path is relative to it)
    jobs/live-e2e-pending.json          the job already queued above
    jobs/live-e2e-twice.json            a job that is not queued yet (for the 409 duplicate test)
    jobs/*.spec.md                      the specs those job files name

No network and no model call: the backend only reads these files. The archive is read from the local git object store with
`git show <ref>:<path>`; the ref must already exist locally (`git fetch origin ah-run-2026-11-04`). Stdlib only.
"""
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_REF = "origin/ah-run-2026-11-04"
ARCHIVE_DIR = "docs/pilot/runs/2026-11-04-api-helpers/ah-budget-20261104-0344-1119"
JOB_ID = "ah-budget-20261104-0344-1119"
JOB_NAME = "ah-budget"
PENDING_NAME = "live-e2e-pending"
TWICE_NAME = "live-e2e-twice"


def git_bytes(*args):
    proc = subprocess.run(["git", "-C", str(REPO_ROOT), *args], capture_output=True)
    if proc.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {proc.stderr.decode('utf-8', 'replace').strip()}\n"
                         f"Fetch the evidence branch first: git fetch origin ah-run-2026-11-04")
    return proc.stdout


def iso_epoch(text):
    """Epoch seconds for an ISO-8601 UTC time such as 2026-11-03T22:14:08.606Z (fraction optional)."""
    fmt = "%Y-%m-%dT%H:%M:%S.%fZ" if "." in text else "%Y-%m-%dT%H:%M:%SZ"
    return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).timestamp()


def copy_archive(home):
    """Copies every file of the archived job folder from git into results/<job_id>/. Returns the copied events' timestamps."""
    listing = git_bytes("ls-tree", "-r", "--name-only", ARCHIVE_REF, "--", ARCHIVE_DIR).decode("utf-8").split()
    if not listing:
        raise SystemExit(f"{ARCHIVE_REF}:{ARCHIVE_DIR} has no files")
    dest_root = home / "results" / JOB_ID
    for path in listing:
        rel = path[len(ARCHIVE_DIR) + 1:]
        target = dest_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(git_bytes("show", f"{ARCHIVE_REF}:{path}"))
    events = [json.loads(line) for line in (dest_root / JOB_NAME / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    start = next(e for e in events if e["type"] == "job-start")
    merge = next(e for e in events if e["type"] == "merge")
    return start, merge, len(listing)


def write_registry(home, start, merge):
    """The registry row the engine writes at the end of a run: one row per request key, naming the job."""
    row = {
        "job": JOB_ID,
        "gen": 1,
        "state": "passed",
        "base": start["base"],
        "result": {"tests_green": True, "review_verdict": "READY", "archive_failed": False},
        "started": iso_epoch(start["ts"]),
        "finished": iso_epoch(merge["ts"]),
        "seq": 1,
    }
    (home / "registry.json").write_text(json.dumps({start["key"]: row}, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def dead_pid():
    """A pid of a process that has exited: the lease's holder is gone."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def write_lease(home, start, merge):
    """An expired, released lease: released, its heartbeat long past, its pid dead. The job is merged, not orphaned."""
    lease_dir = home / "leases"
    lease_dir.mkdir(parents=True, exist_ok=True)
    body = {"job_id": JOB_ID, "pid": dead_pid(), "started": iso_epoch(start["ts"]), "heartbeat": iso_epoch(merge["ts"]), "generation": 1,
            "released": True}
    (lease_dir / f"{JOB_ID}.json").write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_job_files(repo):
    """Valid legacy job files and their specs. The API validates job_path against the repo, so the files must exist there."""
    jobs = repo / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    for name in (PENDING_NAME, TWICE_NAME):
        spec = f"jobs/{name}.spec.md"
        (repo / spec).write_text(f"# {name}\n\nSeeded for the LIVE e2e run. Never started: no loop runs in this test.\n", encoding="utf-8")
        job = {"name": name, "spec": spec, "files": ["scripts/source/example.py"], "tests": ["tests/test_live_e2e_example.py"]}
        (jobs / f"{name}.json").write_text(json.dumps(job, indent=2) + "\n", encoding="utf-8")


def queue_pending(home, repo):
    """The one queued job file, named the way the API names a queued file (arrival time, pid, job file name)."""
    queue = home / "queue"
    queue.mkdir(parents=True, exist_ok=True)
    src = repo / "jobs" / f"{PENDING_NAME}.json"
    dest = queue / f"{1:020d}-0-{PENDING_NAME}.json"
    shutil.copyfile(src, dest)
    return dest


def main(argv):
    if len(argv) != 2:
        raise SystemExit(__doc__)
    seed = Path(argv[1]).resolve()
    if seed.exists():
        shutil.rmtree(seed)
    home = seed / "home"
    repo = seed / "repo"
    home.mkdir(parents=True)
    repo.mkdir(parents=True)

    start, merge, copied = copy_archive(home)
    write_registry(home, start, merge)
    write_lease(home, start, merge)
    write_job_files(repo)
    queued = queue_pending(home, repo)

    summary = {"seed": str(seed), "satvik_home": str(home), "satvik_repo": str(repo), "job_id": JOB_ID, "archive_files": copied,
               "queued_file": queued.name, "seeded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
