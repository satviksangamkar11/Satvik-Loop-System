"""Tests for tl_roman.to_roman."""

import os
import sys
import pytest

# Ensure the source directory is importable.
sys.path.insert(
    0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "scripts", "source"))
)

from tl_roman import to_roman


class TestToRoman:
    """Test cases for the to_roman function."""

    # ---- basic single-digit ----
    def test_1_to_3(self):
        assert to_roman(1) == "I"
        assert to_roman(2) == "II"
        assert to_roman(3) == "III"

    def test_4(self):
        assert to_roman(4) == "IV"

    def test_5_to_8(self):
        assert to_roman(5) == "V"
        assert to_roman(6) == "VI"
        assert to_roman(7) == "VII"
        assert to_roman(8) == "VIII"

    def test_9(self):
        assert to_roman(9) == "IX"

    # ---- tens ----
    def test_tens(self):
        assert to_roman(10) == "X"
        assert to_roman(20) == "XX"
        assert to_roman(30) == "XXX"
        assert to_roman(40) == "XL"
        assert to_roman(50) == "L"
        assert to_roman(60) == "LX"
        assert to_roman(70) == "LXX"
        assert to_roman(80) == "LXXX"
        assert to_roman(90) == "XC"

    # ---- hundreds ----
    def test_hundreds(self):
        assert to_roman(100) == "C"
        assert to_roman(200) == "CC"
        assert to_roman(300) == "CCC"
        assert to_roman(400) == "CD"
        assert to_roman(500) == "D"
        assert to_roman(600) == "DC"
        assert to_roman(700) == "DCC"
        assert to_roman(800) == "DCCC"
        assert to_roman(900) == "CM"

    # ---- thousands ----
    def test_thousands(self):
        assert to_roman(1000) == "M"
        assert to_roman(2000) == "MM"
        assert to_roman(3000) == "MMM"

    # ---- well-known examples ----
    def test_known_examples(self):
        assert to_roman(58) == "LVIII"
        assert to_roman(1990) == "MCMXC"
        assert to_roman(1994) == "MCMXCIV"
        assert to_roman(3999) == "MMMCMXCIX"

    # ---- boundaries ----
    def test_boundaries(self):
        assert to_roman(1) == "I"
        assert to_roman(3999) == "MMMCMXCIX"

    # ---- invalid inputs ----
    def test_zero(self):
        with pytest.raises(ValueError):
            to_roman(0)

    def test_negative(self):
        with pytest.raises(ValueError):
            to_roman(-1)
        with pytest.raises(ValueError):
            to_roman(-42)

    def test_too_large(self):
        with pytest.raises(ValueError):
            to_roman(4000)
        with pytest.raises(ValueError):
            to_roman(9999)

    def test_non_integer(self):
        with pytest.raises(ValueError):
            to_roman("1")
        with pytest.raises(ValueError):
            to_roman(3.14)
        with pytest.raises(ValueError):
            to_roman(None)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
