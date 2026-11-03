"""Locked acceptance tests for api_idempotency (loop job ah-idempotency). Controller-owned: the coder never edits this file."""
import importlib.util
from pathlib import Path

MODULE = Path(__file__).parents[2] / "scripts/source/api_idempotency.py"


def load():
    assert MODULE.is_file(), "api_idempotency.py not written yet"
    spec = importlib.util.spec_from_file_location("api_idempotency_under_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_first_write_wins(tmp_path):
    m = load()
    s = m.IdempotencyStore(tmp_path / 'idem', clock=lambda: 100.0)
    assert s.put('key-0001', {'a': 1}) == {'a': 1}
    assert s.put('key-0001', {'a': 2}) == {'a': 1}
    assert s.get('key-0001') == {'a': 1}


def test_missing_and_expired(tmp_path):
    m = load()
    now = [100.0]
    s = m.IdempotencyStore(tmp_path / 'idem', ttl_s=10, clock=lambda: now[0])
    assert s.get('key-0002') is None
    s.put('key-0002', {'v': 1})
    now[0] = 111.0
    assert s.get('key-0002') is None
    assert s.put('key-0002', {'v': 2}) == {'v': 2}


def test_bad_keys_raise(tmp_path):
    m = load()
    import pytest
    s = m.IdempotencyStore(tmp_path / 'idem')
    for bad in ['short', 'x' * 129, 'has space!', '../escape-key']:
        with pytest.raises(ValueError):
            s.put(bad, {})
        with pytest.raises(ValueError):
            s.get(bad)


def test_survives_a_new_instance(tmp_path):
    m = load()
    a = m.IdempotencyStore(tmp_path / 'idem', clock=lambda: 5.0)
    a.put('key-0003', {'ok': True})
    b = m.IdempotencyStore(tmp_path / 'idem', clock=lambda: 6.0)
    assert b.get('key-0003') == {'ok': True}
