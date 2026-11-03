"""Negative control for api_budget (loop job ah-budget): always-free. The acceptance tests must fail on it."""


def worst_case_usd(stages, catalog):
    return {'worst_case_usd': 0.0, 'per_stage': {s: 0.0 for s in stages}, 'problems': []}
