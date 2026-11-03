"""Locked acceptance tests for fl_wordcount (four-loop job fl-wordcount). Controller-owned: the coder never edits this file. Each test loads the module itself."""
import importlib.util
import sys
from pathlib import Path

SOURCE = Path(__file__).parents[2] / "scripts/source"
MODULE = SOURCE / "fl_wordcount.py"


def load():
    assert MODULE.is_file(), "fl_wordcount.py not written yet"
    spec = importlib.util.spec_from_file_location("fl_wordcount_under_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_case_is_ignored():
    m = load()
    assert m.word_counts('The the THE cat') == {'the': 3, 'cat': 1}


def test_punctuation_separates():
    m = load()
    assert m.word_counts('a,b; a-b c3po!') == {'a': 2, 'b': 2, 'c3po': 1}


def test_empty():
    m = load()
    assert m.word_counts('') == {}
