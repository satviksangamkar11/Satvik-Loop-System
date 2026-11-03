"""Locked acceptance tests for fl_chunk (four-loop job fl-chunk). Controller-owned: the coder never edits this file. Each test loads the module itself."""
import importlib.util
import sys
from pathlib import Path

SOURCE = Path(__file__).parents[2] / "scripts/source"
MODULE = SOURCE / "fl_chunk.py"


def load():
    assert MODULE.is_file(), "fl_chunk.py not written yet"
    spec = importlib.util.spec_from_file_location("fl_chunk_under_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_even_and_uneven():
    m = load()
    assert m.chunks([1, 2, 3, 4], 2) == [[1, 2], [3, 4]] and m.chunks([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


def test_empty_list():
    m = load()
    assert m.chunks([], 3) == []


def test_size_bigger_than_list():
    m = load()
    assert m.chunks(['a', 'b'], 5) == [['a', 'b']]


def test_bad_size_raises():
    m = load()
    import pytest
    with pytest.raises(ValueError):
        m.chunks([1], 0)
