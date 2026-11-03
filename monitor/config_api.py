"""The configuration side of the API: the model pool (config/models.json), stage settings (config/roster.json stages blocks), operator
limits (config/limits.json), paid-budget validation, and the verify calls (one free call each). Every write goes through the engine's own
validators and is atomic. Secrets: key presence only, through openrouter_call.load_keys; a key value is never read into a response."""
import json
import re
import threading
import time
from pathlib import Path

import job_history
from api_budget import worst_case_usd
import limits_config
import model_catalog as mc
import openrouter_call as oc
import stage_config as sc
from .store import ApiError, atomic_write_json, home as satvik_home, read_json

MAX_POOL = 20
MIN_POOL = 10
TEXT_CONTRACTS = ("planner_json", "writer_file_blocks", "repair_edit_blocks", "repair_whole_file", "reviewer_verdict")
PRODUCTION_JOB_REVIEW_USD = oc.JOB_REVIEW_USD_BY_PROFILE["production"]   # the only job_review_usd the engine applies (p0_gates, fix_loop.load_job)
PROMPT_TOKENS_ASSUMED = 40000     # the prompt a worst-case paid call is priced at (about 120,000 characters): stated in every budget answer
REASONING_EFFORTS = ("off", "low", "medium", "high")
PROFILES = ("test", "production")
STAGE_ORDER = sc.STAGE_NAMES
_CONFIG_LOCK = threading.RLock()  # one writer of config/*.json in this process

_LISTING = {"fetch": None}        # tests replace the listing fetch (GET /api/v1/models) with a fake; None: the real one


# ---------------------------------------------------------------- the OpenRouter listing (public GET /models, no completion call)

def fetch_listing():
    """The OpenRouter model listing: a list of model dicts. 503 provider-unavailable when it cannot be read."""
    if _LISTING["fetch"] is not None:
        try:
            return _LISTING["fetch"]()
        except Exception as exc:
            raise ApiError(503, "provider-unavailable", f"the OpenRouter listing could not be read: {type(exc).__name__}")
    keys = oc.load_keys()
    headers = {"Authorization": "Bearer " + next(iter(keys.values()))} if keys else {}
    try:
        status, _, raw = oc.default_transport("GET", f"{oc.API}/models", headers, None, 60)
        _, data, message = oc.parse_response(status, raw)
    except Exception as exc:                               # a network failure: the listing is not available now
        raise ApiError(503, "provider-unavailable", f"the OpenRouter listing could not be read: {type(exc).__name__}")
    listing = data.get("data") if isinstance(data, dict) else None
    if status != 200 or not isinstance(listing, list):
        raise ApiError(503, "provider-unavailable", f"the OpenRouter listing returned HTTP {status}")
    return [m for m in listing if isinstance(m, dict) and isinstance(m.get("id"), str)]


def _listing_entry(model, stamp):
    """A catalog-shaped OpenRouter entry built from one listing record (prices, limits and the reasoning and temperature controls it lists)."""
    mid = model["id"]
    entry = {"model_id": mid, "hosting_provider": None, "paid": not mid.endswith(":free"), "price_in_usd_per_mtok": None,
             "price_out_usd_per_mtok": None, "context_tokens": None, "max_output_tokens": None,
             "reasoning": {"on_off": None, "efforts": [], "token_budget": None}, "temperature": None,
             "contracts": {c: None for c in mc.CONTRACTS}, "structured_outputs": None, "verified_at": None, "metadata_at": None}
    mc._apply_metadata(entry, model, stamp)
    if not entry["paid"]:
        entry["price_in_usd_per_mtok"] = entry["price_out_usd_per_mtok"] = 0
    return entry


# ---------------------------------------------------------------- Model shape (contract section 2)

def _state(value):
    return "verified" if value is True else "failed" if value is False else "unverified"


def _reasoning_records(home):
    if home is None:
        return {}
    return read_json(Path(home) / "api" / "reasoning_verified.json", {}) or {}


def _claude_code_contracts():
    """A Claude Code model's contracts. text_validated is true (its reply goes through the same text parsers), but a contract is verified only
    by a recorded POST /models/verify result for that model and contract. That call takes catalog (OpenRouter) models only and refuses a Claude
    Code model as not-in-catalog, so no such result exists and every text contract reads unverified."""
    return {**{c: "unverified" for c in TEXT_CONTRACTS}, "native_structured_output": "not-applicable"}


def _openrouter_contracts(entry):
    """An OpenRouter model's contracts from its catalog entry (true, false or null: verified, failed, unverified)."""
    c, structured = entry["contracts"], entry["structured_outputs"]
    return {"planner_json": "not-applicable", "writer_file_blocks": _state(c["writer_file_blocks"]),
            "repair_edit_blocks": _state(c["repair_edit_blocks"]), "repair_whole_file": _state(c["writer_file_blocks"]),
            "reviewer_verdict": _state(c["reviewer_verdict"]),
            "native_structured_output": "unverified" if structured is None else "verified" if structured else "not-applicable"}


def model_shape(entry, home=None, reasoning=None):
    """One Model (contract section 2) from a catalog entry (OpenRouter or Claude Code). Unknown stays null; efforts show only what the
    model's capabilities list; the reasoning mode and verified efforts come from POST /models/verify-reasoning when it has run."""
    if "model" in entry:                                   # a Claude Code entry: subscription, no USD, text-checked contracts
        return {"model_id": entry["model"], "via": "claude_code", "hosting_provider": None, "paid": None, "price_in_usd_per_mtok": None,
                "price_out_usd_per_mtok": None, "context_tokens": None, "max_output_tokens": None, "endpoint": None,
                "capabilities": {"reasoning_on_off": None, "efforts": list(entry["efforts"]), "token_budget": None, "temperature": None,
                                 "structured_outputs": None, "contracts": _claude_code_contracts(),
                                 "reasoning_mode": "unknown", "text_validated": True},
                "verified_at": None, "metadata_at": None, "note": "subscription: no USD"}
    rec = (reasoning if reasoning is not None else _reasoning_records(home).get(entry["model_id"])) or {}
    r = entry["reasoning"]
    efforts = list(rec.get("efforts_verified") or r["efforts"])
    if r["on_off"] is False:
        mode = "unsupported"
    else:
        mode = rec.get("reasoning_mode") or "unknown"
    stamps = entry["verified_at"] or {}
    verified_at = max(stamps.values()) if stamps else None
    provider = entry["hosting_provider"]
    shape = {
        "model_id": entry["model_id"], "via": "openrouter",
        "hosting_provider": provider["name"] if provider else None,
        "paid": entry["paid"], "price_in_usd_per_mtok": entry["price_in_usd_per_mtok"], "price_out_usd_per_mtok": entry["price_out_usd_per_mtok"],
        "context_tokens": entry["context_tokens"], "max_output_tokens": entry["max_output_tokens"],
        "capabilities": {
            "reasoning_on_off": r["on_off"], "efforts": efforts, "token_budget": r["token_budget"],
            "temperature": dict(entry["temperature"]) if entry["temperature"] else None,
            "structured_outputs": entry["structured_outputs"],
            "contracts": _openrouter_contracts(entry),
            "reasoning_mode": mode,
        },
        "verified_at": verified_at, "metadata_at": entry["metadata_at"],
        "endpoint": provider["slug"] if provider else None,
    }
    if entry["paid"]:
        shape["note"] = "paid: reviewer-only, production only"
    return shape


def list_models(home=None):
    catalog = mc.load_catalog()
    records = _reasoning_records(home)
    rows = [model_shape(e, home, records.get(e["model_id"])) for e in catalog["openrouter"]]
    rows += [model_shape(e) for e in catalog["claude_code"]]
    return {"models": rows, "index_ok": True}          # the pool is read from config/models.json: the history index is not used here


def search_models(q, home=None):
    """GET /models/search?q=: OpenRouter models whose ID contains q and that are not in the pool. Reads only."""
    needle = (q or "").strip().lower()
    if not needle:
        raise ApiError(400, "bad-query", "q must name part of a model ID")
    in_pool = {e["model_id"] for e in mc.load_catalog()["openrouter"]}
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    hits = [m for m in fetch_listing() if needle in m["id"].lower() and m["id"] not in in_pool]
    return {"results": [model_shape(_listing_entry(m, stamp)) for m in hits[:50]], "index_ok": True}


# ---------------------------------------------------------------- the pool: POST /models, DELETE /models/{id}

def _pool_size(catalog):
    return len(catalog["openrouter"])


def add_model(model_id, hosting_provider=None, home=None):
    """POST /models: 201 with the new Model (contracts unverified). Refused: already-in-pool, pool-full (max 20), paid-without-pin (a paid
    model needs its hosting provider {name, slug}), not-in-catalog (OpenRouter lists no such model)."""
    if not isinstance(model_id, str) or not model_id.strip() or model_id != model_id.strip():
        raise ApiError(400, "invalid-model", "model_id must be a model ID with no surrounding spaces")
    if hosting_provider is not None and (not isinstance(hosting_provider, dict) or set(hosting_provider) != {"name", "slug"}
                                         or not all(isinstance(v, str) and v.strip() for v in hosting_provider.values())):
        raise ApiError(400, "invalid-model", "hosting_provider must be {name, slug}")
    with _CONFIG_LOCK:
        catalog = mc.load_catalog()
        if mc.entry_of(catalog, model_id) is not None or any(e["model"] == model_id for e in catalog["claude_code"]):
            raise ApiError(409, "already-in-pool", f"{model_id} is already in the pool")
        if _pool_size(catalog) >= MAX_POOL:
            raise ApiError(422, "pool-full", f"the pool holds at most {MAX_POOL} models", {"max": MAX_POOL})
        listed = next((m for m in fetch_listing() if m["id"] == model_id), None)
        if listed is None:
            raise ApiError(422, "not-in-catalog", f"{model_id} is not listed by OpenRouter")
        entry = _listing_entry(listed, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        if entry["paid"]:
            if hosting_provider is None:
                raise ApiError(422, "paid-without-pin", f"{model_id} is paid: it needs a pinned hosting provider {{name, slug}}")
            entry["hosting_provider"] = dict(hosting_provider)
            entry["price_in_usd_per_mtok"] = entry["price_out_usd_per_mtok"] = None   # a pinned model's prices are the provider's: kept from the roster
        catalog["openrouter"].append(entry)
        mc.save_catalog(catalog)
    return 201, model_shape(entry, home)


def _stage_users(model_id):
    """[{profile, stage}] for every stage of either profile that resolves to model_id (read only)."""
    users = []
    for name in PROFILES:
        try:
            data = sc._profile_data(name)[1]
            table = sc._table(data)
        except (oc.RosterRefused, sc.StageRefused, KeyError):
            continue
        for stage in STAGE_ORDER:
            if table[stage]["model"] == model_id:
                users.append({"profile": name, "stage": stage})
    return users


def remove_model(model_id, home=None):
    """DELETE /models/{id}: 204. 404 for an ID not in the pool, 409 model-in-use (details.stages), 422 pool-min (min 10)."""
    with _CONFIG_LOCK:
        catalog = mc.load_catalog()
        if mc.entry_of(catalog, model_id) is None:
            raise ApiError(404, "unknown-model", f"{model_id} is not in the pool")
        users = _stage_users(model_id)
        if users:
            raise ApiError(409, "model-in-use", f"{model_id} is used by a stage", {"stages": users})
        if _pool_size(catalog) - 1 < MIN_POOL:
            raise ApiError(422, "pool-min", f"the pool keeps at least {MIN_POOL} models", {"min": MIN_POOL})
        catalog["openrouter"] = [e for e in catalog["openrouter"] if e["model_id"] != model_id]
        mc.save_catalog(catalog)
    return 204, None


# ---------------------------------------------------------------- verify calls (one free call each, through the adapter)

def make_client(home):
    """An OpenRouter client on the shared ledger for one verify action. 503 provider-unavailable when no key is present (presence only)."""
    keys = oc.load_keys()
    if "free" not in keys:
        raise ApiError(503, "provider-unavailable", "no OpenRouter key is present")
    return oc.OpenRouter(keys, Path(home) / "ledger")


def _with_client(home, label, fn):
    client = make_client(home)
    try:
        client.own_job(label, Path(home))
        return fn(client)
    except oc.OpenRouterError as exc:
        if isinstance(exc, (oc.ModelRefused, oc.ModelDenied)):
            raise ApiError(422, "not-in-catalog", str(exc))
        raise ApiError(503, "provider-unavailable", f"{type(exc).__name__}: {exc}")
    except mc.CatalogError as exc:
        code = {"unknown_model": "not-in-catalog", "paid_not_verified": "paid-not-verified"}.get(exc.code, "bad-request")
        raise ApiError(422 if code != "bad-request" else 400, code, str(exc))
    finally:
        client.release_job()


def verify_contract(home, model_id, contract):
    """POST /models/verify: one free call for one contract. {result: pass|fail, verified_at}."""
    if contract not in mc.CONTRACTS:
        raise ApiError(400, "unknown-contract", f"contract must be one of {', '.join(mc.CONTRACTS)}")
    outcome = _with_client(home, "api-verify", lambda client: mc.verify_model(client, model_id, contract))
    stamp = (mc.entry_of(mc.load_catalog(), model_id)["verified_at"] or {}).get(contract)
    return {"result": "pass" if outcome["passed"] else "fail", "verified_at": stamp}


def refresh_prices(home):
    """POST /models/refresh: one GET to OpenRouter's public listing; the pool's prices and metadata are updated."""
    try:
        res = mc.refresh_metadata(oc.default_transport, oc.load_keys())
    except mc.CatalogError as exc:
        raise ApiError(503, "provider-unavailable", str(exc))
    except Exception as exc:
        raise ApiError(503, "provider-unavailable", f"{type(exc).__name__}")
    return {"updated": len(res["updated"]), "metadata_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def verify_reasoning(home, model_id, efforts):
    """POST /models/verify-reasoning: at most one free call per effort. An effort whose call returns a reply is verified. reasoning_mode:
    optional (off and an on-effort both work), mandatory (an on-effort works, off is refused), unknown otherwise. Recorded under
    SATVIK_HOME/api/reasoning_verified.json (the catalog's schema is the engine's and is not changed)."""
    if not isinstance(efforts, list) or not efforts or len(efforts) > len(REASONING_EFFORTS) or len(set(efforts)) != len(efforts) \
            or not all(e in REASONING_EFFORTS for e in efforts):
        raise ApiError(400, "bad-efforts", f"efforts: a non-empty list of distinct values from {', '.join(REASONING_EFFORTS)}")
    entry = mc.entry_of(mc.load_catalog(), model_id)
    if entry is None:
        raise ApiError(422, "not-in-catalog", f"{model_id} is not in the pool")
    if entry["paid"]:
        raise ApiError(422, "paid-not-verified", f"{model_id} is paid: a verification makes free calls only")
    verified, refused = [], []

    def probe(client):
        for effort in efforts:
            try:
                client.call(model_id, mc.VERIFY_SYSTEM, "Reply with the single word OK.", kind="text",
                            max_tokens=mc.VERIFY_MAX_TOKENS, timeout_s=mc.VERIFY_TIMEOUT_S, effort=effort)
                verified.append(effort)
            except (oc.ReplyRejected, oc.ModelDenied, oc.ModelRefused):
                refused.append(effort)
    _with_client(home, "api-verify-reasoning", probe)
    on = [e for e in verified if e != "off"]
    if on and "off" in verified:
        mode = "optional"
    elif on and "off" in refused:
        mode = "mandatory"
    else:
        mode = "unknown"
    rec_path = Path(home) / "api" / "reasoning_verified.json"
    records = read_json(rec_path, {}) or {}
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    records[model_id] = {"reasoning_mode": mode, "efforts_verified": [e for e in efforts if e in verified], "verified_at": stamp}
    with _CONFIG_LOCK:
        atomic_write_json(rec_path, records)
    return {"reasoning_mode": mode, "efforts_verified": [e for e in efforts if e in verified]}


# ---------------------------------------------------------------- stage settings: GET/PUT /stages, POST /stages/validate

def _profile(name):
    name = name or oc.active_profile()
    if name not in PROFILES:
        raise ApiError(400, "bad-profile", "profile is test or production")
    return name


def _roster_revision():
    return oc.load_roster_data().get("revision", 0)


def _roster_stages_now(name):
    try:
        return sc._profile_data(name)[1]
    except (oc.RosterRefused, sc.StageRefused) as exc:
        raise ApiError(503, "engine-unavailable", str(exc))


def stages_view(profile=None):
    """GET /stages: the ten stages of a profile as the engine resolves them, with model_selectable and the inheritance of a stage that takes
    its model from the job as it runs."""
    name = _profile(profile)
    try:
        table = sc.resolve_stages(name)
    except (oc.RosterRefused, sc.StageRefused) as exc:
        raise ApiError(503, "engine-unavailable", str(exc))
    rows = []
    for stage in STAGE_ORDER:
        e = table[stage]
        role = sc.STAGES[stage][0]
        model = e["model"]
        required, fallback = sc.CONTRACT_OF[stage]
        row = {"stage": stage, "model": model, "via": e["via"], "model_selectable": bool(e["model_selectable"]), "effort": e["effort"],
               "settings": sc.thaw(e["settings"]), "required_contract": required, "fallback_contract": fallback}
        if stage in sc.INHERITED:
            row["model"], row["via"], row["model_selectable"] = None, None, False
            row["inherits"] = "triggering reviewer" if stage == "reviewer_reask" else "current writer (lineage)"
        else:
            row["source"] = f"role:{role}"
        if model == oc.NON_AUTHOR_WRITER:
            row["model"], row["model_selectable"] = None, False
            row["via"] = None
            row["inherits"] = "non-author writer"
            if name == "test":
                row["note"] = "fixed to the non-author writer in the test profile only"
        if model == "fake":                             # the test profile's planner: no model, no via
            row["via"], row["note"] = None, "planning refused under test"
        if model == "off":
            row["note"] = "off under test" if name == "test" else "off"
        if stage == "writer_b_write":
            row["note"] = "also the settings Reviewer B uses when it writes as fallback"
        rows.append(row)
    return {"profile": name, "revision": _roster_revision(), "stages": rows}


def _as_changes(changes):
    """A list of {stage, ...overrides} (the contract's form) or a {stage: overrides} object, as the engine's {stage: overrides}."""
    if isinstance(changes, list):
        out = {}
        for item in changes:
            if not isinstance(item, dict) or not isinstance(item.get("stage"), str):
                raise ApiError(400, "bad-stages", "each change names its stage")
            out[item["stage"]] = {k: v for k, v in item.items() if k != "stage"}
        return out
    if isinstance(changes, dict):
        return changes
    raise ApiError(400, "bad-stages", "changes is a list of {stage, ...} or an object of {stage: overrides}")


def stage_problems(name, changes):
    """The engine's validation of a stage change, the pool rule (a stage's model must be a pool model of its own via: model-not-selectable),
    and the API's max_tokens rule (over-max-output: max_tokens must not exceed the model's max_output_tokens, when the catalog knows it).
    [] when it can be used."""
    problems = sc.validate(changes, name)
    if problems:
        return problems
    data = _roster_stages_now(name)
    merged = sc._merge(sc._stages_block(data), changes)
    table = sc._table(dict(data, stages=merged))
    catalog_all = mc.load_catalog()
    catalog = {e["model_id"]: e for e in catalog_all["openrouter"]}
    pool_claude = {e["model"] for e in catalog_all["claude_code"]}
    for stage, change in changes.items():
        if stage not in sc.STAGES or sc.STAGES[stage][0] is None or not isinstance(change, dict) or "model" not in change:
            continue
        model, via = table[stage]["model"], table[stage]["via"]
        pool = pool_claude if via == "claude_code" else set(catalog)
        if model not in pool and model not in oc.SENTINELS:
            problems.append({"code": "model-not-selectable", "stage": stage,
                             "message": f"{stage}: {model} is not in the model pool for {via}"})
    for stage in STAGE_ORDER:
        e = table[stage]
        cap = (catalog.get(e["model"]) or {}).get("max_output_tokens") if e["via"] == "openrouter" else None
        tokens = e["settings"].get("max_tokens")
        if cap is not None and isinstance(tokens, int) and tokens > cap:
            problems.append({"code": "over-max-output", "stage": stage,
                             "message": f"{stage}: max_tokens {tokens} is above the model's max_output_tokens {cap}"})
    return problems


def _contract_problems(name, changes):
    """contract-not-verified for each changed stage whose model does not have the stage's required contract verified, in the state GET /models
    reports (one source for both). Sentinels (fake, non-author writer, off) have no contract to check."""
    data = _roster_stages_now(name)
    table = sc._table(dict(data, stages=sc._merge(sc._stages_block(data), changes)))
    catalog = mc.load_catalog()
    out = []
    for stage, change in changes.items():
        if stage not in sc.STAGES or not isinstance(change, dict) or stage not in table:
            continue
        model, via = table[stage]["model"], table[stage]["via"]
        if not isinstance(model, str) or model in oc.SENTINELS:
            continue
        required = sc.CONTRACT_OF[stage][0]
        if via == "claude_code":
            states = _claude_code_contracts()
        else:
            entry = mc.entry_of(catalog, model)
            states = _openrouter_contracts(entry) if entry is not None else {}
        state = states.get(required, "unverified")
        if state != "verified":
            out.append({"code": "contract-not-verified", "stage": stage,
                        "message": f"{stage}: {model} has {required} {state}; the stage needs it verified (POST /models/verify)"})
    return out


def validate_stages(profile, changes):
    """POST /stages/validate: {valid: true, problems: []}; 400 with details.problems otherwise. A change is refused as contract-not-verified when
    its model's required contract is not verified."""
    name = _profile(profile)
    changes = _as_changes(changes)
    problems = stage_problems(name, changes) or _contract_problems(name, changes)
    if problems:
        raise ApiError(400, problems[0]["code"], problems[0]["message"], {"problems": problems})
    return {"valid": True, "problems": []}


def _write_roster_checked(raw):
    """Writes config/roster.json only after the engine loads the new text cleanly (a temp file beside it, then a rename)."""
    target = Path(oc.ROSTER_PATH)
    tmp = target.with_name(target.name + f".{time.time_ns()}.new")
    atomic_write_json(tmp, raw)
    try:
        oc.load_roster_data(tmp)
    except oc.RosterRefused as exc:
        tmp.unlink(missing_ok=True)
        raise ApiError(400, getattr(exc, "code", "bad-stages"), str(exc))
    tmp.replace(target)


def save_stages(profile, revision, changes):
    """PUT /stages: writes the profile's stages block in config/roster.json and bumps the roster revision. Affects future jobs only.
    409 stale-revision when `revision` is not the current one; 400 with the problems when the change cannot be used."""
    name = _profile(profile)
    changes = _as_changes(changes)
    with _CONFIG_LOCK:
        current = _roster_revision()
        if revision != current:
            raise ApiError(409, "stale-revision", f"the stage settings are at revision {current}, not {revision}", {"revision": current})
        problems = stage_problems(name, changes)
        if problems:
            raise ApiError(400, problems[0]["code"], problems[0]["message"], {"problems": problems})
        raw = read_json(oc.ROSTER_PATH)
        if not isinstance(raw, dict) or name not in raw.get("profiles", {}):
            raise ApiError(503, "engine-unavailable", "config/roster.json cannot be read")
        block = raw["profiles"][name]
        block["stages"] = sc._merge(block.get("stages") or {}, changes)
        raw["revision"] = current + 1
        _write_roster_checked(raw)
    return 200, {"revision": current + 1}


# ---------------------------------------------------------------- paid budget: POST /budget/validate

def _paid_stages(name, stages):
    """The stages whose model the catalog lists as paid (their calls cost USD), in stage order."""
    data = _roster_stages_now(name)
    table = sc._table(dict(data, stages=sc._merge(sc._stages_block(data), stages)))
    paid = {e["model_id"] for e in mc.load_catalog()["openrouter"] if e["paid"]}
    return [stage for stage in STAGE_ORDER if table[stage]["via"] == "openrouter" and table[stage]["model"] in paid]


def _worst_case(name, stages):
    """(worst_case_usd or None, problems, the table) for a stage change under the active profile. Uses api_budget.worst_case_usd."""
    data = _roster_stages_now(name)
    table = sc._table(dict(data, stages=sc._merge(sc._stages_block(data), stages)))
    catalog_entries = {e["model_id"]: e for e in mc.load_catalog()["openrouter"]}
    catalog = {mid: {"paid": e["paid"], "price_in_usd_per_mtok": e["price_in_usd_per_mtok"],
                     "price_out_usd_per_mtok": e["price_out_usd_per_mtok"]} for mid, e in catalog_entries.items()}
    calls, paid_test = {}, []
    for stage in STAGE_ORDER:
        e = table[stage]
        if e["via"] != "openrouter" or e["model"] in oc.SENTINELS or not isinstance(e["model"], str):
            continue                                       # subscription (claude_code) and sentinels cost no USD
        entry = catalog.get(e["model"]) or {}
        if entry.get("paid") and name == "test":
            paid_test.append(stage)
        tokens = e["settings"].get("max_tokens")
        if not isinstance(tokens, int):                    # no max_tokens set: the catalog's output limit, else the adapter's own cap
            tokens = (catalog_entries.get(e["model"]) or {}).get("max_output_tokens") or mc.CALL_MAX_TOKENS
        calls[stage] = {"model": e["model"], "max_tokens": tokens, "prompt_tokens": PROMPT_TOKENS_ASSUMED}
    return worst_case_usd(calls, catalog), paid_test


def validate_budget(profile, stages, paid_budget_usd):
    """POST /budget/validate: the worst-case USD of the stages' paid calls (the prompt priced at PROMPT_TOKENS_ASSUMED, the whole output
    allowance at the catalog or roster price). 422 paid-in-test under the test profile; 400 budget-over-cap when the worst case is above the
    paid budget or the job review ceiling (limits.job_review_usd). Reads only."""
    name = _profile(profile)
    if isinstance(paid_budget_usd, bool) or not isinstance(paid_budget_usd, (int, float)) or paid_budget_usd < 0:
        raise ApiError(400, "bad-budget", "paid_budget_usd is a number of at least 0")
    changes = _as_changes(stages or {})
    problems = stage_problems(name, changes)
    if problems:
        raise ApiError(400, problems[0]["code"], problems[0]["message"], {"problems": problems})
    paid_stages = _paid_stages(name, changes)
    if name == "test" and (paid_stages or paid_budget_usd > 0):
        raise ApiError(422, "paid-in-test", "test profile: free only", {"stage": (paid_stages or [None])[0]})
    worst, _paid = _worst_case(name, changes)
    cap = limits_config.job_review_usd()
    if worst["worst_case_usd"] is None:
        return {"valid": False, "worst_case_usd": None, "problems": worst["problems"], "assumptions": {"prompt_tokens": PROMPT_TOKENS_ASSUMED}}
    limit = min(float(paid_budget_usd), cap)
    if worst["worst_case_usd"] > limit + 1e-12:
        raise ApiError(400, "budget-over-cap", f"worst case ${worst['worst_case_usd']:.4f} is above ${limit:.4f}",
                       {"worst_case_usd": worst["worst_case_usd"], "paid_budget_usd": float(paid_budget_usd), "job_review_usd": cap})
    return {"valid": True, "worst_case_usd": worst["worst_case_usd"], "problems": [], "assumptions": {"prompt_tokens": PROMPT_TOKENS_ASSUMED}}


# ---------------------------------------------------------------- limits: PUT /system/limits

def update_limits(revision, changes, slots_in_use=0):
    """PUT /system/limits: {revision} must be the current revision (409 stale-revision); the values are range-checked (422 out-of-range).
    A change to sandbox_slots waits for no job holding a sandbox slot (409 jobs-running). New starts only: the engine reads the file at
    each start. A job_review_usd other than the engine's production value is refused (422 not-applied-by-engine): the production budget gate
    requires exactly that value, so another one would be stored and not used. Returns (200, {revision: n+1})."""
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise ApiError(400, "bad-revision", "revision is a whole number")
    if not isinstance(changes, dict):
        raise ApiError(400, "bad-limits", "the limits change is an object")
    unknown = set(changes) - set(limits_config.DEFAULTS)
    if unknown:
        raise ApiError(400, "bad-limits", f"unknown limit(s): {', '.join(sorted(unknown))}")
    with _CONFIG_LOCK:
        cur = limits_config.load()
        if revision != cur["revision"]:
            raise ApiError(409, "stale-revision", f"the limits are at revision {cur['revision']}, not {revision}", {"revision": cur["revision"]})
        new = dict(cur, **changes)
        try:
            limits_config._valid(new)
        except limits_config.LimitsError as exc:
            raise ApiError(422, "out-of-range", str(exc))
        if "job_review_usd" in changes and changes["job_review_usd"] != PRODUCTION_JOB_REVIEW_USD:
            raise ApiError(422, "not-applied-by-engine",
                           f"job_review_usd {changes['job_review_usd']} would not apply: the engine's production budget gate requires exactly "
                           f"{PRODUCTION_JOB_REVIEW_USD} (p0_gates, fix_loop.load_job), so only that value is accepted until the engine reads this setting",
                           {"required": PRODUCTION_JOB_REVIEW_USD, "given": changes["job_review_usd"]})
        if "sandbox_slots" in changes and changes["sandbox_slots"] != cur["sandbox_slots"] and slots_in_use:
            raise ApiError(409, "jobs-running", "sandbox_slots changes only when no job holds a sandbox slot", {"holding": slots_in_use})
        new["revision"] = cur["revision"] + 1
        limits_config.save(new)
    return 200, {"revision": new["revision"]}
