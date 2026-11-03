"""The model catalog (Control Center P1, 2026-11-03): config/models.json, the curated list of models a UI may offer, beside the roster.

Each OpenRouter entry holds what is known about one model: its ID, the pinned provider (null unless pinned), paid, prices in USD per million
tokens, the context and output limits, the reasoning and temperature controls, the three text contracts (FILE blocks, SEARCH/REPLACE edit
blocks, a final verdict line) as verified, structured-output support, and when the contracts and the metadata were last read. Unknown is
null: nothing here is guessed. Each Claude Code entry names a model and its efforts (claude_code.MODELS and EFFORTS) and has no cost: the
subscription has no per-call USD price.

refresh_metadata reads GET /api/v1/models (no completion call) and fills the metadata. verify_model makes one small free call per contract
through the OpenRouter adapter, on the shared ledger like any other call, and records pass or fail. capabilities says what a UI may show as
selectable. The adapter's allow-list adds the catalog's free models (openrouter_call.roster_state); a paid catalog model needs a pinned
provider and prices, is reviewer-only, and is refused under the test profile.

Importing this module calls no model and reads no file."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CATALOG_PATH = REPO / "config" / "models.json"
CONTRACTS = ("writer_file_blocks", "repair_edit_blocks", "reviewer_verdict")
CONTRACT_KIND = {"writer_file_blocks": "writer", "repair_edit_blocks": "repair", "reviewer_verdict": "reviewer"}   # the check_reply kind of each
OPENROUTER_KEYS = ("model_id", "hosting_provider", "paid", "price_in_usd_per_mtok", "price_out_usd_per_mtok", "context_tokens",
                   "max_output_tokens", "reasoning", "temperature", "contracts", "structured_outputs", "verified_at", "metadata_at")
REASONING_KEYS = ("on_off", "efforts", "token_budget")
CLAUDE_CODE_KEYS = ("model", "via", "efforts", "cost")
TEMPERATURE_RANGE = {"min": 0.0, "max": 2.0}   # OpenRouter's documented temperature range, used when /models lists temperature
CALL_MAX_TOKENS = 16000             # a catalog model that is not on the roster: its output allowance per call (lowered to its max output when known)
CALL_TIMEOUT_S = 600
VERIFY_MAX_TOKENS = 2000            # a contract check is a few lines: a small call
VERIFY_TIMEOUT_S = 300
VERIFY_FILE = "hello.py"
VERIFY_BEFORE = "x = 1\n"
VERIFY_AFTER = "x = 2\n"


class CatalogError(ValueError):
    """config/models.json cannot be used, or a catalog action is refused. `code` is a stable name a UI or a test can match on; the message
    never holds a key value."""

    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code


def _oc():
    """scripts/source/openrouter_call.py, imported on first use (the same path rule as openrouter_call._shared_limits)."""
    source = str(REPO / "scripts" / "source")
    if source not in sys.path:
        sys.path.insert(0, source)
    import openrouter_call
    return openrouter_call


def _claude_models():
    source = str(REPO / "scripts" / "source")
    if source not in sys.path:
        sys.path.insert(0, source)
    import claude_code
    return claude_code


def _now(clock=time.time):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(clock()))


def _whole(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _bool_or_null(value):
    return value is None or isinstance(value, bool)


def _check_openrouter(i, entry):
    where = f"openrouter[{i}]"
    if not isinstance(entry, dict) or set(entry) != set(OPENROUTER_KEYS):
        raise CatalogError("entry_shape", f"{where}: needs exactly {', '.join(OPENROUTER_KEYS)}")
    mid = entry["model_id"]
    if not isinstance(mid, str) or not mid.strip() or mid != mid.strip():
        raise CatalogError("model_id", f"{where}: model_id must be a non-empty string")
    where = f"openrouter {mid}"
    pin = entry["hosting_provider"]
    if pin is not None and (not isinstance(pin, dict) or set(pin) != {"name", "slug"}
                            or not all(isinstance(v, str) and v.strip() for v in pin.values())):
        raise CatalogError("hosting_provider", f"{where}: hosting_provider must be null or {{name, slug}}")
    if not isinstance(entry["paid"], bool):
        raise CatalogError("paid", f"{where}: paid must be true or false")
    if entry["paid"] == mid.endswith(":free"):
        raise CatalogError("free_mismatch", f"{where}: a :free ID is never paid, and a model without :free is paid")
    for field in ("price_in_usd_per_mtok", "price_out_usd_per_mtok"):
        price = entry[field]
        if price is not None and (not _number(price) or price < 0):
            raise CatalogError("price", f"{where}: {field} must be null or a number of at least 0")
        if not entry["paid"] and price != 0:
            raise CatalogError("price", f"{where}: a :free model costs 0 ({field})")
    for field in ("context_tokens", "max_output_tokens"):
        if entry[field] is not None and (not _whole(entry[field]) or entry[field] <= 0):
            raise CatalogError("limit", f"{where}: {field} must be null or a positive whole number")
    reasoning = entry["reasoning"]
    if not isinstance(reasoning, dict) or set(reasoning) != set(REASONING_KEYS):
        raise CatalogError("reasoning", f"{where}: reasoning needs exactly {', '.join(REASONING_KEYS)}")
    efforts = reasoning["efforts"]
    if (not _bool_or_null(reasoning["on_off"]) or not _bool_or_null(reasoning["token_budget"]) or not isinstance(efforts, list)
            or not all(isinstance(e, str) and e.strip() for e in efforts) or len(set(efforts)) != len(efforts)):
        raise CatalogError("reasoning", f"{where}: on_off and token_budget are true, false or null; efforts is a list of distinct names")
    temp = entry["temperature"]
    if temp is not None and (not isinstance(temp, dict) or set(temp) != {"min", "max"} or not all(_number(v) for v in temp.values())
                             or temp["min"] > temp["max"]):
        raise CatalogError("temperature", f"{where}: temperature must be null or {{min, max}} with min <= max")
    contracts = entry["contracts"]
    if not isinstance(contracts, dict) or set(contracts) != set(CONTRACTS) or not all(_bool_or_null(v) for v in contracts.values()):
        raise CatalogError("contracts", f"{where}: contracts needs exactly {', '.join(CONTRACTS)}, each true, false or null")
    if not _bool_or_null(entry["structured_outputs"]):
        raise CatalogError("structured_outputs", f"{where}: structured_outputs must be true, false or null")
    stamps = entry["verified_at"]
    if stamps is not None and (not isinstance(stamps, dict) or not set(stamps) <= set(CONTRACTS)
                               or not all(isinstance(v, str) and v for v in stamps.values())):
        raise CatalogError("verified_at", f"{where}: verified_at must be null or {{contract: time}}")
    if entry["metadata_at"] is not None and not (isinstance(entry["metadata_at"], str) and entry["metadata_at"]):
        raise CatalogError("metadata_at", f"{where}: metadata_at must be null or a time")


def _check_claude_code(i, entry):
    cc = _claude_models()
    where = f"claude_code[{i}]"
    if not isinstance(entry, dict) or set(entry) != set(CLAUDE_CODE_KEYS):
        raise CatalogError("entry_shape", f"{where}: needs exactly {', '.join(CLAUDE_CODE_KEYS)}")
    if entry["model"] not in cc.MODELS or entry["via"] != "claude_code":
        raise CatalogError("claude_code", f"{where}: model must be one of {', '.join(cc.MODELS)} with via claude_code")
    if entry["efforts"] != list(cc.EFFORTS):
        raise CatalogError("claude_code", f"{where}: efforts must be claude_code.EFFORTS ({', '.join(cc.EFFORTS)})")
    if entry["cost"] is not None:
        raise CatalogError("claude_code_cost", f"{where}: cost is null (a subscription has no per-call USD price)")


def validate_catalog(data):
    """The catalog itself when it is valid; CatalogError with a stable code otherwise."""
    if not isinstance(data, dict) or data.get("schema_version") != 1 or set(data) != {"schema_version", "openrouter", "claude_code"}:
        raise CatalogError("schema", "the catalog holds exactly schema_version 1, openrouter and claude_code")
    if not isinstance(data["openrouter"], list) or not isinstance(data["claude_code"], list):
        raise CatalogError("schema", "openrouter and claude_code are lists")
    for i, entry in enumerate(data["openrouter"]):
        _check_openrouter(i, entry)
    for i, entry in enumerate(data["claude_code"]):
        _check_claude_code(i, entry)
    ids = [e["model_id"] for e in data["openrouter"]]
    names = [e["model"] for e in data["claude_code"]]
    if len(set(ids)) != len(ids) or len(set(names)) != len(names):
        raise CatalogError("duplicate", "a model is listed twice")
    return data


def load_catalog(path=None):
    """config/models.json (or `path`), validated."""
    try:
        data = json.loads(Path(path or CATALOG_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CatalogError("unreadable", f"the catalog cannot be read: {exc}") from exc
    return validate_catalog(data)


def save_catalog(data, path=None):
    """Writes a validated catalog atomically: a temporary file in the same folder, then a rename over the old one."""
    validate_catalog(data)
    target = Path(path or CATALOG_PATH)
    fd, tmp = tempfile.mkstemp(prefix=".models.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def entry_of(data, model_id):
    """The OpenRouter entry for `model_id`, or None."""
    return next((e for e in data["openrouter"] if e["model_id"] == model_id), None)


def call_settings(entry):
    """The adapter settings for a catalog model that is not on the roster: no temperature, no reasoning field and no sampling are sent (the
    provider's defaults), and the output allowance is CALL_MAX_TOKENS, lowered to the model's max output when that is known."""
    tokens = min(CALL_MAX_TOKENS, entry["max_output_tokens"] or CALL_MAX_TOKENS)
    return {"temperature": None, "reasoning": None, "max_tokens": tokens, "timeout_s": CALL_TIMEOUT_S, "sampling": None, "max_tokens_cap": tokens}


def allow_tables(profile, roster_models, path=None):
    """What the catalog adds to a profile's allow-list (openrouter_call.roster_state): {"free": {model: settings}, "paid": {model: (pin,
    prices per token, settings)}}. Roster models are left out (the roster's own tables keep them). A paid model is added only with a pinned
    provider and both prices, and never under the test profile."""
    free, paid = {}, {}
    for entry in load_catalog(path)["openrouter"]:
        mid = entry["model_id"]
        if mid in roster_models:
            continue
        if not entry["paid"]:
            free[mid] = call_settings(entry)
            continue
        prices = (entry["price_in_usd_per_mtok"], entry["price_out_usd_per_mtok"])
        if profile == "test" or entry["hosting_provider"] is None or None in prices:
            continue
        paid[mid] = (dict(entry["hosting_provider"]), {"prompt": prices[0] / 1e6, "completion": prices[1] / 1e6}, call_settings(entry))
    return {"free": free, "paid": paid}


# ---- metadata (GET /api/v1/models)

def _per_mtok(value):
    """An OpenRouter price (USD per token, often a string) as USD per million tokens; None when it is missing, unreadable or negative."""
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return None if price < 0 else round(price * 1e6, 6)


def _positive(value):
    return value if _whole(value) and value > 0 else None


def _apply_metadata(entry, model, stamp):
    """Fills one entry from its /models record. A pinned model keeps its prices: the /models price is not the pinned provider's."""
    pricing = model.get("pricing") if isinstance(model.get("pricing"), dict) else {}
    if entry["paid"] and entry["hosting_provider"] is None:
        entry["price_in_usd_per_mtok"] = _per_mtok(pricing.get("prompt"))
        entry["price_out_usd_per_mtok"] = _per_mtok(pricing.get("completion"))
    top = model.get("top_provider") if isinstance(model.get("top_provider"), dict) else {}
    entry["context_tokens"] = _positive(model.get("context_length"))
    entry["max_output_tokens"] = _positive(top.get("max_completion_tokens"))
    params = model.get("supported_parameters")
    if isinstance(params, list):
        params = set(p for p in params if isinstance(p, str))
        reasons = bool({"reasoning", "include_reasoning"} & params)
        entry["reasoning"]["on_off"] = reasons
        if not reasons:
            entry["reasoning"]["efforts"], entry["reasoning"]["token_budget"] = [], False
        entry["temperature"] = dict(TEMPERATURE_RANGE) if "temperature" in params else None
        entry["structured_outputs"] = bool({"structured_outputs", "response_format"} & params)
    entry["metadata_at"] = stamp


def refresh_metadata(transport, keys=None, path=None, clock=time.time):
    """Reads GET https://openrouter.ai/api/v1/models through `transport` (the adapter's (method, url, headers, body, timeout) shape) and fills
    each catalog entry it lists: prices (USD per token there, per million here), context_length, top_provider.max_completion_tokens, the
    supported parameters and metadata_at. The listing is public; a key, when given, is only sent as the Authorization header and is never
    printed, kept or written. Writes the catalog atomically. Returns {"updated": [...], "missing": [...]}."""
    oc = _oc()
    secrets = [v for v in dict(keys or {}).values() if v]
    headers = {"Authorization": "Bearer " + secrets[0]} if secrets else {}
    status, _, raw = transport("GET", f"{oc.API}/models", headers, None, 60)
    _, data, message = oc.parse_response(status, raw)
    listing = data.get("data") if isinstance(data, dict) else None
    if status != 200 or not isinstance(listing, list):
        raise CatalogError("metadata_unavailable", oc.redact(f"GET /models: HTTP {status} {message[:200]}", secrets))
    by_id = {m["id"]: m for m in listing if isinstance(m, dict) and isinstance(m.get("id"), str)}
    catalog = load_catalog(path)
    stamp, updated, missing = _now(clock), [], []
    for entry in catalog["openrouter"]:
        model = by_id.get(entry["model_id"])
        if model is None:
            missing.append(entry["model_id"])
            continue
        _apply_metadata(entry, model, stamp)
        updated.append(entry["model_id"])
    save_catalog(catalog, path)
    return {"updated": updated, "missing": missing}


# ---- contract verification (one small free call per contract)

VERIFY_SYSTEM = "You are a format check. Follow the instructions exactly and add nothing else."
VERIFY_USER = {
    "writer_file_blocks": (f"Write the file {VERIFY_FILE} containing exactly the line {VERIFY_AFTER.strip()}. Reply with one block only:\n"
                           f"FILE: {VERIFY_FILE}\n```python\n<the file>\n```"),
    "repair_edit_blocks": (f"The file {VERIFY_FILE} is:\n```python\n{VERIFY_BEFORE}```\nChange it to {VERIFY_AFTER.strip()} with one "
                           f"SEARCH/REPLACE block, exactly in this form and nothing else:\nFILE: {VERIFY_FILE}\n<<<<<<< SEARCH\n"
                           f"{VERIFY_BEFORE}=======\n{VERIFY_AFTER}>>>>>>> REPLACE"),
    "reviewer_verdict": ("Review this code: `x = 1`. It is correct. Write one short sentence, then end with this exact last line:\n"
                         "## Verdict: READY"),
}


def contract_problem(contract, text):
    """None when a checked reply also passes the repo's own parser for its contract, else the reason."""
    if contract == "writer_file_blocks":
        import nemotron_code
        files, refused = nemotron_code.parse_files(text)
        if refused or VERIFY_FILE not in files:
            return f"parse_files gave {sorted(files)} and refused {refused}"
        return None
    if contract == "repair_edit_blocks":
        import p1b_production
        changed, refusals = p1b_production.apply_edits(text, {VERIFY_FILE: VERIFY_BEFORE})
        if refusals or changed.get(VERIFY_FILE, "").strip() != VERIFY_AFTER.strip():
            return f"apply_edits refused {refusals}" if refusals else "apply_edits did not make the asked change"
        return None
    import p1b_routing
    return None if p1b_routing.verdict_of(text) in ("READY", "FIX FIRST") else "verdict_of found no verdict"


def verify_model(client, model_id, contract, path=None, clock=time.time):
    """One small free call through the OpenRouter adapter (`client`, an openrouter_call.OpenRouter: the shared ledger as for every call), with
    the contract's check_reply kind, then the repo parser for that contract (FILE blocks: nemotron_code.parse_files; edit blocks:
    p1b_production.apply_edits; verdict: p1b_routing.verdict_of). A reply the adapter rejects, or the parser refuses, is a fail; a pass needs
    both. Records true or false and its time under contracts and verified_at, written atomically. A call that never gave a reply (refused,
    budget, quota, provider down, a reply from another model) records nothing and its adapter error is raised. Paid models are not verified:
    a check spends nothing. Returns {"model_id", "contract", "passed", "reason"}."""
    if contract not in CONTRACTS:
        raise CatalogError("unknown_contract", f"{contract!r} is not one of {', '.join(CONTRACTS)}")
    entry = entry_of(load_catalog(path), model_id)
    if entry is None:
        raise CatalogError("unknown_model", f"{model_id!r} is not in the catalog")
    if entry["paid"]:
        raise CatalogError("paid_not_verified", f"{model_id} is paid: a contract check makes free calls only")
    oc = _oc()
    try:
        text, _ = client.call(model_id, VERIFY_SYSTEM, VERIFY_USER[contract], kind=CONTRACT_KIND[contract],
                              max_tokens=VERIFY_MAX_TOKENS, timeout_s=VERIFY_TIMEOUT_S)
        reason = contract_problem(contract, text)
    except oc.ReplyRejected as exc:
        reason = str(exc)
    catalog = load_catalog(path)            # read again: another check may have written since
    entry = entry_of(catalog, model_id)
    entry["contracts"][contract] = reason is None
    entry["verified_at"] = dict(entry["verified_at"] or {}, **{contract: _now(clock)})
    save_catalog(catalog, path)
    return {"model_id": model_id, "contract": contract, "passed": reason is None, "reason": reason}


# ---- what a UI may show

def capabilities(model_id, path=None):
    """Only the controls a UI may offer for a model: its reasoning modes (off/on, the efforts the adapter sends, a token budget), the
    temperature range, the max output and the verified contracts. A control that is unsupported or unknown is absent, never false. A Claude
    Code model (by its name) gives its efforts. CatalogError unknown_model for anything else."""
    data = load_catalog(path)
    cc = next((e for e in data["claude_code"] if e["model"] == model_id), None)
    if cc is not None:
        return {"model_id": model_id, "via": "claude_code", "efforts": list(cc["efforts"])}
    entry = entry_of(data, model_id)
    if entry is None:
        raise CatalogError("unknown_model", f"{model_id!r} is not in the catalog")
    caps = {"model_id": model_id, "via": "openrouter", "paid": entry["paid"]}
    sent = [e for e in _oc().EFFORT_VALUES if e not in (None, "off")]      # the efforts the adapter can send
    reasoning = {}
    if entry["reasoning"]["on_off"] is True:
        reasoning["modes"] = ["off", "on"]
    efforts = [e for e in entry["reasoning"]["efforts"] if e in sent]
    if efforts and entry["reasoning"]["on_off"] is not False:
        reasoning["efforts"] = efforts
    if entry["reasoning"]["token_budget"] is True:
        reasoning["token_budget"] = True
    if reasoning:
        caps["reasoning"] = reasoning
    if entry["temperature"] is not None:
        caps["temperature"] = dict(entry["temperature"])
    if entry["max_output_tokens"] is not None:
        caps["max_output_tokens"] = entry["max_output_tokens"]
    verified = [c for c in CONTRACTS if entry["contracts"][c] is True]
    if verified:
        caps["contracts"] = verified
    return caps
