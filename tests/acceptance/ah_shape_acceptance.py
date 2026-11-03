"""Locked acceptance tests for api_shape (loop job ah-shape). Controller-owned: the coder never edits this file."""
import importlib.util
from pathlib import Path

MODULE = Path(__file__).parents[2] / "scripts/source/api_shape.py"


def load():
    assert MODULE.is_file(), "api_shape.py not written yet"
    spec = importlib.util.spec_from_file_location("api_shape_under_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_same_shape_is_empty():
    m = load()
    assert m.shape_diff({'a': 1, 'b': [{'x': 's'}]}, {'a': 2.5, 'b': [{'x': 't'}, {'x': 'u'}]}) == []


def test_missing_and_extra_keys():
    m = load()
    assert m.shape_diff({'a': 1, 'b': 2}, {'a': 1, 'c': 3}) == ['missing $.b', 'extra $.c']


def test_type_mismatches():
    m = load()
    assert m.shape_diff({'a': 1, 'b': 's', 'c': True}, {'a': 's', 'b': 2, 'c': 1}) == ['type $.a: expected number', 'type $.b: expected str', 'type $.c: expected bool']


def test_null_matches_anything():
    m = load()
    assert m.shape_diff({'a': None, 'b': 1}, {'a': {'z': 1}, 'b': None}) == []


def test_list_items_and_paths():
    m = load()
    assert m.shape_diff({'l': [{'k': 1}]}, {'l': [{'k': 1}, {'k': 'x'}]}) == ['type $.l[1].k: expected number']
    assert m.shape_diff({'l': [1]}, {'l': 'no'}) == ['type $.l: expected list']
