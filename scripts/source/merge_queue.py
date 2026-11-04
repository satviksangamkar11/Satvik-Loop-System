"""WP3 merge queue (Revision 4): one job at a time into main, under home/merge.lock and the lease "merge".

Per job: fetch origin/main, rebase the job branch onto it (a conflict ends merge-conflict, the worktree stays for the owner), run the
sandbox suite (every acceptance test, then the full suite), and push only if origin/main is still the commit that was tested. If main
moved, test again from the new main. At most MAX_ATTEMPTS tries in all (the first try plus 2 retries), then main-moved.
The queue itself is durable (I40): each submitted job is an entry file under home/merge/ before anything else happens.
"""
from __future__ import annotations

import time
from pathlib import Path

import event_log
import job_history
import loop_home
from launch import git, record_merge

MERGE_KEY = "merge"
INDEX_END = "<!-- change-index:end -->"
MAX_ATTEMPTS = 3              # the first try plus 2 retries
MERGE_DIR = "merge"           # home/merge/<job_id>.json: one durable entry per queued merge (I40)
MAX_ERRORS = 3                # git or I/O errors on one entry before it is finished as merge-error


def add_index_line(wt, job_id) -> bool:
    """Add `- docs/changes/<job_id>.md` above the change-index end marker in README.md. Returns True when README changed. Nothing is
    added when the marker is missing, the change doc is missing, or the line is already there (a retry after a rebase)."""
    readme, doc = Path(wt) / "README.md", Path(wt) / "docs" / "changes" / f"{job_id}.md"
    if not readme.is_file() or not doc.is_file():
        return False
    lines = readme.read_text(encoding="utf-8").splitlines(keepends=True)
    line = f"- docs/changes/{job_id}.md"
    if line in [x.rstrip("\r\n") for x in lines]:
        return False
    ends = [i for i, x in enumerate(lines) if x.strip() == INDEX_END]
    if not ends:
        return False
    lines.insert(ends[0], line + "\n")
    readme.write_text("".join(lines), encoding="utf-8")
    return True


# The folders the merge check copies into the sandbox: every folder the suite reads, by path (I39, 2026-11-04). monitor/ is imported by the API
# tests; ui/ holds the contract test file they read; tools/ and specs/ are read by the guard tests. A folder missing from this list fails the
# suite at merge time, so pieces/test_sandbox_image_deps.py checks the list against the tests' own paths.
SUITE_INCLUDE = ("pieces", "scripts", "config", "tests", "docs", "monitor", "ui", "tools", "specs")


def default_suite(wt):
    """Docker sandbox, no network: every acceptance test first, then the full suite. No usable sandbox means no merge. The copy holds
    every folder the suite reads (SUITE_INCLUDE): without config/ the roster cannot load (found in the 2-loop test, 2026-11-03)."""
    from sandbox_runner import DockerSandbox
    sandbox = DockerSandbox(include=list(SUITE_INCLUDE))
    problems = sandbox.selftest()
    if problems:
        return False, "sandbox unusable: " + "; ".join(problems)
    acceptance = sorted(p.relative_to(wt).as_posix() for p in (Path(wt) / "pieces").glob("test_*acceptance*.py"))
    for targets in (acceptance, ["pieces"]):
        if not targets:
            continue
        res = sandbox.run(Path(wt), targets, timeout_s=1800)
        if res.timed_out or res.returncode != 0:
            return False, res.output[-4000:]
    return True, "suite ok"


class MergeQueue:
    """Durable merge queue (I40). Each submitted job is a file home/merge/<job_id>.json written BEFORE anything else happens, so a
    restarted loop finds it. Entry states: pending (submitted, not tried), merging (a try is running; `head` is the commit that may be
    pushed), pushed (the push succeeded), done (the outcome is recorded). On a restart, an entry in merging or pushed is reconciled with
    origin/main first: when its head is already an ancestor of origin/main the job is recorded merged and never pushed again; otherwise it
    goes back to pending and is tried from the start. A git or I/O error never stops the drainer: the entry is kept for a retry, and after
    MAX_ERRORS it is finished as merge-error."""

    def __init__(self, repo, api, launcher, run_suite=default_suite):
        self.repo = Path(repo)
        self.api = api
        self.launcher = launcher
        self.run_suite = run_suite

    @property
    def folder(self) -> Path:
        return Path(self.api.home) / MERGE_DIR

    def _put(self, entry):
        loop_home._check_id(entry["job_id"])
        self.folder.mkdir(parents=True, exist_ok=True)
        loop_home._save(self.folder / f"{entry['job_id']}.json", entry)

    def _entries(self):
        """Every entry that is not done, oldest submit first."""
        if not self.folder.is_dir():
            return []
        rows = [loop_home._load(p, None) for p in self.folder.glob("*.json")]
        return sorted((r for r in rows if r and r.get("state") != "done"), key=lambda r: (r.get("seq", 0), r["job_id"]))

    @property
    def pending(self) -> list:
        """The entries still to merge, read from disk: a restarted queue sees the ones a crash left behind."""
        return self._entries()

    def submit(self, job_id, gen):
        """Persist the entry (state pending) before anything else: a crash after this point cannot lose the merge."""
        self._put({"job_id": job_id, "gen": gen, "state": "pending", "seq": time.time_ns(), "head": None, "errors": 0})

    def drain(self):
        """Merge every pending entry in submit order. Returns [(job_id, state)]. Holds home/merge.lock for the whole drain. An error on one
        entry is recorded and the drain goes on with the next one."""
        out = []
        with loop_home.merge_lock(self.api.home):
            merge_gen = self.api.lease.acquire(MERGE_KEY)
            try:
                for entry in self._entries():
                    out.append((entry["job_id"], self._run_entry(entry, merge_gen)))
            finally:
                self.api.lease.release(MERGE_KEY, merge_gen)
        return out

    def _run_entry(self, entry, merge_gen):
        job_id, gen = entry["job_id"], entry["gen"]
        try:
            if entry["state"] in ("merging", "pushed") and entry.get("head"):
                self._fetch_main()
                if self._delivered(entry["head"]):             # the push happened before the crash: record it, never push again
                    return self._finish(job_id, gen, "merged", entry.get("base"),
                                        {"head": entry["head"], "attempts": entry.get("attempts", 1)}, entry)
            entry["state"], entry["head"] = "pending", None     # not on main: tried again from the start
            self._put(entry)
            return self._merge(entry, merge_gen)
        except Exception as exc:                               # git or I/O: the drainer goes on; the entry is kept for a retry
            return self._failed(entry, exc)

    def _delivered(self, head) -> bool:
        return git(["merge-base", "--is-ancestor", head, "origin/main"], self.repo, check=False).returncode == 0

    def _failed(self, entry, exc):
        job_id, gen = entry["job_id"], entry["gen"]
        entry["errors"] = int(entry.get("errors", 0)) + 1
        entry["last_error"] = f"{type(exc).__name__}: {exc}"[:500]
        entry["state"], entry["head"] = "pending", None
        self._put(entry)
        wt = self.api.home / "wt" / job_id
        if wt.is_dir():
            git(["rebase", "--abort"], wt, check=False)        # a rebase that was in progress must not block the retry
        if entry["errors"] < MAX_ERRORS:
            return "retry"
        return self._finish(job_id, gen, "merge-error", entry.get("base"), {"tail": entry["last_error"], "attempts": entry.get("attempts", 0)},
                            entry)

    def _merge(self, entry, merge_gen):
        job_id, gen = entry["job_id"], entry["gen"]
        wt = self.api.home / "wt" / job_id
        for attempt in range(MAX_ATTEMPTS):
            if attempt:                                        # main moved after the last try: this is a rebase-and-retest
                self.api.registry.bump("merge-retry")
            self.api.lease.beat(MERGE_KEY, merge_gen)
            self.api.lease.beat(job_id, gen)
            base = self._fetch_main()
            entry.update(state="merging", base=base, head=None, attempts=attempt + 1)
            self._put(entry)
            if git(["rebase", base], wt, check=False).returncode != 0:
                git(["rebase", "--abort"], wt, check=False)
                return self._finish(job_id, gen, "merge-conflict", base, None, entry)
            if add_index_line(wt, job_id):                     # the index line goes in during the merge, before the suite
                git(["add", "README.md"], wt)
                git(["commit", "-q", "-m", f"index: {job_id}"], wt)
            ok, text = self.run_suite(wt)
            if not ok:
                return self._finish(job_id, gen, "tests-failed", base, {"tail": text[-2000:]}, entry)
            if self._fetch_main() != base:                     # main moved while the suite ran: test again from the new main
                continue
            head = git(["rev-parse", "HEAD"], wt).stdout.strip()
            entry.update(state="merging", head=head)           # recorded before the push: a crash now is reconciled against main
            self._put(entry)
            if git(["push", "origin", "HEAD:refs/heads/main"], wt, check=False).returncode != 0:
                if self._fetch_main() == base:                 # rejected with main unmoved: not a race, do not loop on it
                    return self._finish(job_id, gen, "push-failed", base, None, entry)
                continue                                       # main moved during the push: test again
            entry.update(state="pushed", head=head)
            self._put(entry)
            return self._finish(job_id, gen, "merged", base, {"head": head, "attempts": attempt + 1}, entry)   # finish removes the worktree
        return self._finish(job_id, gen, "main-moved", base, None, entry)

    def _finish(self, job_id, gen, state, base, result=None, entry=None):
        """The launcher's finish, then the merge outcome as results/<job_id>/merge.json (launch.record_merge). The execution evidence
        archived at the job's own finish is never overwritten. The merge event goes in first (into the archived events once there are
        any), and the job is indexed into history.db last. The entry is marked done only after all of that."""
        extra = {k: (result or {})[k] for k in ("head", "attempts") if k in (result or {})}
        event_log.emit(event_log.job_dir_for(self.api.home, job_id), "merge", job_id=job_id, state=state, base=base, **extra)
        out = self.launcher.finish(job_id, gen, state, base, result)
        record_merge(self.api.home, job_id, state, base, result)
        job_history.safe_index(self.api.home, job_id)
        if entry is not None:
            entry["state"] = "done"
            self._put(entry)
        return out

    def _fetch_main(self):
        git(["fetch", "-q", "origin", "main"], self.repo)
        return git(["rev-parse", "origin/main"], self.repo).stdout.strip()
