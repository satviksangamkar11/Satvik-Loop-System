"""P1b production runner (2026-11-02, Revision 3): runs one production unit through p1b_routing.walk(). Every model call goes through
openrouter_call by role (writer_a, writer_b, reviewer, repair). The sandbox runs the job's tests and the locked acceptance tests, and those
decide. This module writes and restores the job files and records the evidence. It names no legacy model family (a static test checks it).

Pre-pilot review (2026-11-02): a fresh writer never receives a previous attempt's pytest output (the output goes to the job history instead);
every result that is not PASS puts the originals back and keeps the authorized candidate for inspection; the job is marked `running` before its
first write, so a killed worker is restored by the next launch (fix_loop._prepare_job_folder).

Review package (pre-pilot patch, Step 1): each reviewer gets ONE request with nine sections in this order: SPEC (requirement lines numbered),
DIFF, FILES, TESTS, RESULTS (from the JUnit file of the last green run), STATIC WARNINGS (advisory only), CONTEXT, OMITTED, CHECKS. Sections 1 to 6
and 9 are mandatory: if they do not fit PACKAGE_LIMIT the job is blocked before any reviewer call. Only CONTEXT is cut to fit. Excluded paths
(_loop, _moss, secrets, .git, .env, keys, and anything that resolves outside the repo) are never read."""
import ast
import difflib
import json
import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import openrouter_call as oc
import p0_gates as gates
import p1b_routing as p1b
import sandbox_runner as sr

ROLE_BY_MODEL = {p1b.DOTS: "writer_a", p1b.XING: "writer_b", p1b.NORTH: "reviewer", p1b.GLM: "repair"}
PACKAGE_LIMIT = 100000              # characters: the whole review request (the owner's prompt limit for one reviewer call)
EXCLUDED_DIRS = set(gates.NEVER_INCLUDED) | set(sr.EXCLUDED_NAMES)
EXCLUDED_SUFFIXES = tuple(sr.EXCLUDED_SUFFIXES)
EXCLUDED_PREFIXES = (".env", "id_rsa", "id_ed25519")
CHECK_LINES = ("CHECK 1: every spec clause (SPEC n) has code and a test.",
               "CHECK 2: no code special-cases test values.",
               "CHECK 3: edge cases are handled.",
               "CHECK 4: nothing changed outside the spec.",
               "CHECK 5: no security or data-loss risk.")
VERDICT_LINE = ("Then the verdict line: `## Verdict: READY` or `## Verdict: FIX FIRST`. A FIX FIRST must name file:line of a file in this package "
                "and the SPEC n or CHECK n it breaks; without both it is not counted.")
TRIVIAL = (-1, 0, 1, 2)
CONTEXT_HEAD = "## 7. CONTEXT\n"
OMITTED_HEAD = "## 8. OMITTED (optional files left out for the size limit, and excluded references)\n"
WRITER_SYSTEM = ("You write the files of one job. Follow the spec exactly. Reply with FILE blocks only: for each file you were given, "
                 "`FILE: <path>` then the complete file in a fenced block, nothing else. Never write a reviewer verdict line.")
REVIEW_SYSTEM = ("REVIEW TASK. Review the files against the spec. End your reply with exactly one line: `## Verdict: READY` or "
                 "`## Verdict: FIX FIRST`. A FIX FIRST must name file:line (a file of this package) and the SPEC n or CHECK n it breaks; "
                 "without both it is not counted.")
REASK = ("Your FIX FIRST had no valid file:line and SPEC n or CHECK n, so it is not counted. Reply again with that evidence, or with READY.")


class AcceptanceTampered(Exception):
    """A locked acceptance file changed while the job ran: nothing the job produced is trusted."""


def client_for(root, job, keys=None):
    """The job's OpenRouter client: the keys (secrets/ or the environment, or `keys` when given), the job's budget exactly as the job states it,
    and the ledger under _loop/openrouter."""
    budget = job["budget"]
    return oc.OpenRouter(oc.load_keys() if keys is None else keys, Path(root) / "_loop" / "openrouter",
                         budget=oc.JobBudget(free_requests=budget["free_requests"], paid_usd=budget["paid_usd"]))


def restore_files(root, originals):
    """Puts every job file back to its original text; a file that did not exist before the job is removed."""
    for rel, text in (originals or {}).items():
        path = Path(root) / rel
        if text is None:
            if path.exists():
                path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")


def _snapshot(root, files, dest):
    """The candidate as it stood when the job stopped: the job's authorized files only, kept for inspection and never restored from."""
    for rel in files:
        src = Path(root) / rel
        if src.is_file():
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(src.read_bytes())


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _produced(root, files, originals):
    """True when a candidate exists: some job file on disk differs from its original. A stop before any write has no candidate."""
    for rel in files:
        path = Path(root) / rel
        now = path.read_text(encoding="utf-8") if path.is_file() else None
        if now != (originals or {}).get(rel):
            return True
    return False


# ---------------------------------------------------------------- review package (Step 1)

def readable(root, rel):
    """The path of a repo file, or None when it resolves outside the repo, into an excluded folder, or is an excluded file (.env, keys)."""
    base = Path(root).resolve()
    try:
        path = (base / rel).resolve()
        parts = path.relative_to(base).parts
    except (OSError, ValueError):
        return None
    if not path.is_file() or any(part in EXCLUDED_DIRS for part in parts):
        return None
    if path.name.startswith(EXCLUDED_PREFIXES) or path.name.endswith(EXCLUDED_SUFFIXES):
        return None
    return path


def _read(root, rel):
    path = readable(root, rel)
    return None if path is None else path.read_text(encoding="utf-8", errors="replace")


def numbered(text):
    return "\n".join(f"{n:4d} | {line}" for n, line in enumerate(text.splitlines(), 1))


def spec_requirements(spec_text):
    """The requirement lines of the spec: every non-blank line that is not a markdown heading (KNOWN: and ASSUMED: lines included)."""
    return [line.strip() for line in spec_text.splitlines() if line.strip() and not line.strip().startswith("#")]


def diff_block(rel, before, now):
    if before is None and now is None:
        return f"{rel}: absent before and after (no change)"
    if before == now:
        return f"{rel}: (no change)"
    head = f"{rel}: NEW FILE" if before is None else f"{rel}: DELETED" if now is None else f"{rel}: changed"
    body = difflib.unified_diff((before or "").splitlines(), (now or "").splitlines(), fromfile=f"original/{rel}", tofile=f"current/{rel}",
                                lineterm="")
    return head + "\n" + "\n".join(body)


def results_section(junit, expected):
    """One line per expected test id: passed, failed, skipped or missing, from the JUnit XML of the last green run. `expected` is
    gates.expected_test_ids(...): rel -> set of ids, or None when the file cannot be read."""
    try:
        details = gates._case_details(junit) if junit else []
    except ET.ParseError:
        details = []
    seen = {}
    for file, tid, outcome, _kind in details:
        seen[(file, gates._base(tid))] = "failed" if outcome == "error" else outcome
    lines = []
    for rel in sorted(expected):
        ids = expected[rel]
        if ids is None:
            lines.append(f"{rel}: unreadable, so its tests cannot be listed")
            continue
        lines.extend(f"{rel}::{tid}: {seen.get((rel, tid), 'missing')}" for tid in sorted(ids))
    return "\n".join(lines) or "(no expected tests)"


def _parse(text):
    try:
        return ast.parse(text)
    except (SyntaxError, ValueError):
        return ast.parse("")


def _literal(node):
    if isinstance(node, ast.Constant) and not isinstance(node.value, bool):
        value = node.value
        if isinstance(value, str) and len(value.strip()) >= 3:
            return value
        if isinstance(value, (int, float)) and value not in TRIVIAL:
            return value
    return None


def _compared(tree):
    """(value, line) for each non-trivial literal that a comparison (==, in, ...) or a regex match is made against."""
    out = []
    for node in ast.walk(tree):
        sides = []
        if isinstance(node, ast.Compare):
            sides = [node.left, *node.comparators]
        elif isinstance(node, ast.Call) and node.args:
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name in ("match", "search", "fullmatch"):
                sides = [node.args[0]]
        for side in sides:
            for item in (side.elts if isinstance(side, (ast.List, ast.Tuple, ast.Set)) else [side]):
                value = _literal(item)
                if value is not None:
                    out.append((value, node.lineno))
    return out


def static_warnings(test_texts, impl_texts):
    """Advisory only: a job's code compares against a literal that one of its tests compares against. Never decides a job on its own."""
    seen = {}
    for rel, text in test_texts.items():
        for value, line in _compared(_parse(text)):
            seen.setdefault(repr(value), f"{rel}:{line}")
    out = []
    for rel, text in impl_texts.items():
        for value, line in _compared(_parse(text)):
            key = repr(value)
            if key in seen:
                out.append(f"{rel}:{line} compares against {key}, which the test {seen[key]} also compares against")
    return out


def _module(rel):
    return rel[:-3].replace("/", ".") if rel.endswith(".py") else None


def _imported(text):
    """Module names a Python file imports, and module.name for each from-import name (so `from pieces import calc` names pieces.calc)."""
    out = set()
    for node in ast.walk(_parse(text)):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module)
            out.update(f"{node.module}.{alias.name}" for alias in node.names)
    return out


def _repo_python_files(root):
    base = Path(root).resolve()
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_DIRS)
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield Path(dirpath, name).relative_to(base).as_posix()


def _scrubbed(text, scrub_keys):
    for key in scrub_keys:
        text = text.replace(key, "[REDACTED]")
    return text


def _scrubbed_obj(value, scrub_keys):
    """The same scrub for a whole JSON-shaped value (keys and strings, at every depth): status.json and the returned result use it."""
    if isinstance(value, str):
        return _scrubbed(value, scrub_keys)
    if isinstance(value, dict):
        return {_scrubbed(str(k), scrub_keys): _scrubbed_obj(v, scrub_keys) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrubbed_obj(v, scrub_keys) for v in value]
    return value


def build_package(root, job, originals, junit, scrub_keys=(), limit=None):
    """(text, info): the nine-section review package for one review call. info = {"files": {path: lines} for every file shown,
    "spec_count": n, "kept": [context files], "omitted": [context files left out by name]}. Raises p1b.Blocked when the mandatory sections
    (1 to 6 and 9) do not fit in `limit` characters: no reviewer is called for it."""
    limit = PACKAGE_LIMIT if limit is None else limit
    root = Path(root)
    tests = list(job["tests"])
    locked = list(job.get("acceptance_tests") or []) + list(job.get("regression") or [])
    impl = [rel for rel in job["files"] if rel not in tests]
    job_files = set(job["files"]) | set(tests) | set(locked)
    shown = {}

    reqs = spec_requirements(_read(root, job["spec"]) or "")
    sections = {1: "## 1. SPEC\n" + ("\n".join(f"SPEC {n}: {line}" for n, line in enumerate(reqs, 1)) or "(no requirement lines)")}
    sections[2] = "## 2. DIFF\n" + "\n\n".join(diff_block(rel, originals.get(rel), _read(root, rel)) for rel in job["files"])

    blocks = []
    for rel in impl:
        now = _read(root, rel)
        if now is None:
            blocks.append(f"{rel}: (missing)")
            continue
        shown[rel] = len(now.splitlines())
        blocks.append(f"{rel}" + (" (unchanged)" if now == originals.get(rel) else "") + "\n" + numbered(now))
    sections[3] = "## 3. FILES\n" + ("\n\n".join(blocks) or "(none)")

    blocks = []
    for label, rels in (("[JOB TEST]", tests), ("[LOCKED ACCEPTANCE]", locked)):
        for rel in rels:
            now = _read(root, rel)
            if now is None:
                blocks.append(f"{rel} {label}: (missing)")
                continue
            shown[rel] = len(now.splitlines())
            blocks.append(f"{rel} {label}\n{numbered(now)}")
    sections[4] = "## 4. TESTS\n" + ("\n\n".join(blocks) or "(none)")

    targets = sorted(set(tests) | set(locked))
    sections[5] = "## 5. RESULTS (JUnit file of the last green run)\n" + results_section(junit, gates.expected_test_ids(root, targets))

    test_texts = {rel: _read(root, rel) for rel in targets}
    impl_texts = {rel: _read(root, rel) for rel in impl}
    warnings = static_warnings({rel: t for rel, t in test_texts.items() if t is not None},
                               {rel: t for rel, t in impl_texts.items() if t is not None})
    sections[6] = "## 6. STATIC WARNINGS (advisory: these never decide a job)\n" + ("\n".join(warnings) or "(none)")
    sections[9] = "## 9. CHECKS\n" + "\n".join(CHECK_LINES) + "\n" + VERDICT_LINE

    mandatory = [sections[k] for k in (1, 2, 3, 4, 5, 6, 9)]
    if len("\n\n".join(mandatory)) > limit:
        raise p1b.Blocked("review package exceeds the prompt limit")

    # CONTEXT: direct imports of the job's files and the job's reference files first, then callers (test callers included); smallest first
    # within each group. The rest is left out and named in OMITTED.
    imports, seen = {}, set(job_files)
    for rel in sorted(job_files):
        text = _read(root, rel)
        for mod in sorted(_imported(text) if text else set()):
            for cand in (mod.replace(".", "/") + ".py", mod.replace(".", "/") + "/__init__.py"):
                if cand not in seen and readable(root, cand) is not None:
                    imports[cand] = _read(root, cand)
                    seen.add(cand)
    references = {}
    excluded = []
    for rel in job.get("reference") or []:
        text = _read(root, rel)
        if text is None:
            excluded.append(rel)
        else:
            references[rel] = text
    job_modules = {_module(rel) for rel in job_files} - {None}
    callers = {}
    for rel in _repo_python_files(root):
        if rel in seen or rel in references:
            continue
        text = _read(root, rel)
        if text is not None and _imported(text) & job_modules:
            callers[rel] = text

    def ordered(group, role):
        return sorted(((rel, text, role) for rel, text in group.items()), key=lambda item: (len(item[1]), item[0]))

    candidates = ordered(imports, "import") + ordered(references, "reference") + ordered(callers, "caller")
    names = [rel for rel, _, _ in candidates] + excluded           # OMITTED can name at most these
    reserve = len(CONTEXT_HEAD) + len(OMITTED_HEAD) + len("\n".join(names) or "(none)") + len("\n\n".join(mandatory)) + 16
    budget = max(0, limit - reserve)
    kept, omitted, used = [], list(excluded), 0
    for rel, text, role in candidates:
        block = f"{rel} ({role})\n{numbered(text)}"
        if used + len(block) + 2 <= budget:
            kept.append((rel, block))
            used += len(block) + 2
            shown[rel] = len(text.splitlines())
        else:
            omitted.append(rel)
    sections[7] = CONTEXT_HEAD + ("\n\n".join(block for _, block in kept) or "(none)")
    sections[8] = OMITTED_HEAD + ("\n".join(omitted) or "(none)")

    text = "\n\n".join(sections[k] for k in range(1, 10))
    if len(text) > limit:                         # cannot happen while the reserve above holds; refuse rather than send an oversize request
        raise p1b.Blocked("review package exceeds the prompt limit")
    info = {"files": shown, "spec_count": len(reqs), "kept": [rel for rel, _ in kept], "omitted": omitted}
    return _scrubbed(text, scrub_keys), info


class Production:
    """The models object walk() drives: calls through openrouter_call, writes and restores the job files, runs the sandbox."""

    def __init__(self, job, root, client, sandbox, *, protected, acceptance_lock, originals, parse_files, check_path, content_problem,
                 history=None, scrub_keys=()):
        self.job, self.root, self.client, self.sandbox = job, Path(root), client, sandbox
        self.files, self.protected, self.acceptance_lock, self.originals = list(job["files"]), protected, acceptance_lock, originals
        self.parse_files, self.check_path, self.content_problem = parse_files, check_path, content_problem
        self.history, self.scrub_keys = history, tuple(scrub_keys)
        self.test_targets = sorted(set(job["tests"]) | set(job["acceptance_tests"]) | set(job.get("regression") or []))
        self.spec = (self.root / job["spec"]).read_text(encoding="utf-8")
        self.review_text = ""           # the production review prompt is built in: a job's review_prompt is never read (the legacy docs/REVIEW_PROMPT.md is never used)
        self.steps = []                 # every model call and test run, in order
        self.last_output = ""           # the output a writer may see next: cleared by restore(), so a fresh writer never gets an earlier run's output
        self.evidence_output = ""       # the output of the last test run, kept for the diagnostic
        self.test_runs = 0
        self.tested_hash = None         # hash of the job files at the last green run
        self.green_junit = None         # the JUnit XML of the last green run: the RESULTS section of the review package
        self.reviewed_hashes = []       # hash of the job files at each review call
        self.package = {"files": {}, "spec_count": 0}   # what the last review was given: read by p1b_routing.evidence_ok

    # ---------------------------------------------------------------- model calls

    def call(self, model, mode, files):
        if mode in ("write", "repair"):
            text = self._ask(model, WRITER_SYSTEM, self._writer_user(), kind="writer")
            reply = self._parse(text)
            self._step(model, mode, f"{len(reply)} file(s) returned")
            return reply
        user = self._review_package(reask=(mode == "review-reask"))
        text = self._ask(model, REVIEW_SYSTEM + "\n\n" + self.review_text, user, kind="reviewer", backup=model in oc.REVIEWER_BACKUP)
        self._check_locked()
        self.reviewed_hashes.append(gates.combined_hash(self.root, self.files))
        lines = text.strip().splitlines()
        self._step(model, mode, lines[-1] if lines else "(empty reply)")
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

    def _review_package(self, reask=False):
        """The one review request for this call: the nine-section package, with the re-ask note when this is the re-ask."""
        limit = PACKAGE_LIMIT - (len(REASK) + 2 if reask else 0)
        text, info = build_package(self.root, self.job, self.originals, self.green_junit, self.scrub_keys, limit=limit)
        self.package = info
        return text + ("\n\n" + REASK if reask else "")

    # ---------------------------------------------------------------- files and tests

    def write(self, reply):
        for rel, text in reply.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def restore(self):
        restore_files(self.root, self.originals)
        self.last_output = ""           # the next writer starts from the originals and the spec only: no output of the attempt just undone

    def tests(self, files):
        result = self.sandbox.run(self.root, self.test_targets, readonly=self.protected, timeout_s=600)
        self._check_locked()
        self.test_runs += 1
        self.evidence_output = result.output or ""
        if self.history is not None:                # the evidence of every run stays with the job, never in a writer's prompt
            (self.history / f"test_{self.test_runs:02d}.txt").write_text(_scrubbed(self.evidence_output, self.scrub_keys), encoding="utf-8")
        self.last_output = self.evidence_output
        expected = gates.expected_test_ids(self.root, self.test_targets)
        problems = gates.verify_junit(result.junit, expected, result.returncode, result.timed_out)
        failing = self._failing(result.junit, expected)
        green = not problems
        if green:
            self.tested_hash = gates.combined_hash(self.root, self.files)
            self.green_junit = result.junit
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
    """Runs the production unit. Returns the status dict (the same keys the legacy loop reports). Whatever the outcome, a job that did not
    PASS leaves the originals in the checkout, the authorized candidate under candidate/ (only when a candidate was written), and a status
    with its reason."""
    root, files = Path(root), list(job["files"])
    status_dir_job.mkdir(parents=True, exist_ok=True)
    history = status_dir_job / "history"
    history.mkdir(exist_ok=True)
    try:
        client.begin_job()
    except oc.OpenRouterError as exc:
        return _finish(job, root, status_dir_job, evidence, scrub_keys, None, p1b.Outcome("provider-unavailable", f"key state: {exc}"), None,
                       originals)
    (status_dir_job / "status.json").write_text(json.dumps({"name": job["name"], "state": "running", "updated": _now()}, indent=2), encoding="utf-8")
    models = Production(job, root, client, sandbox, protected=protected, acceptance_lock=acceptance_lock, originals=originals,
                        parse_files=parse_files, check_path=check_path, content_problem=content_problem, history=history, scrub_keys=scrub_keys)
    lineage = p1b.Lineage()
    try:
        client.set_job_deadline(job["deadline_min"] * 60)     # the unit deadline: no retry (openrouter_call) starts after it
        outcome = p1b.walk(job, models, lineage)
        lineage.save(status_dir_job / "lineage.json")
        if outcome.status == "passed":
            mismatch = _integrity(files, root, models, protected, acceptance_lock, evidence)
            if mismatch:
                outcome = mismatch
    except AcceptanceTampered as exc:
        outcome = p1b.Outcome("acceptance-tampered", str(exc))
    except Exception as exc:                        # any other failure: the job does not pass, its files are restored (see _finish)
        outcome = p1b.Outcome("exception", f"{type(exc).__name__}: {exc}")
    evidence["review_hashes"] = list(models.reviewed_hashes)
    evidence["openrouter_retries"] = list(client.retry_log)   # every retry of the job: model, attempt, wait, error (scrubbed in _finish)
    return _finish(job, root, status_dir_job, evidence, scrub_keys, models, outcome, models.evidence_output, originals)


def _integrity(files, root, models, protected, acceptance_lock, evidence):
    """None when the delivered files are the files that passed the locked tests and were reviewed, and the locked acceptance files are unchanged."""
    final = gates.combined_hash(root, files)
    lock_ok = gates.hash_paths(root, protected) == acceptance_lock
    evidence.update(tested_hash=models.tested_hash, final_hash=final, reviewed_hash=models.reviewed_hashes[-1] if models.reviewed_hashes else None)
    same = bool(models.tested_hash) and final == models.tested_hash and all(h == final for h in models.reviewed_hashes) and lock_ok
    if same:
        return None
    return p1b.Outcome("integrity-mismatch", "the files reviewed or delivered are not the files that passed the locked tests")


def _finish(job, root, status_dir_job, evidence, scrub_keys, models, outcome, last_output, originals):
    name = job["name"]
    state, verdict, tests_passed = outcome.status, None, False
    if outcome.status == "passed":
        state, verdict, tests_passed = "passed", "READY", True
    elif outcome.status == "review-fix":
        state, verdict, tests_passed = "review-open", "FIX FIRST", True
        (status_dir_job / "REVIEW_FIX.md").write_text(_scrubbed(f"# REVIEW FIX (the FIX FIRST repair loop is not built yet)\n\n{outcome.reason}\n", scrub_keys),
                                                     encoding="utf-8")
    elif outcome.status == "integrity-mismatch":
        state, verdict, tests_passed = "integrity-mismatch", "unavailable", True
    elif outcome.status in ("escalate", "blocked", "provider-unavailable", "deadline", "acceptance-tampered", "exception"):
        state = outcome.status
    if state != "passed":                           # nothing that did not pass stays in the checkout: keep the candidate if one was produced, then restore
        if _produced(root, job["files"], originals):
            _snapshot(root, job["files"], status_dir_job / "candidate")
        restore_files(root, originals)
    if state not in ("passed", "review-open"):
        diagnostic = "EXCEPTION.md" if state == "exception" else "ESCALATE.md"
        (status_dir_job / diagnostic).write_text(_scrubbed(
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
              "updated": _now()}
    status = _scrubbed_obj(status, scrub_keys)               # the reason can carry a provider's error text: scrub the whole status before it is saved
    (status_dir_job / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    print(f"{name}: {state}")
    return _scrubbed_obj({"name": name, "state": state, "round": len(steps), "tests_passed": tests_passed, "review_verdict": verdict, "rounds": steps,
                          "coder_calls": coder_calls, "reviewer_calls": reviewer_calls, "coder_budget": None, "evidence": evidence,
                          "updated": status["updated"]}, scrub_keys)
