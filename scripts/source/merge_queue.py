"""WP3 merge queue (Revision 4): one job at a time into main, under home/merge.lock and the lease "merge".

Per job: fetch origin/main, rebase the job branch onto it (a conflict ends merge-conflict, the worktree stays for the owner), run the
sandbox suite (every acceptance test, then the full suite), and push only if origin/main is still the commit that was tested. If main
moved, test again from the new main. At most MAX_ATTEMPTS tries in all (the first try plus 2 retries), then main-moved.
"""
from __future__ import annotations

from pathlib import Path

import loop_home
from launch import git

MERGE_KEY = "merge"
MAX_ATTEMPTS = 3              # the first try plus 2 retries


def default_suite(wt):
    """Docker sandbox, no network: every acceptance test first, then the full suite. No usable sandbox means no merge."""
    from sandbox_runner import DockerSandbox
    sandbox = DockerSandbox(include=["pieces", "scripts"])
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
    def __init__(self, repo, api, launcher, run_suite=default_suite):
        self.repo = Path(repo)
        self.api = api
        self.launcher = launcher
        self.run_suite = run_suite
        # ponytail: in-memory FIFO. A crash drops pending submits; those jobs stay `running` in the registry and need a re-submit. Durable queue if that bites.
        self.pending = []

    def submit(self, job_id, gen):
        self.pending.append((job_id, gen))

    def drain(self):
        """Merge every pending job in submit order. Returns [(job_id, state)]. Holds home/merge.lock for the whole drain."""
        out = []
        with loop_home.merge_lock(self.api.home):
            merge_gen = self.api.lease.acquire(MERGE_KEY)
            try:
                while self.pending:
                    job_id, gen = self.pending.pop(0)
                    out.append((job_id, self._merge(job_id, gen, merge_gen)))
            finally:
                self.api.lease.release(MERGE_KEY, merge_gen)
        return out

    def _merge(self, job_id, gen, merge_gen):
        wt = self.api.home / "wt" / job_id
        for attempt in range(MAX_ATTEMPTS):
            self.api.lease.beat(MERGE_KEY, merge_gen)
            self.api.lease.beat(job_id, gen)
            base = self._fetch_main()
            if git(["rebase", base], wt, check=False).returncode != 0:
                git(["rebase", "--abort"], wt, check=False)
                return self.launcher.finish(job_id, gen, "merge-conflict", base)
            ok, text = self.run_suite(wt)
            if not ok:
                return self.launcher.finish(job_id, gen, "tests-failed", base, {"tail": text[-2000:]})
            if self._fetch_main() != base:                     # main moved while the suite ran: test again from the new main
                continue
            if git(["push", "origin", "HEAD:refs/heads/main"], wt, check=False).returncode != 0:
                if self._fetch_main() == base:                 # rejected with main unmoved: not a race, do not loop on it
                    return self.launcher.finish(job_id, gen, "push-failed", base)
                continue                                       # main moved during the push: test again
            head = git(["rev-parse", "HEAD"], wt).stdout.strip()
            self.launcher.finish(job_id, gen, "merged", base, {"head": head, "attempts": attempt + 1})
            git(["worktree", "remove", "--force", str(wt)], self.repo)
            return "merged"
        return self.launcher.finish(job_id, gen, "main-moved", base)

    def _fetch_main(self):
        git(["fetch", "-q", "origin", "main"], self.repo)
        return git(["rev-parse", "origin/main"], self.repo).stdout.strip()
