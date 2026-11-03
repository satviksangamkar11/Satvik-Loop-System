"""Locked acceptance tests for tl_slug (two-loop job tl-slug). Controller-owned: the coder never edits this file. Each test loads the module itself."""
import importlib.util
from pathlib import Path

MODULE = Path(__file__).parents[2] / "scripts/source/tl_slug.py"


def load():
    assert MODULE.is_file(), "tl_slug.py not written yet"
    spec = importlib.util.spec_from_file_location("tl_slug_under_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_basic_words():
    slugify = load().slugify
    assert slugify("Hello, World!") == "hello-world"
    assert slugify("ABC") == "abc"


def test_runs_collapse_to_one_dash():
    slugify = load().slugify
    assert slugify("a   --  b") == "a-b"
    assert slugify("x,.;y") == "x-y"


def test_leading_and_trailing_dashes_removed():
    assert load().slugify("  -Leading and trailing-  ") == "leading-and-trailing"


def test_digits_are_kept():
    assert load().slugify("Version 2.0 beta") == "version-2-0-beta"


def test_no_letters_or_digits_gives_empty():
    slugify = load().slugify
    assert slugify("") == ""
    assert slugify("!!! ???") == ""


def test_non_ascii_is_a_separator():
    assert load().slugify("café au lait") == "caf-au-lait"


def test_slug_is_stable_when_applied_twice():
    slugify = load().slugify
    for text in ["Hello, World!", "a   --  b", "Version 2.0 beta", "café au lait"]:
        once = slugify(text)
        assert slugify(once) == once
