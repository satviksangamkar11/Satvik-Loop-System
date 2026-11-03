"""Locked acceptance tests for fl_dedupe (four-loop job fl-dedupe). Controller-owned: the coder never edits this file. Each test loads the module itself."""
import importlib.util
import sys
from pathlib import Path

SOURCE = Path(__file__).parents[2] / "scripts/source"
MODULE = SOURCE / "fl_dedupe.py"


def load():
    assert MODULE.is_file(), "fl_dedupe.py not written yet"
    spec = importlib.util.spec_from_file_location("fl_dedupe_under_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_order_of_first_occurrence():
    m = load()
    assert m.dedupe([3, 1, 3, 2, 1]) == [3, 1, 2] and m.dedupe(['b', 'a', 'b']) == ['b', 'a']


def test_empty():
    m = load()
    assert m.dedupe([]) == []


def test_input_not_changed():
    m = load()
    data = [2, 2, 1]
    m.dedupe(data)
    assert data == [2, 2, 1]
