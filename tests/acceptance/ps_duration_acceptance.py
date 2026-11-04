"""Locked acceptance tests for ps_duration (prod-smoke job ps-duration). Controller-owned: the coder never edits this file. Each test loads the module itself."""
import importlib.util
from pathlib import Path

import pytest

SOURCE = Path(__file__).parents[2] / "scripts/prod_smoke"
MODULE = SOURCE / "duration_text.py"


def load():
    assert MODULE.is_file(), "duration_text.py not written yet"
    spec = importlib.util.spec_from_file_location("duration_text_under_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_zero_is_all_zero():
    assert load().format_duration(0) == "0h 00m 00s"


def test_one_hour_two_minutes_five_seconds():
    assert load().format_duration(3725) == "1h 02m 05s"


def test_under_a_minute_keeps_zero_hours_and_minutes():
    m = load()
    assert m.format_duration(59) == "0h 00m 59s" and m.format_duration(61) == "0h 01m 01s"


def test_hours_are_not_capped_or_padded():
    m = load()
    assert m.format_duration(86399) == "23h 59m 59s"
    assert m.format_duration(360000) == "100h 00m 00s"


def test_negative_is_refused():
    m = load()
    with pytest.raises(ValueError):
        m.format_duration(-1)


def test_non_whole_numbers_are_refused():
    m = load()
    for bad in (1.5, "3", True):
        with pytest.raises(TypeError):
            m.format_duration(bad)
