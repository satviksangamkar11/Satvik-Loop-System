# api_budget: worst-case review budget (loop job ah-budget)

- KNOWN: The module is scripts/source/api_budget.py and uses only the Python standard library.
- KNOWN: worst_case_usd(stages, catalog) takes two dicts and returns a dict with exactly the keys worst_case_usd, per_stage and problems.
- KNOWN: stages maps a stage name to {"model": str, "max_tokens": int, "prompt_tokens": int}. catalog maps a model id to {"paid": bool, "price_in_usd_per_mtok": float or None, "price_out_usd_per_mtok": float or None}.
- KNOWN: For each stage: a model that is not in catalog gives per_stage[stage] = None and a problem {"code": "unknown-model", "stage": stage}.
- KNOWN: A model with paid False costs 0.0 for that stage.
- KNOWN: A paid model with either price None gives per_stage[stage] = None and a problem {"code": "unknown-price", "stage": stage}.
- KNOWN: Otherwise the stage costs prompt_tokens * price_in_usd_per_mtok / 1000000 + max_tokens * price_out_usd_per_mtok / 1000000.
- KNOWN: worst_case_usd is the sum of per_stage when no stage is None, else None. An empty stages dict gives worst_case_usd 0.0, per_stage {} and problems [].
- KNOWN: problems are listed in the order of the stages dict. The inputs are not changed.
- KNOWN: The module makes no network call and prints nothing.
