"""Locked acceptance tests for loop_status (pilot). Controller-owned: the coder never edits this file. Each test builds its own _loop folder."""
import importlib.util
import json
from pathlib import Path

MODULE = Path(__file__).parents[2] / "scripts/source/loop_status.py"


def load():
    assert MODULE.is_file(), "loop_status.py not written yet"
    spec = importlib.util.spec_from_file_location("loop_status_under_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_status(loop, name, text):
    folder = loop / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "status.json").write_text(text, encoding="utf-8")


def make_data(tmp_path):
    loop = tmp_path / "_loop"
    write_status(loop, "a", json.dumps({"name": "a", "state": "passed", "reason": "READY from: x", "coder_calls": 2,
                                        "updated": "2026-11-02T10:00:00Z"}))
    write_status(loop, "b", json.dumps({"name": "b", "state": "provider-unavailable", "reason": "r" * 100, "coder_calls": 1,
                                        "updated": "2026-11-02T11:00:00Z"}))
    write_status(loop, "c", "{")
    return loop


def test_rows_order_and_fields(tmp_path):
    rows, bad = load().collect(make_data(tmp_path))
    assert [r["name"] for r in rows] == ["b", "a"]
    for row in rows:
        assert set(row) == {"name", "state", "reason", "coder_calls", "updated"}
    assert len(rows[0]["reason"]) == 80
    assert rows[1]["coder_calls"] == 2
    assert bad == ["c"]


def test_missing_fields_default(tmp_path):
    loop = tmp_path / "_loop"
    write_status(loop, "only", json.dumps({"name": "only"}))
    rows, bad = load().collect(loop)
    assert rows == [{"name": "only", "state": "", "reason": "", "coder_calls": 0, "updated": ""}]
    assert bad == []


def test_not_an_object(tmp_path):
    loop = tmp_path / "_loop"
    write_status(loop, "list", json.dumps([1, 2]))
    rows, bad = load().collect(loop)
    assert rows == []
    assert bad == ["list"]


def test_tie_sorted_by_name(tmp_path):
    loop = tmp_path / "_loop"
    same = "2026-11-02T12:00:00Z"
    write_status(loop, "zeta", json.dumps({"name": "zeta", "state": "passed", "updated": same}))
    write_status(loop, "alpha", json.dumps({"name": "alpha", "state": "passed", "updated": same}))
    rows, _ = load().collect(loop)
    assert [r["name"] for r in rows] == ["alpha", "zeta"]


def test_deeper_files_ignored(tmp_path):
    loop = tmp_path / "_loop"
    write_status(loop, "a", json.dumps({"name": "a", "state": "passed", "updated": "2026-11-02T10:00:00Z"}))
    (loop / "x" / "sub").mkdir(parents=True)
    (loop / "x" / "sub" / "status.json").write_text(json.dumps({"name": "deep", "state": "passed"}), encoding="utf-8")
    rows, bad = load().collect(loop)
    assert [r["name"] for r in rows] == ["a"]
    assert bad == []


def test_missing_dir(tmp_path):
    assert load().collect(tmp_path / "nope") == ([], [])


def test_main_output(tmp_path, capsys):
    loop = make_data(tmp_path)
    code = load().main([str(loop)])
    out = capsys.readouterr().out.splitlines()
    assert code == 0
    assert len(out) == 3
    assert out[0] == "b | provider-unavailable | 1 | " + "r" * 80
    assert out[1] == "a | passed | 2 | READY from: x"
    assert out[-1] == "unreadable: c"


def test_read_only(tmp_path, capsys):
    loop = make_data(tmp_path)

    def snapshot():
        return {str(p): (p.read_bytes() if p.is_file() else None, p.stat().st_mtime_ns) for p in tmp_path.rglob("*")}

    before = snapshot()
    load().collect(loop)
    load().main([str(loop)])
    capsys.readouterr()
    assert snapshot() == before
