"""P1b production runner (2026-11-02, Revision 3): runs one production unit through p1b_routing.walk(). Every model call goes through
openrouter_call by role (writer_a, writer_b, reviewer, repair). The sandbox runs the job's tests and the locked acceptance tests, and those
decide. This module writes and restores the job files and records the evidence. It names no legacy model family (a static test checks it)."""
import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import openrouter_call as oc
import p0_gates as gates
import p1b_routing as p1b

ROLE_BY_MODEL = {p1b.DOTS: "writer_a", p1b.XING: "writer_b", p1b.NORTH: "reviewer", p1b.GLM: "repair"}
WRITER_SYSTEM = ("You write the files of one job. Follow the spec exactly. Reply with FILE blocks only: for each file you were given, "
                 "`FILE: <path>` then the complete file in a fenced block, nothing else. Never write a reviewer verdict line.")
REVIEW_SYSTEM = ("REVIEW TASK. Review the files against the spec. End your reply with exactly one line: `## Verdict: READY` or "
                 "`## Verdict: FIX FIRST`. A FIX FIRST must name file:line and the SPEC clause it breaks; without both it is not counted.")
REASK = "Your FIX FIRST had no file:line and no SPEC clause, so it is not counted. Reply again with that evidence, or with READY."


class AcceptanceTampered(Exception):
    """A locked acceptance file changed while the job ran: nothing the job produced is trusted."""


def client_for(root):
    """The job's OpenRouter client: the keys from secrets/ or the environment, the ledger under _loop/openrouter."""
    return oc.OpenRouter(oc.load_keys(), Path(root) / "_loop" / "openrouter")


class Production:
    """The models object walk() drives: calls through openrouter_call, writes and restores the job files, runs the sandbox."""

    def __init__(self, job, root, client, sandbox, *, protected, acceptance_lock, originals, parse_files, check_path, content_problem):
        self.job, self.root, self.client, self.sandbox = job, Path(root), client, sandbox
        self.files, self.protected, self.acceptance_lock, self.originals = list(job["files"]), protected, acceptance_lock, originals
        self.parse_files, self.check_path, self.content_problem = parse_files, check_path, content_problem
        self.test_targets = sorted(set(job["tests"]) | set(job["acceptance_tests"]) | set(job.get("regression") or []))
        self.spec = (self.root / job["spec"]).read_text(encoding="utf-8")
        review_prompt = job.get("review_prompt")
        self.review_text = (self.root / review_prompt).read_text(encoding="utf-8") if review_prompt and (self.root / review_prompt).is_file() else ""
        self.steps = []                 # every model call and test run, in order
        self.last_output = ""
        self.tested_hash = None         # hash of the job files at the last green run
        self.reviewed_hashes = []       # hash of the job files at each review call

    # ---------------------------------------------------------------- model calls

    def call(self, model, mode, files):
        if mode in ("write", "repair"):
            text = self._ask(model, WRITER_SYSTEM, self._writer_user(), kind="writer")
            reply = self._parse(text)
            self._step(model, mode, f"{len(reply)} file(s) returned")
            return reply
        user = self._review_user() + (("\n" + REASK) if mode == "review-reask" else "")
        text = self._ask(model, REVIEW_SYSTEM + "\n\n" + self.review_text, user, kind="reviewer", backup=model in oc.REVIEWER_BACKUP)
        self._check_locked()
        self.reviewed_hashes.append(gates.combined_hash(self.root, self.files))
        self._step(model, mode, text.strip().splitlines()[-1])
        return text

    def _ask(self, model, system, user, kind, backup=False):
        try:
            text, _info = self.client.call(model if backup else ROLE_BY_MODEL[model], system, user, kind=kind, allow_disabled_backup=backup)
        except oc.OpenRouterError as exc:          # refused, dropped, quota, budget, account: this model is down for this job
            raise p1b.ModelFailed(str(exc)) from exc
        return text

    def _parse(self, text):
        ok, refused = self.parse_files(text)
        refused_job = sorted({r[0] if isinstance(r, (list, tuple)) else r for r in refused} & set(self.files))
        if refused_job:
            raise p1b.BadReply(f"refused block for job file(s): {refused_job}")
        for rel, content in ok.items():
            if rel in self.files:
                problem = self.check_path(rel) or self.content_problem(rel, content)
                if problem:
                    raise p1b.BadReply(f"{rel}: {problem}")
        return ok

    def _writer_user(self):
        parts = [f"SPEC:\n{self.spec}", "EXPECTED FILES:"]
        for rel in self.files:
            path = self.root / rel
            parts.append(f"{rel}:\n{path.read_text(encoding='utf-8')}" if path.is_file() else f"{rel}: (new file)")
        if self.last_output:
            parts.append("PYTEST OUTPUT (last 9000 chars):\n" + self.last_output[-9000:])
            parts.append("Fix exactly the problems shown above. Change nothing else.")
        return "\n\n".join(parts)

    def _review_user(self):
        parts = [f"SPEC:\n{self.spec}"]
        for rel in self.files:
            path = self.root / rel
            parts.append(f"{rel}:\n{path.read_text(encoding='utf-8')}" if path.is_file() else f"{rel}: (missing)")
        return "\n\n".join(parts)

    # ---------------------------------------------------------------- files and tests

    def write(self, reply):
        for rel, text in reply.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def restore(self):
        for rel, text in self.originals.items():
            path = self.root / rel
            if text is None:
                if path.exists():
                    path.unlink()
            else:
                path.write_text(text, encoding="utf-8")

    def tests(self, files):
        result = self.sandbox.run(self.root, self.test_targets, readonly=self.protected, timeout_s=600)
        self._check_locked()
        expected = gates.expected_test_ids(self.root, self.test_targets)
        problems = gates.verify_junit(result.junit, expected, result.returncode, result.timed_out)
        failing = self._failing(result.junit, expected)
        self.last_output = result.output
        green = not problems
        if green:
            self.tested_hash = gates.combined_hash(self.root, self.files)
        self._step("sandbox", "tests", "green" if green else f"red: {len(failing)} failing")
        systemic = result.timed_out or result.junit is None or (not green and not failing)
        return p1b.Result(green, frozenset(failing), frozenset(t.split("::")[0] for t in failing), systemic)

    def _check_locked(self):
        if gates.hash_paths(self.root, self.protected) != self.acceptance_lock:
            raise AcceptanceTampered("locked acceptance files changed during the job")

    def _failing(self, xml, expected):
        """The failing test ids of this run as 'file::test', plus every expected test that was not collected at all."""
        try:
            details = gates._case_details(xml) if xml else []
        except ET.ParseError:
            details = []
        out = {f"{file}::{tid}" for file, tid, outcome, _kind in details if outcome != "passed"}
        for rel, ids in expected.items():
            if ids is None:
                out.add(rel)
                continue
            seen = {gates._base(tid) for file, tid, _outcome, _kind in details if file == rel}
            out |= {f"{rel}::{tid}" for tid in ids - seen}
        return out

    def paid_fits(self):
        b = self.client.budget
        return b.paid_rounds < oc.PAID_ROUNDS and b.paid_attempts < oc.PAID_ATTEMPTS and b.paid_spent + b.paid_reserved < b.paid_usd

    def _step(self, model, mode, note):
        self.steps.append({"model": model, "mode": mode, "note": note})


def run(job, root, status_dir_job, *, sandbox, protected, acceptance_lock, originals, evidence, client, parse_files, check_path,
        content_problem, scrub_keys=()):
    """Runs the production unit. Returns the status dict (the same keys the legacy loop reports)."""
    root, files = Path(root), list(job["files"])
    status_dir_job.mkdir(parents=True, exist_ok=True)
    try:
        client.begin_job()
    except oc.OpenRouterError as exc:
        return _finish(job, root, status_dir_job, evidence, scrub_keys, None, p1b.Outcome("provider-unavailable", f"key state: {exc}"), None)
    models = Production(job, root, client, sandbox, protected=protected, acceptance_lock=acceptance_lock, originals=originals,
                        parse_files=parse_files, check_path=check_path, content_problem=content_problem)
    lineage = p1b.Lineage()
    try:
        outcome = p1b.walk(job, models, lineage)
    except AcceptanceTampered as exc:
        outcome = p1b.Outcome("acceptance-tampered", str(exc))
    lineage.save(status_dir_job / "lineage.json")
    evidence["review_hashes"] = list(models.reviewed_hashes)
    if outcome.status == "passed":
        final = gates.combined_hash(root, files)
        lock_ok = gates.hash_paths(root, protected) == acceptance_lock
        evidence.update(tested_hash=models.tested_hash, final_hash=final, reviewed_hash=models.reviewed_hashes[-1] if models.reviewed_hashes else None)
        same = bool(models.tested_hash) and final == models.tested_hash and all(h == final for h in models.reviewed_hashes) and lock_ok
        if not same:
            outcome = p1b.Outcome("integrity-mismatch", "the files reviewed or delivered are not the files that passed the locked tests")
    return _finish(job, root, status_dir_job, evidence, scrub_keys, models, outcome, models.last_output)


def _finish(job, root, status_dir_job, evidence, scrub_keys, models, outcome, last_output):
    name = job["name"]
    state, verdict, tests_passed = outcome.status, None, False
    if outcome.status == "passed":
        state, verdict, tests_passed = "passed", "READY", True
    elif outcome.status == "review-fix":
        state, verdict, tests_passed = "review-open", "FIX FIRST", True
        (status_dir_job / "REVIEW_FIX.md").write_text(_scrubbed(f"# REVIEW FIX (the FIX FIRST repair loop is not built yet)\n\n{outcome.reason}\n", scrub_keys),
                                                     encoding="utf-8")
    elif outcome.status == "acceptance-tampered":
        state = "acceptance-tampered"
    elif outcome.status == "integrity-mismatch":
        state, verdict = "integrity-mismatch", "unavailable"
        tests_passed = True
    if state in ("escalate", "blocked", "provider-unavailable", "deadline", "acceptance-tampered"):
        if models:
            models.restore()                      # nothing that did not pass is kept: the originals come back
        (status_dir_job / "ESCALATE.md").write_text(_scrubbed(
            f"# Escalation Report\n\nJob: {name}\n\nState: {state}\n\nReason: {outcome.reason}\n\nLast test output (last 9000 chars):\n```\n"
            f"{(last_output or '')[-9000:]}\n```\n", scrub_keys), encoding="utf-8")
    steps = models.steps if models else []
    reviewer_calls, coder_calls = {}, 0
    for step in steps:
        if step["mode"] in ("write", "repair"):
            coder_calls += 1
        elif step["mode"] in ("review", "review-reask"):
            reviewer_calls[step["model"]] = reviewer_calls.get(step["model"], 0) + 1
    status = {"name": name, "state": state, "profile": "production", "tests_passed": tests_passed, "review_verdict": verdict,
              "reason": outcome.reason, "steps": steps, "coder_calls": coder_calls, "reviewer_calls": reviewer_calls, "evidence": evidence,
              "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (status_dir_job / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    print(f"{name}: {state}")
    return {"name": name, "state": state, "round": len(steps), "tests_passed": tests_passed, "review_verdict": verdict, "rounds": steps,
            "coder_calls": coder_calls, "reviewer_calls": reviewer_calls, "coder_budget": None, "evidence": evidence, "updated": status["updated"]}


def _scrubbed(text, scrub_keys):
    for key in scrub_keys:
        text = text.replace(key, "[REDACTED]")
    return text
