"""P0 acceptance gates (2026-11-02). Small, pure, deterministic checks used by fix_loop.run_job. No model calls, no network.

Every check FAILS CLOSED: an unreadable input, a parse error or an unexpected shape is a problem, never a pass."""
from __future__ import annotations

import ast
import hashlib
import sys
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

VALID_VERDICTS = ("READY", "FIX FIRST")

# files a coder may never write in a production job: they change how pytest or Python start, or which module an import finds
TEST_CONFIG_NAMES = {"conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini", ".pytest.ini"}
STARTUP_NAMES = {"sitecustomize", "usercustomize", "pytest", "_pytest", "pluggy", "py", "conftest"}
NEVER_INCLUDED = {"_moss", "_loop", "secrets", ".git"}


# ---------------------------------------------------------------- review acceptance

def decide_review(verdicts: dict, required: list, *, judge=None, coder_fix_first=False, test_findings=False, min_valid=0, production=False) -> str:
    """One review pass -> "accept" | "fix" | "test-findings" | "blocked".

    verdicts: reviewer name -> its verdict ("READY", "FIX FIRST", anything else = no valid review); a required name absent from it is missing.
    coder_fix_first: some FIX FIRST report has findings the coder can repair. test_findings: some FIX FIRST names a frozen test file.
    judge: the tie-breaker's verdict on a mixed panel, or None.

    Rules: a judge never stands in for a missing or invalid review. Production: acceptance needs at least two distinct required reviewers, every one
    of them READY, and the judge cannot accept anything. Legacy: an explicit lower quorum (min_valid) is allowed, and the judge may settle a
    READY-vs-FIX FIRST disagreement only when that quorum is met."""
    required = list(dict.fromkeys(required))
    valid = {n: verdicts.get(n) for n in required if verdicts.get(n) in VALID_VERDICTS}
    missing = [n for n in required if n not in valid]
    ready = [n for n, v in valid.items() if v == "READY"]
    any_fix_first_verdict = any(v == "FIX FIRST" for v in valid.values())

    if production:
        quorum_met = len(required) >= 2 and not missing
    else:
        quorum = min_valid or len(required)
        quorum_met = bool(required) and len(valid) >= quorum

    if judge == "FIX FIRST":
        return "fix"
    if coder_fix_first:
        if not production and not test_findings and judge == "READY" and quorum_met and ready:
            return "accept"                    # legacy owner rule: a complete panel's disagreement settled by the judge
        return "fix"                           # coder findings are repaired first; a test finding stays recorded and still blocks later
    if test_findings:
        return "test-findings"                 # a frozen-test finding blocks; no judge clears it
    if any_fix_first_verdict:                  # a FIX FIRST whose findings were neither coder- nor test-findings still is not a READY
        return "blocked"
    if quorum_met and len(ready) == len(valid) and ready:
        if production and len(ready) < 2:
            return "blocked"
        return "accept"
    return "blocked"


# ---------------------------------------------------------------- hashing

def hash_paths(root: Path, rels) -> dict:
    """rel -> sha256 of the bytes, or None when the file does not exist."""
    out = {}
    for rel in sorted(set(rels)):
        p = Path(root) / rel
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
    return out


def combined_hash(root: Path, rels) -> str:
    h = hashlib.sha256()
    for rel, digest in hash_paths(root, rels).items():
        h.update(rel.encode("utf-8") + b"\0" + (digest or "absent").encode("ascii") + b"\n")
    return h.hexdigest()


def protected_paths(root: Path, acceptance_tests, control_files) -> list:
    """Everything that decides what the acceptance run means: the acceptance tests, every conftest.py from their folder up to the root, the root
    pytest configuration files, and the negative-control files. Listed whether or not they exist (a file that appears later is a change too)."""
    out = set(acceptance_tests) | set(control_files)
    for rel in acceptance_tests:
        parts = PurePosixPath(rel).parent.parts
        for i in range(len(parts) + 1):
            out.add(str(PurePosixPath(*parts[:i], "conftest.py")) if i else "conftest.py")
    out |= {n for n in TEST_CONFIG_NAMES if n != "conftest.py"}
    return sorted(out)


# ---------------------------------------------------------------- production job validation

def production_job_problems(job: dict) -> list:
    """Problems that make a job unsafe to run as a production job. load_job raises on any of them."""
    p = []
    reviewers = job.get("reviewers") or []
    if not job.get("review", True):
        p.append("production jobs cannot set review: false (no-review mode is legacy/tests only)")
    if len(set(reviewers)) < 2:
        p.append("production jobs need at least two distinct reviewers")
    mv = job.get("min_valid_reviews", 0)
    if mv not in (0, len(reviewers)):
        p.append("production jobs cannot lower the review quorum (min_valid_reviews must be 0 or the number of reviewers)")
    acc = job.get("acceptance_tests") or []
    if not acc:
        p.append("production jobs need acceptance_tests (independent, controller-owned tests)")
    controls = job.get("negative_controls") or []
    baseline = job.get("acceptance_baseline_fail") or []
    if not controls and not baseline:
        p.append("production jobs need a negative control or acceptance_baseline_fail: the acceptance tests must be shown to fail on wrong code")
    files = job.get("files") or []
    control_files = [src for c in controls for src in (c.get("replace") or {}).values()]
    for f in files:
        name = PurePosixPath(f).name
        if f in acc or f in control_files or f in (job.get("regression") or []):
            p.append(f"{f}: acceptance tests, regression tests and negative controls cannot be coder-editable files")
        if name in TEST_CONFIG_NAMES:
            p.append(f"{f}: a coder may not write test configuration ({name}: conftest/pytest config is controller-owned)")
        stem = PurePosixPath(f).stem
        if f.endswith(".pth") or (f.endswith(".py") and (stem in STARTUP_NAMES or stem in sys.stdlib_module_names)):
            p.append(f"{f}: file name shadows a standard-library or start-up module ({stem})")
    acc_set = set(acc)
    for c in controls:
        if not c.get("replace") or not c.get("must_fail"):
            p.append(f"negative control {c.get('name')!r} needs 'replace' and 'must_fail'")
        for tid in c.get("must_fail") or []:
            if tid.split("::")[0] not in acc_set:
                p.append(f"negative control {c.get('name')!r}: {tid} is not an acceptance test")
        for target in (c.get("replace") or {}):
            if target not in files:
                p.append(f"negative control {c.get('name')!r}: replaces {target}, which is not a job file")
    for tid in baseline:
        if tid.split("::")[0] not in acc_set:
            p.append(f"acceptance_baseline_fail: {tid} is not an acceptance test")
    for inc in job.get("sandbox_include") or []:
        if set(PurePosixPath(inc).parts) & NEVER_INCLUDED:
            p.append(f"sandbox_include: {inc} names a protected folder ({', '.join(sorted(NEVER_INCLUDED))})")
    return p


# ---------------------------------------------------------------- expected test ids and JUnit verdicts

def expected_test_ids(root: Path, rel_files) -> dict:
    """rel -> set of test ids found statically: 'test_x' or 'TestC::test_m'. A file that is missing or does not parse maps to None (fail closed)."""
    out = {}
    for rel in rel_files:
        p = Path(root) / rel
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError):
            out[rel] = None
            continue
        ids = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                ids.add(node.name)
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name.startswith("test"):
                        ids.add(node.name + "::" + sub.name)
        out[rel] = ids
    return out


def _failure_kind(message: str) -> str:
    """What a failure is, from the JUnit message (xunit1 writes no type attribute): 'AssertionError' for an assert statement, else the
    exception's name, which pytest puts first, e.g. 'Failed' for pytest.fail and pytest.raises' DID NOT RAISE."""
    if message.startswith("assert "):
        return "AssertionError"
    return message.split(":", 1)[0].strip() or "unknown"


def _case_details(xml_text):
    """Parse JUnit XML (xunit1 family, which carries `file`) into [(file, test_id, outcome, kind)]; kind is set for failures and errors.
    Raises on anything unreadable."""
    root = ET.fromstring(xml_text)
    out = []
    for case in root.iter("testcase"):
        name = case.get("name") or ""
        classname = case.get("classname") or ""
        file = (case.get("file") or "").replace("\\", "/")
        cls = ""
        if file:
            module = file[:-3].replace("/", ".") if file.endswith(".py") else file.replace("/", ".")
            if classname.startswith(module + "."):
                cls = classname[len(module) + 1:]
        test_id = (cls.replace(".", "::") + "::" + name) if cls else name
        outcome, kind = "passed", ""
        for child in case:
            if child.tag in ("failure", "error"):
                outcome = "failed" if child.tag == "failure" else "error"
                kind = _failure_kind(child.get("message") or "")
                break
            if child.tag == "skipped":
                outcome = "skipped"
        out.append((file, test_id, outcome, kind))
    return out


def _cases(xml_text):
    """[(file, test_id, outcome)] for every collected test. Raises on anything unreadable."""
    return [(file, test_id, outcome) for file, test_id, outcome, _kind in _case_details(xml_text)]


def _base(test_id):
    return test_id.split("[", 1)[0]


def verify_junit(xml_text, expected: dict, returncode: int, timed_out: bool) -> list:
    """Problems with a run that must PASS. Empty list = every expected test was collected and passed, nothing was skipped, xfailed or errored."""
    problems = []
    if timed_out:
        problems.append("pytest timed out")
    if returncode != 0:
        problems.append(f"pytest exit code {returncode}")
    if not xml_text:
        return problems + ["no JUnit XML result"]
    try:
        cases = _cases(xml_text)
    except ET.ParseError as exc:
        return problems + [f"JUnit XML unreadable: {exc}"]
    if not cases:
        problems.append("no test was collected")
    for file, tid, outcome in cases:
        if outcome != "passed":
            problems.append(f"{file or '?'}::{tid}: {outcome}")
    for rel, ids in expected.items():
        if ids is None:
            problems.append(f"{rel}: unreadable or does not parse")
            continue
        if not ids:
            problems.append(f"{rel}: defines no test")
        seen = {_base(tid) for file, tid, _ in cases if file == rel}
        for tid in sorted(ids - seen):
            problems.append(f"{rel}::{tid}: missing (not collected or deselected)")
    return problems


ASSERTION_KINDS = ("AssertionError", "Failed")   # a negative control may fail only by an assert, pytest.fail or pytest.raises' DID NOT RAISE


def must_fail_problems(xml_text, must_fail, returncode: int, timed_out: bool) -> list:
    """Problems with a run in which each named test must FAIL with an assertion of a collected test. Any other reason (NameError, AttributeError,
    ImportError, SyntaxError, a setup or collection error), a skip, or a test that is not collected means the control proves nothing."""
    if timed_out:
        return ["pytest timed out"]
    if not xml_text:
        return ["no JUnit XML result"]
    try:
        details = _case_details(xml_text)
    except ET.ParseError as exc:
        return [f"JUnit XML unreadable: {exc}"]
    problems = []
    for want in must_fail:
        file, _, tid = want.partition("::")
        hits = [(outcome, kind) for f, t, outcome, kind in details if f == file and _base(t) == _base(tid)]
        wrong = sorted({"error" if outcome == "error" else kind for outcome, kind in hits
                        if outcome == "error" or (outcome == "failed" and kind not in ASSERTION_KINDS)})
        if not hits:
            problems.append(f"{want}: not collected, so it cannot show the failure")
        elif wrong:
            problems.append(f"{want}: fails for a reason other than an assertion ({', '.join(wrong)})")
        elif not any(outcome == "failed" for outcome, _kind in hits):
            problems.append(f"{want}: did not fail ({', '.join(sorted({outcome for outcome, _kind in hits}))})")
    return problems
