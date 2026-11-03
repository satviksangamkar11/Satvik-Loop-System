"""Locked acceptance tests for fl_top (four-loop job fl-top). Controller-owned: the coder never edits this file. Each test loads the module itself."""
import importlib.util
import sys
from pathlib import Path

SOURCE = Path(__file__).parents[2] / "scripts/source"
MODULE = SOURCE / "fl_top.py"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))


def load():
    assert MODULE.is_file(), "fl_top.py not written yet"
    spec = importlib.util.spec_from_file_location("fl_top_under_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sorted_by_count_then_word():
    m = load()
    assert m.top_words('b a c a b a', 2) == [('a', 3), ('b', 2)]


def test_ties_by_word():
    m = load()
    assert m.top_words('z y x', 3) == [('x', 1), ('y', 1), ('z', 1)]


def test_zero_and_negative():
    m = load()
    import pytest
    assert m.top_words('a b', 0) == []
    with pytest.raises(ValueError):
        m.top_words('a', -1)


def test_counts_come_from_fl_wordcount(monkeypatch):
    m = load()
    import fl_wordcount
    monkeypatch.setattr(fl_wordcount, 'word_counts', lambda text: {'q': 9, 'r': 1})
    assert m.top_words('anything', 1) == [('q', 9)]
