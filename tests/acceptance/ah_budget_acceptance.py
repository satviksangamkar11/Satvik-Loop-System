"""Locked acceptance tests for api_budget (loop job ah-budget). Controller-owned: the coder never edits this file."""
import importlib.util
from pathlib import Path

MODULE = Path(__file__).parents[2] / "scripts/source/api_budget.py"


def load():
    assert MODULE.is_file(), "api_budget.py not written yet"
    spec = importlib.util.spec_from_file_location("api_budget_under_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_free_stages_cost_zero():
    m = load()
    r = m.worst_case_usd({'a': {'model': 'f', 'max_tokens': 1000, 'prompt_tokens': 500}}, {'f': {'paid': False, 'price_in_usd_per_mtok': None, 'price_out_usd_per_mtok': None}})
    assert r == {'worst_case_usd': 0.0, 'per_stage': {'a': 0.0}, 'problems': []}


def test_paid_stage_uses_both_prices():
    m = load()
    r = m.worst_case_usd({'r': {'model': 'g', 'max_tokens': 16000, 'prompt_tokens': 30000}}, {'g': {'paid': True, 'price_in_usd_per_mtok': 0.15, 'price_out_usd_per_mtok': 0.5}})
    assert abs(r['per_stage']['r'] - (30000 * 0.15 + 16000 * 0.5) / 1e6) < 1e-12 and abs(r['worst_case_usd'] - r['per_stage']['r']) < 1e-12


def test_unknown_model_and_unknown_price():
    m = load()
    r = m.worst_case_usd({'x': {'model': 'nope', 'max_tokens': 1, 'prompt_tokens': 1}, 'y': {'model': 'g', 'max_tokens': 1, 'prompt_tokens': 1}}, {'g': {'paid': True, 'price_in_usd_per_mtok': None, 'price_out_usd_per_mtok': 0.5}})
    assert r['worst_case_usd'] is None and r['per_stage'] == {'x': None, 'y': None}
    assert r['problems'] == [{'code': 'unknown-model', 'stage': 'x'}, {'code': 'unknown-price', 'stage': 'y'}]


def test_empty_stages():
    m = load()
    assert m.worst_case_usd({}, {}) == {'worst_case_usd': 0.0, 'per_stage': {}, 'problems': []}
