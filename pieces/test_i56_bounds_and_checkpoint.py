"""I56: a green run is kept on disk before any repair (a checkpoint that restore_green falls back to when the memory copy is gone; a damaged
checkpoint is never restored), and the sandbox's captured output, JUnit report and writable area are bounded. No Docker, no model call."""
import json
from pathlib import Path

import pytest

import p0_gates as gates
import p1b_production as pm
import sandbox_runner as sr
from test_p1b_production_run import _production_profile, _repair_only_production  # noqa: F401  (the production profile fixture)


def test_a_green_checkpoint_is_written_atomically_and_read_back(tmp_path):
    files = {"pieces/calc.py": "x = 1\n", "pieces/gone.py": None}
    folder = tmp_path / "green"
    pm.write_green_checkpoint(folder, files, pm._texts_hash(files))
    assert pm.read_green_checkpoint(folder) == {"tested_hash": pm._texts_hash(files), "files": files}
    assert not any(p.name.endswith(".tmp") for p in folder.iterdir())


def test_a_damaged_checkpoint_is_never_read_back(tmp_path):
    files = {"pieces/calc.py": "x = 1\n"}
    folder = tmp_path / "green"
    target = pm.write_green_checkpoint(folder, files, pm._texts_hash(files))
    target.write_text(json.dumps({"tested_hash": pm._texts_hash(files), "files": {"pieces/calc.py": "x = 2\n"}}), encoding="utf-8")
    assert pm.read_green_checkpoint(folder) is None           # the text does not hash to the recorded tested_hash
    target.write_text("{not json", encoding="utf-8")
    assert pm.read_green_checkpoint(folder) is None


def test_restore_green_puts_the_green_files_back_from_disk_when_memory_is_gone(tmp_path):
    models, _history = _repair_only_production(tmp_path)
    green = {"pieces/calc.py": "x = 1\n", "pieces/test_calc.py": "def test_x():\n    pass\n"}     # the job's two files
    pm.write_green_checkpoint(models.job_dir / pm.GREEN_DIR, green, pm._texts_hash(green))
    (models.root / "pieces" / "calc.py").write_text("x = 'half written by a failed repair'\n", encoding="utf-8")
    models.green_files, models.tested_hash = None, None
    models.restore_green()
    assert (models.root / "pieces" / "calc.py").read_text(encoding="utf-8") == "x = 1\n"
    assert models.tested_hash == pm._texts_hash(green)


def test_restore_green_refuses_a_damaged_checkpoint(tmp_path):
    models, _history = _repair_only_production(tmp_path)
    green = {"pieces/calc.py": "x = 1\n"}
    target = pm.write_green_checkpoint(models.job_dir / pm.GREEN_DIR, green, pm._texts_hash(green))
    target.write_text(json.dumps({"tested_hash": pm._texts_hash(green), "files": {"pieces/calc.py": "tampered\n"}}), encoding="utf-8")
    models.green_files = None
    with pytest.raises(AssertionError, match="before any green run"):
        models.restore_green()


def test_a_green_run_with_crlf_files_is_captured_restored_and_hashed_the_same_bytes(tmp_path):
    """I69: the green files are captured, checkpointed and restored as their exact bytes. Text mode dropped the \\r of a CRLF file (on Windows
    it also wrote \\r\\n for every \\n), so the restored files did not hash to the green run. Keys are posix relative paths."""
    root = tmp_path / "repo"
    rel = "pieces/sub/calc.py"
    raw = b"x = 1\r\ny = 2\r\n"
    (root / "pieces" / "sub").mkdir(parents=True)
    (root / rel).write_bytes(raw)
    green = {rel: pm.read_text_exact(root / rel)}                        # captured the way the green run captures its files
    assert green[rel] == raw.decode("utf-8")
    tested = gates.combined_hash(root, [rel])
    assert pm._texts_hash(green) == tested                                # the checkpoint hashes what is on disk
    folder = tmp_path / "green"
    pm.write_green_checkpoint(folder, green, tested)
    pm.write_text_exact(root / rel, "x = 'damaged'\n")
    cp = pm.read_green_checkpoint(folder)
    assert cp is not None
    pm.restore_files(root, cp["files"])
    assert (root / rel).read_bytes() == raw
    assert gates.combined_hash(root, [rel]) == cp["tested_hash"] == tested


def test_restored_files_are_written_as_their_exact_bytes_on_every_platform(tmp_path):
    """Text mode writes \\r\\n for \\n on Windows; the restore must not (I69)."""
    pm.restore_files(tmp_path, {"pieces/a.py": "one\ntwo\n", "pieces/b.py": "x\r\n"})
    assert (tmp_path / "pieces" / "a.py").read_bytes() == b"one\ntwo\n"
    assert (tmp_path / "pieces" / "b.py").read_bytes() == b"x\r\n"


def test_captured_output_is_kept_to_its_head_and_tail(tmp_path):
    text = "A" * (sr.OUTPUT_KEEP_EACH_END + 10) + "M" * 1_000_000 + "Z" * (sr.OUTPUT_KEEP_EACH_END + 10)
    small = "short output"
    assert sr._bounded_output(small) == small
    bounded = sr._bounded_output(text * 3)
    assert len(bounded) <= 2 * sr.OUTPUT_KEEP_EACH_END + 200 and "characters of output dropped" in bounded
    assert bounded.startswith("A") and bounded.endswith("Z")


def test_a_junit_report_over_the_bound_is_not_read(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "MAX_JUNIT_BYTES", 100)

    def fake_container(self, tmpd, cmd, readonly=None, timeout_s=600, extra_mounts=()):
        out_dir = [host for host, target, _mode in extra_mounts if target == "/out"][0]
        (out_dir / "junit.xml").write_text("<testsuites>" + "x" * 500 + "</testsuites>", encoding="utf-8")
        return sr.RunResult(0, "1 passed in 0.01s", None, False)

    monkeypatch.setattr(sr.DockerSandbox, "_run_container", fake_container)
    root = tmp_path / "repo"
    (root / "pieces").mkdir(parents=True)
    (root / "pieces" / "test_x.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    res = sr.DockerSandbox().run(root, ["pieces/test_x.py"])
    assert res.junit is None and "not read" in res.output and res.returncode == 0   # no report: the controller refuses the run


def test_a_writable_area_over_the_bound_is_a_failed_run(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "MAX_WRITABLE_BYTES", 1000)

    def fake_container(self, tmpd, cmd, readonly=None, timeout_s=600, extra_mounts=()):
        (tmpd / "work" / "filler.bin").write_bytes(b"0" * 5000)
        return sr.RunResult(0, "1 passed in 0.01s", None, False)

    monkeypatch.setattr(sr.DockerSandbox, "_run_container", fake_container)
    root = tmp_path / "repo"
    (root / "pieces").mkdir(parents=True)
    (root / "pieces" / "test_x.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    res = sr.DockerSandbox().run(root, ["pieces/test_x.py"])
    assert res.returncode != 0 and res.junit is None and "writable area" in res.output
