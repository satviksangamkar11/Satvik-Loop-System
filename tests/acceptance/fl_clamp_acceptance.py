"""Locked acceptance tests for fl_clamp (four-loop job fl-clamp). Controller-owned: the coder never edits this file. Each test loads the module itself."""
import importlib.util
import sys
from pathlib import Path

SOURCE = Path(__file__).parents[2] / "scripts/source"
MODULE = SOURCE / "fl_clamp.py"


def load():
    assert MODULE.is_file(), "fl_clamp.py not written yet"
    spec = importlib.util.spec_from_file_location("fl_clamp_under_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_inside_is_unchanged():
    m = load()
    assert m.clamp(5, 0, 10) == 5 and m.clamp(0, 0, 10) == 0 and m.clamp(10, 0, 10) == 10


def test_below_and_above():
    m = load()
    assert m.clamp(-3, 0, 10) == 0 and m.clamp(42, 0, 10) == 10 and m.clamp(2.5, 3.0, 4.0) == 3.0


def test_bad_range_raises():
    m = load()
    import pytest
    with pytest.raises(ValueError):
        m.clamp(1, 5, 2)
