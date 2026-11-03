import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts', 'source'))

import tl_index


def test_empty_list():
    assert tl_index.index_entries([]) == []


def test_single_title():
    result = tl_index.index_entries(["Hello World"])
    assert result == [("hello-world", "Hello World")]


def test_multiple_unique_titles():
    result = tl_index.index_entries(["First", "Second", "Third"])
    assert result == [("first", "First"), ("second", "Second"), ("third", "Third")]


def test_duplicate_titles():
    result = tl_index.index_entries(["Section", "Section", "Section"])
    assert result == [
        ("section", "Section"),
        ("section-2", "Section"),
        ("section-3", "Section"),
    ]


def test_empty_slug_becomes_section():
    result = tl_index.index_entries(["!!!"])
    assert result == [("section", "!!!")]


def test_order_preserved():
    titles = ["Alpha", "Beta", "Alpha", "Gamma", "Alpha"]
    result = tl_index.index_entries(titles)
    anchors = [a for a, t in result]
    assert anchors == ["alpha", "beta", "alpha-2", "gamma", "alpha-3"]
    titles_out = [t for a, t in result]
    assert titles_out == titles


def test_title_returned_unchanged():
    titles = ["Mixed Case", "With Numbers 123", "Special!Chars"]
    result = tl_index.index_entries(titles)
    for orig, (_, title) in zip(titles, result):
        assert title == orig


if __name__ == "__main__":
    test_empty_list()
    test_single_title()
    test_multiple_unique_titles()
    test_duplicate_titles()
    test_empty_slug_becomes_section()
    test_order_preserved()
    test_title_returned_unchanged()
    print("All tests passed!")
