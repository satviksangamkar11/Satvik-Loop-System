"""Locked acceptance tests for tl_roman (two-loop job tl-roman). Controller-owned: the coder never edits this file. Each test loads the module itself."""
import importlib.util
from pathlib import Path

import pytest

MODULE = Path(__file__).parents[2] / "scripts/source/tl_roman.py"

PAIRS = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
         (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
LETTER_VALUE = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def load():
    assert MODULE.is_file(), "tl_roman.py not written yet"
    spec = importlib.util.spec_from_file_location("tl_roman_under_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def greedy(n):
    out = ""
    for value, symbol in PAIRS:
        while n >= value:
            out += symbol
            n -= value
    return out


def read_back(numeral):
    total = 0
    for i, letter in enumerate(numeral):
        value = LETTER_VALUE[letter]
        if i + 1 < len(numeral) and value < LETTER_VALUE[numeral[i + 1]]:
            total -= value
        else:
            total += value
    return total


def test_examples():
    to_roman = load().to_roman
    expected = {1: "I", 4: "IV", 9: "IX", 14: "XIV", 40: "XL", 58: "LVIII", 90: "XC", 400: "CD", 900: "CM",
                1994: "MCMXCIV", 3999: "MMMCMXCIX"}
    for n, numeral in expected.items():
        assert to_roman(n) == numeral, n


def test_every_value_is_the_standard_numeral():
    to_roman = load().to_roman
    for n in range(1, 4000):
        numeral = to_roman(n)
        assert numeral == greedy(n), n
        assert read_back(numeral) == n, n


def test_out_of_range_raises():
    to_roman = load().to_roman
    for bad in (0, -1, 4000):
        with pytest.raises(ValueError):
            to_roman(bad)
