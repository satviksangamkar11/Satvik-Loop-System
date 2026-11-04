"""CRLF on Windows (I73, I74, I75): job, original, candidate and green files are compared, hashed and restored as exact bytes.

Linux does not translate newlines, so each test that stands for the Windows behaviour turns text mode on for its own duration
(windows_text_io): read_text turns CRLF into LF and write_text turns LF into CRLF, as open() does on Windows. Under that, the
text-mode code paths fail on Linux the same way they failed on the owner's Windows run."""
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
