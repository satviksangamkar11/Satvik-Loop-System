"""CRLF on Windows (I73, I74, I75): job, original, candidate and green files are compared, hashed and restored as exact bytes.

Linux does not translate newlines, so each test that stands for the Windows behaviour turns text mode on for its own duration
(windows_text_io): read_text turns CRLF into LF and write_text turns LF into CRLF, as open() does on Windows. Under that, the
text-mode code paths fail on Linux the same way they failed on the owner's Windows run."""
import ast
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts" / "source"))

import fix_loop as fl                       # noqa: E402
import p1b_production as p1b                # noqa: E402
import test_p1b_review_package as p1t       # noqa: E402

CRLF_ORIGINAL = "def calculate_total(prices):\r\n    return 0\r\n"
CRLF_CANDIDATE = "def calculate_total(prices):\r\n    return sum(prices)\r\n"


def _win_read_text(self, encoding=None, errors=None, newline=None):
    text = self.read_bytes().decode(encoding or "utf-8", errors or "strict")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _win_write_text(self, data, encoding=None, errors=None, newline=None):
    self.write_bytes(data.replace("\n", "\r\n").encode(encoding or "utf-8", errors or "strict"))
    return len(data)


@pytest.fixture
def windows_text_io(monkeypatch):
    """Text mode as Windows does it: CRLF is read as LF and LF is written as CRLF. Undone after the test."""
    monkeypatch.setattr(pathlib.Path, "read_text", _win_read_text)
    monkeypatch.setattr(pathlib.Path, "write_text", _win_write_text)


# ---------------------------------------------------------------- I73: _produced and the candidate snapshot

def test_i73_produced_is_false_for_an_untouched_crlf_file(tmp_path, windows_text_io):
    target = tmp_path / "pieces" / "calc.py"
    target.parent.mkdir(parents=True)
    target.write_bytes(CRLF_ORIGINAL.encode("utf-8"))
    originals = {"pieces/calc.py": p1b.read_text_exact(target)}      # captured the way the job captures its originals
    assert p1b._produced(tmp_path, ["pieces/calc.py"], originals) is False


def test_i73_produced_is_true_once_a_crlf_file_is_written(tmp_path, windows_text_io):
    target = tmp_path / "pieces" / "calc.py"
    target.parent.mkdir(parents=True)
    target.write_bytes(CRLF_ORIGINAL.encode("utf-8"))
    originals = {"pieces/calc.py": p1b.read_text_exact(target)}
    p1b.write_text_exact(target, CRLF_CANDIDATE)
    assert p1b._produced(tmp_path, ["pieces/calc.py"], originals) is True


def test_i73_a_missing_file_is_not_produced_and_a_new_file_is(tmp_path, windows_text_io):
    assert p1b._produced(tmp_path, ["pieces/new.py"], {"pieces/new.py": None}) is False
    (tmp_path / "pieces").mkdir()
    p1b.write_text_exact(tmp_path / "pieces" / "new.py", CRLF_CANDIDATE)
    assert p1b._produced(tmp_path, ["pieces/new.py"], {"pieces/new.py": None}) is True


@pytest.fixture
def production_roster(profile):
    """The production profile and its roster, as test_p1b_review_package sets them for its own tests."""
    profile("production")
    roles = p1t.oc.roster_state()["ROLES"]
    p1t.DOTS, p1t.XING, p1t.NORTH, p1t.GLM = roles["writer_a"], roles["writer_b"], roles["reviewer_b"], roles["reviewer_a"]


def test_i73_a_provider_failure_before_the_first_write_writes_no_candidate_on_windows(tmp_path, windows_text_io, production_roster):
    sandbox = p1t.SpecialSandbox()
    root, result = p1t.run_e2e(tmp_path, sandbox=sandbox, client=p1t.DownClient())
    assert result["state"] == "provider-unavailable", result
    assert sandbox.normal_runs == 0                                  # the stop came before the first write
    assert (root / "pieces" / "calc.py").read_bytes() == p1t.ORIGINAL_CODE.replace("\n", "\r\n").encode("utf-8")   # the original, byte for byte
    assert not (root / "_loop" / "calc" / "candidate").exists()      # _produced is False, so no candidate folder is written


# ---------------------------------------------------------------- I74: the saved originals and the kill-recovery restore

CRLF_TEXTS = [
    "def calculate_total(prices):\r\n    return 0\r\n",
    "import os\r\n\r\n\r\nX = 1\r\n",                       # blank CRLF lines: a doubled \r would show here
    "A = 'one'\r\nB = 'two'",                              # no final newline
    "C = 1\rD = 2\r\n",                                    # a lone CR survives too
]


def _job_folders(tmp_path):
    root = tmp_path / "repo"
    (root / "pieces").mkdir(parents=True)
    job_dir = tmp_path / "_loop" / "calc"
    job_dir.mkdir(parents=True)
    return root, job_dir


@pytest.mark.parametrize("text", CRLF_TEXTS)
def test_i74_the_saved_original_is_the_exact_bytes_of_the_file(tmp_path, windows_text_io, text):
    root, job_dir = _job_folders(tmp_path)
    (root / "pieces" / "calc.py").write_bytes(text.encode("utf-8"))
    originals = fl._prepare_job_folder(job_dir, root, ["pieces/calc.py"])
    assert originals["pieces/calc.py"] == text
    assert (job_dir / "original" / "pieces" / "calc.py").read_bytes() == text.encode("utf-8")


@pytest.mark.parametrize("text", CRLF_TEXTS)
def test_i74_a_worker_killed_mid_run_is_restored_byte_for_byte_on_the_next_launch(tmp_path, windows_text_io, text):
    root, job_dir = _job_folders(tmp_path)
    (root / "pieces" / "calc.py").write_bytes(text.encode("utf-8"))
    fl._prepare_job_folder(job_dir, root, ["pieces/calc.py"])                                  # the first launch saves the originals
    (job_dir / "status.json").write_text(json.dumps({"state": "running"}), encoding="utf-8")    # the run was killed: still running
    (root / "pieces" / "calc.py").write_bytes(CRLF_CANDIDATE.encode("utf-8"))                  # a candidate was written
    again = fl._prepare_job_folder(job_dir, root, ["pieces/calc.py"])                          # the next launch
    assert (root / "pieces" / "calc.py").read_bytes() == text.encode("utf-8")
    assert again["pieces/calc.py"] == text


# ---------------------------------------------------------------- I75: the sweep of the other content paths, and a guard

@pytest.mark.parametrize("text", CRLF_TEXTS)
def test_i75_the_attempt_snapshot_is_the_exact_bytes(tmp_path, windows_text_io, text):
    root, job_dir = _job_folders(tmp_path)
    (root / "pieces" / "calc.py").write_bytes(text.encode("utf-8"))
    fl._save_attempt(job_dir, root, ["pieces/calc.py"])
    assert (job_dir / "attempt" / "pieces" / "calc.py").read_bytes() == text.encode("utf-8")


@pytest.mark.parametrize("text", CRLF_TEXTS)
def test_i75_a_frozen_test_is_compared_with_the_bytes_on_disk(tmp_path, windows_text_io, text):
    path = tmp_path / "test_calc.py"
    path.write_bytes(text.encode("utf-8"))
    assert fl._test_change_forbidden(path, text, None) is False      # the same bytes: not a change


def test_i75_the_green_checkpoint_holds_crlf_text_exactly(tmp_path, windows_text_io):
    files = {"pieces/calc.py": CRLF_CANDIDATE, "pieces/new.py": None}
    p1b.write_green_checkpoint(tmp_path, files, p1b._texts_hash(files))
    assert p1b.read_green_checkpoint(tmp_path)["files"] == files


def test_i75_restore_puts_the_crlf_bytes_back(tmp_path, windows_text_io):
    p1b.restore_files(tmp_path, {"pieces/calc.py": CRLF_ORIGINAL, "pieces/new.py": None})
    assert (tmp_path / "pieces" / "calc.py").read_bytes() == CRLF_ORIGINAL.encode("utf-8")


# The functions that read or write the contents of job, original, candidate, green, attempt, plan and review files. None of them may do
# text-mode file I/O (read_text, write_text, or open in a text mode): the exact helpers or bytes only. A rename must update this table.
GUARDED = {
    "fix_loop.py": ["_read_file_safe", "_prepare_job_folder", "_save_attempt", "_test_change_forbidden", "_parses"],
    "p1b_production.py": ["_produced", "_snapshot", "restore_files", "write_green_checkpoint", "read_green_checkpoint", "_texts_hash", "_apply_edits"],
    "plan_job.py": ["_write", "_summary", "package_members", "read_package"],
    "exact_text.py": ["read_text_exact", "write_text_exact"],
}
SOURCE = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "source"


def _text_mode_calls(func):
    """(line, what) for each read_text, write_text or open-in-text-mode call inside the function."""
    found = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr in ("read_text", "write_text"):
            found.append((node.lineno, fn.attr))
            continue
        is_open = (isinstance(fn, ast.Name) and fn.id == "open") or (isinstance(fn, ast.Attribute) and fn.attr == "open")
        if is_open:
            mode_at = 0 if isinstance(fn, ast.Attribute) else 1           # Path.open(mode) or open(file, mode)
            mode = node.args[mode_at] if len(node.args) > mode_at else next((k.value for k in node.keywords if k.arg == "mode"), None)
            if mode is None or not (isinstance(mode, ast.Constant) and isinstance(mode.value, str)) or "b" not in mode.value:
                found.append((node.lineno, "open in text mode"))
    return found


def _functions(path):
    tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
    return {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


@pytest.mark.parametrize("module", sorted(GUARDED))
def test_i75_the_guarded_content_functions_do_no_text_mode_io(module):
    defs = _functions(SOURCE / module)
    for name in GUARDED[module]:
        assert name in defs, f"{module}: {name} is not defined any more; update GUARDED"
        assert _text_mode_calls(defs[name]) == [], f"{module}: {name} does text-mode file I/O on contents"


def test_i75_the_guard_itself_catches_text_mode_io():
    def func(source):
        return _text_mode_calls(ast.parse(source).body[0])
    assert func("def f(p):\n    return p.read_text(encoding='utf-8')")
    assert func("def f(p, t):\n    p.write_text(t)")
    assert func("def f(p):\n    return open(p, 'r').read()")
    assert func("def f(p):\n    return open(p).read()")
    assert func("def f(p):\n    return p.open('w')")
    assert func("def f(p, m):\n    return open(p, m)")
    assert func("def f(p):\n    return open(p, 'rb').read()") == []
    assert func("def f(p, t):\n    p.write_bytes(t.encode())") == []


# ---------------------------------------------------------------- I76: a model reply is compared with an on-disk original line-ending-insensitively
# The coder always replies with LF. A CRLF file on disk (Windows) restated unchanged by the coder is not a change; a changed test still is.
# Hashes of locked files stay byte-exact (the acceptance lock is not touched by these comparisons).

from test_groq_files import write_job                     # noqa: E402 (pieces/ is on sys.path under pytest)
from test_fix_loop_review import FENCE, FILE_MARK, make_root  # noqa: E402

FROZEN_CRLF_TEST = "def test_m():\r\n    assert True\r\n"
FROZEN_LF_TEST = "def test_m():\n    assert True\n"
APPENDED_LF_TEST = "\ndef test_m_again():\n    assert True\n"


def _ok_reviewer(files, report):
    report.write_text("## Verdict: READY\n", encoding="utf-8")
    return {"verdict": "READY"}


def _run_round0(tmp_path, reply_test_text):
    """One round-0 job (groq scenario): pieces/test_m.py is a pre-existing CRLF file on disk; the coder replies with `reply_test_text` for it."""
    root = make_root(tmp_path)
    (root / "pieces" / "test_m.py").write_bytes(FROZEN_CRLF_TEST.encode("utf-8"))
    loaded = fl.load_job(write_job(root, {}))
    reply_text = (FILE_MARK + "pieces/m.py\n" + FENCE + "python\nX = 2\n" + FENCE + "\n"
                  + FILE_MARK + "pieces/test_m.py\n" + FENCE + "python\n" + reply_test_text + FENCE + "\n")
    return fl.run_job(loaded, root, caller=lambda s_, u: (reply_text, {"status": 200, "finish": "stop"}),
                      reviewer={"codex": _ok_reviewer, "groq": _ok_reviewer}, pytest_runner=lambda *a, **k: (True, "ok"), scrub_keys=[])


def test_i76_round0_accepts_an_lf_reply_that_restates_a_crlf_test_and_appends_a_test(tmp_path):
    result = _run_round0(tmp_path, FROZEN_LF_TEST + APPENDED_LF_TEST)
    assert result["state"] == "passed", result["rounds"]
    assert result["rounds"][0]["reason"] is None


def test_i76_round0_refuses_a_real_change_to_a_crlf_test(tmp_path):
    result = _run_round0(tmp_path, "def test_m():\n    assert False\n" + APPENDED_LF_TEST)
    assert result["state"] == "escalate"
    assert "frozen or pre-existing tests" in result["rounds"][0]["reason"]
    assert result["rounds"][0]["reason"].count("pieces/test_m.py") == 1


def test_i76_a_frozen_crlf_test_restated_in_lf_is_not_a_change(tmp_path, windows_text_io):
    path = tmp_path / "test_m.py"
    path.write_bytes(FROZEN_CRLF_TEST.encode("utf-8"))
    assert fl._test_change_forbidden(path, FROZEN_LF_TEST, None) is False        # the same text, other line endings: not a change


def test_i76_a_frozen_crlf_test_with_a_real_change_is_still_forbidden(tmp_path):
    path = tmp_path / "test_m.py"
    path.write_bytes(FROZEN_CRLF_TEST.encode("utf-8"))
    assert fl._test_change_forbidden(path, "def test_m():\n    assert False\n", None) is True


def test_i76_the_groq_reviewer_file_set_passes_with_a_crlf_test_file(tmp_path, windows_text_io):
    """pieces/test_groq_files.py::test_the_groq_reviewer_receives_only_its_own_file_set, with test_m.py written as CRLF (Windows)."""
    root = make_root(tmp_path)
    (root / "pieces" / "test_m.py").write_text("def test_m():\n    assert True\n", encoding="utf-8")
    assert (root / "pieces" / "test_m.py").read_bytes() == FROZEN_CRLF_TEST.encode("utf-8")
    loaded = fl.load_job(write_job(root, {"groq_review_files": ["pieces/m.py", "pieces/test_m.py"]}))
    seen = {}

    def groq(files, report):
        seen["groq"] = [p.name for p in files]
        return _ok_reviewer(files, report)

    def codex(files, report):
        seen["codex"] = [p.name for p in files]
        return _ok_reviewer(files, report)

    reply_text = (FILE_MARK + "pieces/m.py\n" + FENCE + "python\nX = 2\n" + FENCE + "\n"
                  + FILE_MARK + "pieces/test_m.py\n" + FENCE + "python\ndef test_m():\n    assert True\n" + FENCE + "\n")
    result = fl.run_job(loaded, root, caller=lambda s_, u: (reply_text, {"status": 200, "finish": "stop"}),
                        reviewer={"codex": codex, "groq": groq}, pytest_runner=lambda *a, **k: (True, "ok"), scrub_keys=[])
    assert result["state"] == "passed", result["rounds"]
    assert seen["groq"] == ["m.py", "test_m.py"]
    assert seen["codex"] == ["m.py", "test_m.py", "s.md"]


def test_i76_the_review_package_shows_an_untouched_crlf_file_as_unchanged(windows_text_io):
    assert p1b.diff_block("pieces/m.py", "X = 1\r\n", "X = 1\n") == "pieces/m.py: (no change)"
    assert p1b.diff_block("pieces/m.py", "X = 1\r\n", "X = 2\n").startswith("pieces/m.py: changed")
