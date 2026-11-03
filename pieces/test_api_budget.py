from scripts.source.api_budget import worst_case_usd


def test_empty_stages():
    result = worst_case_usd({}, {})
    assert result == {
        "worst_case_usd": 0.0,
        "per_stage": {},
        "problems": [],
    }


def test_unknown_model():
    stages = {
        "stage1": {"model": "gpt-4", "max_tokens": 100, "prompt_tokens": 50}
    }
    catalog = {}
    result = worst_case_usd(stages, catalog)
    assert result == {
        "worst_case_usd": None,
        "per_stage": {"stage1": None},
        "problems": [{"code": "unknown-model", "stage": "stage1"}],
    }


def test_free_model():
    stages = {
        "stage1": {"model": "free-model", "max_tokens": 100, "prompt_tokens": 50}
    }
    catalog = {
        "free-model": {
            "paid": False,
            "price_in_usd_per_mtok": 10.0,
            "price_out_usd_per_mtok": 20.0,
        }
    }
    result = worst_case_usd(stages, catalog)
    assert result == {
        "worst_case_usd": 0.0,
        "per_stage": {"stage1": 0.0},
        "problems": [],
    }


def test_paid_model_unknown_price_in():
    stages = {
        "stage1": {"model": "gpt-4", "max_tokens": 100, "prompt_tokens": 50}
    }
    catalog = {
        "gpt-4": {
            "paid": True,
            "price_in_usd_per_mtok": None,
            "price_out_usd_per_mtok": 20.0,
        }
    }
    result = worst_case_usd(stages, catalog)
    assert result == {
        "worst_case_usd": None,
        "per_stage": {"stage1": None},
        "problems": [{"code": "unknown-price", "stage": "stage1"}],
    }


def test_paid_model_unknown_price_out():
    stages = {
        "stage1": {"model": "gpt-4", "max_tokens": 100, "prompt_tokens": 50}
    }
    catalog = {
        "gpt-4": {
            "paid": True,
            "price_in_usd_per_mtok": 10.0,
            "price_out_usd_per_mtok": None,
        }
    }
    result = worst_case_usd(stages, catalog)
    assert result == {
        "worst_case_usd": None,
        "per_stage": {"stage1": None},
        "problems": [{"code": "unknown-price", "stage": "stage1"}],
    }


def test_valid_paid_model():
    stages = {
        "stage1": {"model": "gpt-4", "max_tokens": 100, "prompt_tokens": 50}
    }
    catalog = {
        "gpt-4": {
            "paid": True,
            "price_in_usd_per_mtok": 10.0,
            "price_out_usd_per_mtok": 20.0,
        }
    }
    result = worst_case_usd(stages, catalog)
    expected = 50 * 10.0 / 1000000 + 100 * 20.0 / 1000000
    assert result == {
        "worst_case_usd": expected,
        "per_stage": {"stage1": expected},
        "problems": [],
    }


def test_multiple_valid_stages():
    stages = {
        "stage1": {"model": "gpt-4", "max_tokens": 100, "prompt_tokens": 50},
        "stage2": {"model": "claude", "max_tokens": 200, "prompt_tokens": 75},
    }
    catalog = {
        "gpt-4": {
            "paid": True,
            "price_in_usd_per_mtok": 10.0,
            "price_out_usd_per_mtok": 20.0,
        },
        "claude": {
            "paid": True,
            "price_in_usd_per_mtok": 5.0,
            "price_out_usd_per_mtok": 15.0,
        },
    }
    result = worst_case_usd(stages, catalog)
    cost1 = 50 * 10.0 / 1000000 + 100 * 20.0 / 1000000
    cost2 = 75 * 5.0 / 1000000 + 200 * 15.0 / 1000000
    assert result == {
        "worst_case_usd": cost1 + cost2,
        "per_stage": {"stage1": cost1, "stage2": cost2},
        "problems": [],
    }


def test_mixed_stages_with_error():
    stages = {
        "stage1": {"model": "unknown", "max_tokens": 100, "prompt_tokens": 50},
        "stage2": {"model": "gpt-4", "max_tokens": 200, "prompt_tokens": 75},
    }
    catalog = {
        "gpt-4": {
            "paid": True,
            "price_in_usd_per_mtok": 10.0,
            "price_out_usd_per_mtok": 20.0,
        }
    }
    result = worst_case_usd(stages, catalog)
    cost2 = 75 * 10.0 / 1000000 + 200 * 20.0 / 1000000
    assert result == {
        "worst_case_usd": None,
        "per_stage": {"stage1": None, "stage2": cost2},
        "problems": [{"code": "unknown-model", "stage": "stage1"}],
    }


def test_problems_in_stage_order():
    stages = {
        "stage1": {"model": "unknown", "max_tokens": 100, "prompt_tokens": 50},
        "stage2": {"model": "bad-price", "max_tokens": 200, "prompt_tokens": 75},
    }
    catalog = {
        "bad-price": {
            "paid": True,
            "price_in_usd_per_mtok": None,
            "price_out_usd_per_mtok": 20.0,
        }
    }
    result = worst_case_usd(stages, catalog)
    assert result["problems"] == [
        {"code": "unknown-model", "stage": "stage1"},
        {"code": "unknown-price", "stage": "stage2"},
    ]


def test_inputs_not_modified():
    stages = {
        "stage1": {"model": "gpt-4", "max_tokens": 100, "prompt_tokens": 50}
    }
    catalog = {
        "gpt-4": {
            "paid": True,
            "price_in_usd_per_mtok": 10.0,
            "price_out_usd_per_mtok": 20.0,
        }
    }
    stages_snapshot = {k: dict(v) for k, v in stages.items()}
    catalog_snapshot = {k: dict(v) for k, v in catalog.items()}
    worst_case_usd(stages, catalog)
    assert stages == stages_snapshot
    assert catalog == catalog_snapshot


def test_free_and_paid_mixed():
    stages = {
        "stage1": {"model": "free", "max_tokens": 100, "prompt_tokens": 50},
        "stage2": {"model": "gpt-4", "max_tokens": 200, "prompt_tokens": 75},
    }
    catalog = {
        "free": {
            "paid": False,
            "price_in_usd_per_mtok": 10.0,
            "price_out_usd_per_mtok": 20.0,
        },
        "gpt-4": {
            "paid": True,
            "price_in_usd_per_mtok": 10.0,
            "price_out_usd_per_mtok": 20.0,
        },
    }
    result = worst_case_usd(stages, catalog)
    cost2 = 75 * 10.0 / 1000000 + 200 * 20.0 / 1000000
    assert result == {
        "worst_case_usd": cost2,
        "per_stage": {"stage1": 0.0, "stage2": cost2},
        "problems": [],
    }
