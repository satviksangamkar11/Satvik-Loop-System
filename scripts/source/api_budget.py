def worst_case_usd(stages, catalog):
    per_stage = {}
    problems = []

    for stage, info in stages.items():
        model = info["model"]
        max_tokens = info["max_tokens"]
        prompt_tokens = info["prompt_tokens"]

        if model not in catalog:
            per_stage[stage] = None
            problems.append({"code": "unknown-model", "stage": stage})
            continue

        model_info = catalog[model]

        if not model_info["paid"]:
            per_stage[stage] = 0.0
            continue

        price_in = model_info["price_in_usd_per_mtok"]
        price_out = model_info["price_out_usd_per_mtok"]

        if price_in is None or price_out is None:
            per_stage[stage] = None
            problems.append({"code": "unknown-price", "stage": stage})
            continue

        cost = (
            prompt_tokens * price_in / 1000000
            + max_tokens * price_out / 1000000
        )
        per_stage[stage] = cost

    if any(v is None for v in per_stage.values()):
        worst_case = None
    else:
        worst_case = sum(per_stage.values()) if per_stage else 0.0

    return {
        "worst_case_usd": worst_case,
        "per_stage": per_stage,
        "problems": problems,
    }
