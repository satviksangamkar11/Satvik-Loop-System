"""Locked acceptance tests for tl_index (two-loop job tl-index). Controller-owned: the coder never edits this file. Each test loads the module itself.

tl_index imports slugify from tl_slug (job tl-slug), so the folder scripts/source goes on sys.path first."""
import importlib.util
import sys
from pathlib import Path

SOURCE = Path(__file__).parents[2] / "scripts/source"
MODULE = SOURCE / "tl_index.py"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))


def load():
    assert MODULE.is_file(), "tl_index.py not written yet"
    spec = importlib.util.spec_from_file_location("tl_index_under_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_anchors_follow_the_titles_in_order():
    assert load().index_entries(["Intro", "Setup Guide", "Usage"]) == [
        ("intro", "Intro"), ("setup-guide", "Setup Guide"), ("usage", "Usage")]


def test_repeated_anchors_are_numbered():
    assert load().index_entries(["Notes", "Notes", "Notes"]) == [
        ("notes", "Notes"), ("notes-2", "Notes"), ("notes-3", "Notes")]


def test_empty_anchor_becomes_section():
    assert load().index_entries(["!!!", "???"]) == [("section", "!!!"), ("section-2", "???")]


def test_empty_list_gives_empty_list():
    assert load().index_entries([]) == []


def test_anchor_comes_from_tl_slug(monkeypatch):
    import tl_slug
    monkeypatch.setattr(tl_slug, "slugify", lambda text: "X")
    assert load().index_entries(["a", "b"]) == [("X", "a"), ("X-2", "b")]
