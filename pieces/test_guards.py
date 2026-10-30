"""MECHANICAL GUARDS (P1-D33): each recurring mistake of this project is turned into a check that fails the build. Static, no network, no model call.
Mistakes it prevents (all happened): a `raise` swallowed by its own `except` (twice), pipe-based Codex calls (false hangs), code that creates folders,
the forbidden 3D word in generated code, and a spec-required side effect that the code never performs (HF_HUB_OFFLINE)."""
import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FORBIDDEN = "blend" + "er"                                       # the 7-letter name of the 3D tool, built from pieces on purpose

# SCOPE: the new Phase 1 code (p1_*, p2_*, p3_* modules and tools/). The older frozen modules (a*, c*, d*, f*, g*) and the copied loop scripts are reviewed
# and not changed here. Every allowlist entry carries its reason.
ALLOW_SWALLOWED_RAISE = {"p1_claims.py": "KeyLedger._read raises ValueError on purpose to reach its own 'unparseable ledger: rebuild' handler"}
ALLOW_MKDIR = {"p1_inventory.py": "write_inventory creates only the output folder its caller names", "boundary_check.py": "creates its own work/phase1/boundary folder"}


def code_files(include_tests=False):
    out = []
    for d, pattern in (("pieces", "p[1234]_*.py"), ("tools", "*.py")):
        for p in sorted((REPO / d).glob(pattern)):
            if p.name.startswith("test_") and not include_tests:
                continue
            out.append(p)
    return out


def swallowed_raises(source):
    """(line, exception name) of every `raise X` inside a `try` body whose handlers would catch X (or everything)."""
    tree = ast.parse(source)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        caught, broad = set(), False
        for h in node.handlers:
            if h.type is None:
                broad = True
            elif isinstance(h.type, ast.Name):
                caught.add(h.type.id)
            elif isinstance(h.type, ast.Tuple):
                caught |= {e.id for e in h.type.elts if isinstance(e, ast.Name)}
        broad = broad or bool({"Exception", "BaseException"} & caught)
        for stmt in node.body:
            for r in ast.walk(stmt):
                if isinstance(r, ast.Raise) and r.exc is not None:
                    e = r.exc
                    name = e.func.id if isinstance(e, ast.Call) and isinstance(e.func, ast.Name) else (e.id if isinstance(e, ast.Name) else None)
                    if name and (name in caught or broad):
                        hits.append((r.lineno, name))
    return hits


def test_the_detector_finds_the_swallowed_raise_and_not_a_clean_one():
    bad = "def f(x):\n    try:\n        if x:\n            raise ValueError('no')\n    except ValueError:\n        pass\n"
    good = "def f(x):\n    if x:\n        raise ValueError('no')\n    try:\n        int('1')\n    except ValueError:\n        pass\n"
    assert swallowed_raises(bad) == [(4, "ValueError")]
    assert swallowed_raises(good) == []


def test_no_module_swallows_its_own_raise():
    offenders = {p.name: swallowed_raises(p.read_text(encoding="utf-8")) for p in code_files() if p.name not in ALLOW_SWALLOWED_RAISE}
    offenders = {k: v for k, v in offenders.items() if v}
    assert offenders == {}, offenders


def test_codex_is_never_called_through_pipes():
    """capture_output=True with a codex command waits for helper processes that keep the pipe open (three false 'hangs' of 5 to 35 minutes)."""
    bad = []
    for p in code_files():
        text = p.read_text(encoding="utf-8")
        if p.name in ("test_guards.py",):
            continue
        for m in re.finditer(r"subprocess\.run\((?:(?!\n\n).)*?capture_output\s*=\s*True", text, flags=re.S):
            if "codex" in text[max(0, m.start() - 400):m.end() + 400].lower():
                bad.append(p.name)
                break
    assert bad == [], bad


def test_no_new_code_creates_folders():
    """Folders are never created by project code (owner rule). Allowed only with a reason (ALLOW_MKDIR)."""
    offenders = []
    for p in code_files():
        if p.name in ALLOW_MKDIR or p.name == "test_guards.py":
            continue
        if re.search(r"\.mkdir\(|os\.makedirs\(", p.read_text(encoding="utf-8")):
            offenders.append(p.name)
    assert offenders == [], offenders


def test_the_forbidden_word_is_in_no_code_file_and_no_spec():
    hits = [str(p.relative_to(REPO)) for p in code_files(include_tests=True) + sorted((REPO / "specs").glob("*.md"))
            if p.name != "test_guards.py" and FORBIDDEN in p.read_text(encoding="utf-8", errors="ignore").lower()]
    assert hits == [], hits


def test_the_audio_module_really_sets_hf_hub_offline():
    """The spec requires it so that nothing is ever downloaded; the first generated version only had a comment about it."""
    text = (REPO / "pieces" / "p3_audio_evidence.py").read_text(encoding="utf-8")
    assert re.search(r"os\.environ\[\s*[\"']HF_HUB_OFFLINE[\"']\s*\]\s*=\s*[\"']1[\"']", text)


def test_review_prompt_checks_the_recurring_mistake_classes():
    text = (REPO / "docs" / "REVIEW_PROMPT.md").read_text(encoding="utf-8").lower()
    for needle in ("swallow", "producer", "side effect"):
        assert needle in text, needle


def test_agent_rules_list_the_known_coder_traps():
    text = (REPO / "docs" / "AGENT_RULES.md").read_text(encoding="utf-8")
    assert "KNOWN CODER TRAPS" in text


def test_every_real_job_file_is_consistent():
    """Learned 2026-10-30: job files copied from another job kept its review_files, so reviewers judged the wrong files."""
    import json
    repo = Path(__file__).resolve().parents[1]
    jobs = sorted((repo / "_loop" / "jobs").glob("*.json"))
    assert jobs, "no job files found: this guard would pass vacuously"
    for path in jobs:
        job = json.loads(path.read_text(encoding="utf-8"))
        assert (repo / job["spec"]).is_file(), f"{path.name}: spec missing"
        assert job["name"] == path.stem, f"{path.name}: name differs from the file name"
        assert all(f == "nemotron" for f in job.get("fixers", ["nemotron"])), f"{path.name}: non-nemotron fixer"
        assert set(job.get("reviewers", [])) <= {"lightning", "minimax", "codex", "haiku"}, f"{path.name}: unknown reviewer"
        if job.get("review_files") is not None:
            assert set(job["files"]) <= set(job["review_files"]), f"{path.name}: review_files lack its own files"
        assert set(job["tests"]) <= set(job["files"]), f"{path.name}: a test is not a job file"
