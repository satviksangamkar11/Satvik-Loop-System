"""Owner 2026-10-31: a job may give the Groq reviewer its own file set (`groq_review_files`), because the Groq free tier has an input budget
(GROQ_MAX_INPUT_CHARS). The option must be a subset of review_files and must include every job file; the Groq reviewer must receive exactly that set.
Fakes only: no model call."""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
import fix_loop as fl  # noqa: E402
from test_fix_loop_review import FENCE, FILE_MARK, make_root  # noqa: E402


def write_job(root, extra):
    job = {"name": "j", "spec": "specs/s.md", "files": ["pieces/m.py", "pieces/test_m.py"], "tests": ["pieces/test_m.py"], "reference": [],
           "max_rounds": 1, "review": True, "reviewers": ["codex", "groq"], "review_prompt": "docs/RP.md", "fixers": ["nemotron"] * 3,
           "review_files": ["pieces/m.py", "pieces/test_m.py", "specs/s.md"], **extra}
    (root / "_loop" / "jobs").mkdir(exist_ok=True)
    path = root / "_loop" / "jobs" / "j.json"
    path.write_text(json.dumps(job), encoding="utf-8")
    return path


def test_groq_review_files_is_a_subset_of_review_files_and_keeps_the_job_files(tmp_path):
    root = make_root(tmp_path)
    (root / "pieces" / "test_m.py").write_text("def test_m():\n    assert True\n", encoding="utf-8")
    ok = write_job(root, {"groq_review_files": ["pieces/m.py", "pieces/test_m.py"]})
    assert fl.load_job(ok)["groq_review_files"] == ["pieces/m.py", "pieces/test_m.py"]
    with pytest.raises(ValueError, match="subset"):
        fl.load_job(write_job(root, {"groq_review_files": ["pieces/m.py", "pieces/test_m.py", "docs/other.md"]}))
    with pytest.raises(ValueError, match="include every job file"):
        fl.load_job(write_job(root, {"groq_review_files": ["pieces/m.py"]}))


def test_the_groq_reviewer_receives_only_its_own_file_set(tmp_path):
    root = make_root(tmp_path)
    (root / "pieces" / "test_m.py").write_text("def test_m():\n    assert True\n", encoding="utf-8")
    loaded = fl.load_job(write_job(root, {"groq_review_files": ["pieces/m.py", "pieces/test_m.py"]}))
    seen = {}

    def groq(files, report):
        seen["groq"] = [p.name for p in files]
        report.write_text("## Verdict: READY\n", encoding="utf-8")
        return {"verdict": "READY"}

    def codex(files, report):
        seen["codex"] = [p.name for p in files]
        report.write_text("## Verdict: READY\n", encoding="utf-8")
        return {"verdict": "READY"}

    reply_text = (FILE_MARK + "pieces/m.py\n" + FENCE + "python\nX = 2\n" + FENCE + "\n"
                  + FILE_MARK + "pieces/test_m.py\n" + FENCE + "python\ndef test_m():\n    assert True\n" + FENCE + "\n")
    result = fl.run_job(loaded, root, caller=lambda s_, u: (reply_text, {"status": 200, "finish": "stop"}),
                        reviewer={"codex": codex, "groq": groq}, pytest_runner=lambda *a, **k: (True, "ok"), scrub_keys=[])
    assert result["state"] == "passed"
    assert seen["groq"] == ["m.py", "test_m.py"]                 # the Groq reviewer got only its own set
    assert seen["codex"] == ["m.py", "test_m.py", "s.md"]        # the other reviewers keep the full review_files
