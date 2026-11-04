"""Stage configuration (Control Center P1, 2026-11-03): the ten stage types of a production job, how each resolves from config/roster.json,
the frozen per-job snapshot, and the one safe change to it (apply_future_stage).

The roster stays the one place a model is named. Each profile has a "stages" block: {stage: overrides}. An override may set model, via,
effort and settings keys; a key it leaves out comes from the stage's role (its roster entry, effort_by_mode for an OpenRouter role, and the
profile's repair_max_tokens as the max_tokens of a repair mode). Two stages take their model from the job as it runs and refuse a model:
reviewer_reask (the reviewer that gave the FIX FIRST) and review_fix_repair (the writer that last wrote the finding's file, by lineage).

  stage              role        mode          model
  planner            planner     -             role
  writer_a_write     writer_a    write         role
  writer_a_repair    writer_a    repair        role
  writer_b_write     writer_b    write         role (when writer_b is down: reviewer_b's model, see call_spec)
  writer_b_repair    writer_b    repair        role
  reviewer_b         reviewer_b  review        role
  reviewer_a         reviewer_a  review        role (non_author_writer: the writer that did not write the code)
  reviewer_reask     -           review-reask  the triggering reviewer (not selectable)
  specialist_repair  repair      repair        role
  review_fix_repair  -           repair        the current writer by lineage (not selectable)

A job freezes its table once at start (snapshot, written to _loop/<job>/stage_snapshot.json). Every later model call of that job resolves
through that file, so a roster edit during the job changes nothing for it. apply_future_stage writes the next revision of the file, and only
for a stage the job has not started; the change is read when that stage next starts. No call that is in flight is ever changed.

The ladder is unchanged: it still names models by role. A writer's model in the ladder (its lineage name) is its write stage's model; a
repair stage with another model is served by that model and recorded under it in history/call_NN.json.

Importing this module calls no model and reads no file."""
import copy
import hashlib
import json
import math
import os
import threading
import time
from pathlib import Path
from types import MappingProxyType

import openrouter_call as oc

SNAPSHOT_FILE = "stage_snapshot.json"
STARTED_FILE = "stage_started.json"
LOCK_FILE = ".stage_snapshot.lock"
SNAPSHOT_SCHEMA = 1
STAGES = {                                  # stage -> (role, mode); role None: taken from the job as it runs
    "planner": ("planner", None),
    "writer_a_write": ("writer_a", "write"),
    "writer_a_repair": ("writer_a", "repair"),
    "writer_b_write": ("writer_b", "write"),
    "writer_b_repair": ("writer_b", "repair"),
    "reviewer_b": ("reviewer_b", "review"),
    "reviewer_a": ("reviewer_a", "review"),
    "reviewer_reask": (None, "review-reask"),
    "specialist_repair": ("repair", "repair"),
    "review_fix_repair": (None, "repair"),
}
STAGE_NAMES = tuple(STAGES)
INHERITED = ("reviewer_reask", "review_fix_repair")     # model and via come from the job as it runs: never selectable
# The output contract each stage needs from its model (docs/API_CONTRACT.md: contract, contract_state). The UI offers only models whose
# required contract is verified. fallback: the contract of the whole-file repair a repair stage falls back to (None: no fallback).
CONTRACT_OF = {                                  # stage -> (required_contract, fallback_contract)
    "planner": ("planner_json", None),
    "writer_a_write": ("writer_file_blocks", None),
    "writer_b_write": ("writer_file_blocks", None),
    "writer_a_repair": ("repair_edit_blocks", "repair_whole_file"),
    "writer_b_repair": ("repair_edit_blocks", "repair_whole_file"),
    "review_fix_repair": ("repair_edit_blocks", "repair_whole_file"),
    "specialist_repair": ("repair_edit_blocks", "repair_whole_file"),
    "reviewer_b": ("reviewer_verdict", None),
    "reviewer_a": ("reviewer_verdict", None),
    "reviewer_reask": ("reviewer_verdict", None),
}
WRITER_STAGES = ("writer_a_write", "writer_a_repair", "writer_b_write", "writer_b_repair")
REVIEWER_STAGES = ("reviewer_b", "reviewer_a")
OVERRIDE_KEYS = ("model", "via", "effort", "settings")
STAGE_SETTING_KEYS = oc.SETTING_KEYS + ("retry_max_tokens", "retry_timeout_s")
IDENTITY = {"writer_a": "writer_a_write", "writer_b": "writer_b_write", "reviewer_b": "reviewer_b", "reviewer_a": "reviewer_a",
            "repair": "specialist_repair", "planner": "planner"}     # the stage whose model is the role's model in the ladder and lineage
REPAIR_OF = {"writer_a": "writer_a_repair", "writer_b": "writer_b_repair", "repair": "specialist_repair"}
CODES = ("unknown-stage", "unknown-key", "bad-stages", "model-not-selectable", "bad-model", "bad-via", "bad-effort", "role-rules",
         "test-profile-paid", "test-profile-claude-code", "paid-model-override", "reviewer-is-writer", "reviewers-same-model",
         "writers-share-model", "no-snapshot", "stage-started", "bad-revision",
         "unknown-billing", "billing-mismatch", "paid-model-stage", "unpriced-paid-model", "bad-ceiling")


class StageRefused(oc.RosterRefused):
    """A stage table that cannot be used. `code` is one of CODES, `stage` the stage it is about (None for the whole table)."""

    def __init__(self, code, message, stage=None):
        super().__init__(message, code=code)
        self.stage = stage


# ---------------------------------------------------------------- frozen values

def freeze(value):
    """A read-only copy: dicts become mapping proxies, lists tuples, at every depth."""
    if isinstance(value, dict):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    return value


def thaw(value):
    """A plain, JSON-ready copy of a frozen value."""
    if isinstance(value, (dict, MappingProxyType)):
        return {k: thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [thaw(v) for v in value]
    return copy.deepcopy(value)


def _sha256(data):
    return hashlib.sha256(json.dumps(thaw(data), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- the profile a table is resolved from

def _profile_data(profile):
    """(name, data) for a profile: a name (read now from config/roster.json, checked as the roster load checks it) or a profile dict (one
    holding roles, effort_by_mode, repair_max_tokens and stages, with an optional "name")."""
    if profile is None or isinstance(profile, str):
        name = (profile or oc.active_profile()).strip()
        data = oc.load_roster_data()
        if name not in data["profiles"]:
            raise oc.RosterRefused(f"unknown profile {name!r}: SATVIK_PROFILE is test (the default) or production")
        oc.load_profile(name)                       # the test profile's paid-role rule
        block = copy.deepcopy(data["profiles"][name])
        block["name"] = name
        block["roster_revision"] = data.get("revision", 0)
        return name, block
    block = thaw(profile)
    return block.get("name") or "test", block


def _stages_block(data):
    stages = data.get("stages") or {}
    if not isinstance(stages, dict):
        raise StageRefused("bad-stages", "the stages block must be an object of {stage: overrides}")
    return stages


def _default_effort(data, via, role_spec, mode):
    """The effort a stage uses when it sets none: a Claude Code role's own effort; for OpenRouter, the profile's effort for the mode."""
    if via == "claude_code":
        return role_spec.get("effort") if oc.via_of(role_spec) == "claude_code" else None
    return data["effort_by_mode"].get(mode) if mode else None


def _role_base(data, role, mode):
    """The stage a role gives with no override: its model and via, its settings (a repair mode on OpenRouter takes the profile's
    repair_max_tokens as max_tokens, which is what a repair call sends), and its default effort."""
    spec = data["roles"][role]
    via = oc.via_of(spec)
    settings = dict(spec["settings"])
    source_settings = f"role {role}"
    if mode == "repair" and via == "openrouter":
        settings["max_tokens"] = data["repair_max_tokens"]
        source_settings += " + profile repair_max_tokens"
    effort = _default_effort(data, via, spec, mode)
    effort_source = f"role {role}" if via == "claude_code" else (f"effort_by_mode.{mode}" if mode else "none")
    return {"model": spec["model"], "via": via, "effort": effort, "settings": settings,
            "source": {"model": f"role {role}", "via": f"role {role}", "effort": effort_source, "settings": source_settings}}


def _overlay(data, base, override, role, mode):
    """A base stage with an override on top: model, via and effort replace; settings keys replace one by one. A via change with no effort
    takes the new via's default effort."""
    entry = {"model": base["model"], "via": base["via"], "effort": base["effort"], "settings": dict(base["settings"]),
             "source": dict(base["source"])}
    for key in ("model", "via"):
        if key in override:
            entry[key] = override[key]
            entry["source"][key] = "stage"
    if "effort" in override:
        entry["effort"] = override["effort"]
        entry["source"]["effort"] = "stage"
    elif entry["via"] != base["via"]:
        entry["effort"] = _default_effort(data, entry["via"], data["roles"][role] if role else {}, mode)
        entry["source"]["effort"] = "default for the stage's via"
    if override.get("settings"):
        entry["settings"].update(override["settings"])
        entry["source"]["settings"] += " + stage"
    return entry


def _table(data):
    """The resolved table, unchecked: {stage: {model, via, effort, settings, model_selectable, source}}."""
    stages = _stages_block(data)
    table = {}
    for stage, (role, mode) in STAGES.items():
        override = stages.get(stage) or {}
        if stage in INHERITED:
            what = "the triggering reviewer" if stage == "reviewer_reask" else "the current writer by lineage"
            table[stage] = {"model": None, "via": None, "effort": override.get("effort"), "settings": dict(override.get("settings") or {}),
                            "model_selectable": False,
                            "source": {"model": f"inherited from {what}", "via": f"inherited from {what}",
                                       "effort": "stage" if "effort" in override else f"inherited from {what}",
                                       "settings": f"inherited from {what}" + (" + stage" if override.get("settings") else "")}}
            continue
        entry = _overlay(data, _role_base(data, role, mode), override, role, mode)
        entry["model_selectable"] = True
        table[stage] = entry
    return table


# ---------------------------------------------------------------- checks

def _problem(code, stage, message):
    return {"code": code, "stage": stage, "message": message}


def _shape_problems(stages):
    out = []
    for stage, override in stages.items():
        if stage not in STAGES:
            out.append(_problem("unknown-stage", stage, f"{stage!r} is not a stage; the stages are {', '.join(STAGE_NAMES)}"))
            continue
        if not isinstance(override, dict):
            out.append(_problem("bad-stages", stage, f"{stage}: overrides must be an object"))
            continue
        for key in override:
            if key not in OVERRIDE_KEYS:
                out.append(_problem("unknown-key", stage, f"{stage}: {key!r} cannot be set; a stage sets {', '.join(OVERRIDE_KEYS)}"))
        if stage in INHERITED:
            for key in ("model", "via"):
                if key in override:
                    out.append(_problem("model-not-selectable", stage,
                                        f"{stage}: its model is inherited from the job as it runs, so {key} cannot be set"))
        if "model" in override and (not isinstance(override["model"], str) or not override["model"].strip()):
            out.append(_problem("bad-model", stage, f"{stage}: model must be a non-empty string"))
        if "via" in override and override["via"] not in oc.VIAS:
            out.append(_problem("bad-via", stage, f"{stage}: via must be {' or '.join(oc.VIAS)}"))
        settings = override.get("settings", {})
        if not isinstance(settings, dict):
            out.append(_problem("unknown-key", stage, f"{stage}: settings must be an object"))
        else:
            for key in settings:
                if key not in STAGE_SETTING_KEYS:
                    out.append(_problem("unknown-key", stage, f"{stage}: settings key {key!r} is not one of {', '.join(STAGE_SETTING_KEYS)}"))
    return out


def _entry_problems(name, data, stage, entry, role_spec, overridden_model):
    """The existing roster rules for one resolved stage, as if it were a roster role: _check_role, the test profile's rules, and the effort."""
    out = []
    via, effort = entry["via"], entry["effort"]
    if via == "claude_code":
        if effort not in oc._claude_code().EFFORTS:
            out.append(_problem("bad-effort", stage, f"{stage}: a claude_code stage needs an effort, one of {', '.join(oc._claude_code().EFFORTS)}"))
    elif effort not in oc.EFFORT_VALUES:
        out.append(_problem("bad-effort", stage, f"{stage}: an OpenRouter effort is null, off, low, medium or high"))
    if out:
        return out
    paid, provider, pricing = role_spec["paid"], role_spec["provider"], role_spec["pricing"]
    billed_ok = True
    if via == "openrouter" and entry["model"] not in oc.SENTINELS:                # I36: the billing is the model's, not the role's
        billed = _billing(data, entry["model"])
        if billed is None:
            out.append(_problem("unknown-billing", stage, f"{stage}: {entry['model']} has no billing that config/models.json and the roster agree on"))
            billed_ok = False
        elif billed is not UNNAMED:
            paid, provider, pricing = billed
            if paid != role_spec["paid"]:                  # a role (or a stage on it) may not claim another billing class than its model's
                out.append(_problem("billing-mismatch", stage, f"{stage}: {entry['model']} is {'paid' if paid else 'free'} in config/models.json "
                                                               f"and the roster, but this stage's role is {'paid' if role_spec['paid'] else 'free'}"))
            if paid and STAGES[stage][1] not in ("review", "review-reask"):
                out.append(_problem("paid-model-stage", stage, f"{stage}: a paid model is for a reviewer stage only; {stage} is a {STAGES[stage][1]} stage"))
            if paid and (provider is None or pricing is None):
                out.append(_problem("unpriced-paid-model", stage, f"{stage}: {entry['model']} is paid and has no pinned provider and prices"))
    if overridden_model and role_spec["paid"] and via == "openrouter" and entry["model"] not in oc.SENTINELS:
        out.append(_problem("paid-model-override", stage, f"{stage}: a paid OpenRouter role keeps its roster model (its pin and prices belong to it)"))
    if name == "test" and (paid or role_spec["paid"]):
        out.append(_problem("test-profile-paid", stage, f"{stage}: the test profile refuses paid roles"))
    if out or not billed_ok:
        return out
    spec = {"model": entry["model"], "provider": provider, "paid": paid, "pricing": pricing, "settings": entry["settings"], "via": via}
    if via == "claude_code":
        spec["effort"] = effort
    try:
        oc._check_role(name, stage, spec)
    except oc.RosterRefused as exc:
        return [_problem("role-rules", stage, str(exc))]
    if name == "test":
        if via == "claude_code":
            problem = oc._claude_code().test_profile_problem(f"{name}/{stage}", spec)
            if problem:
                out.append(_problem("test-profile-claude-code", stage, problem))
    return out


def _problems(name, data):
    """Every problem of a profile's stage table, in a stable order."""
    try:
        stages = _stages_block(data)
    except StageRefused as exc:
        return [_problem(exc.code, None, str(exc))]
    out = _shape_problems(stages)
    if out:
        return out
    table = _table(data)
    for stage, (role, mode) in STAGES.items():
        if stage in INHERITED:
            for base_role, spec in _inherited_bases(data, table, stage):
                out += _entry_problems(name, data, stage, spec, data["roles"][base_role], False)
            continue
        out += _entry_problems(name, data, stage, table[stage], data["roles"][role], "model" in (stages.get(stage) or {}))
    writer_models = {}
    for stage in WRITER_STAGES:
        model = table[stage]["model"]
        if model not in oc.SENTINELS:
            writer_models.setdefault(model, set()).add(STAGES[stage][0])
    for model, roles in writer_models.items():
        if len(roles) > 1:
            out.append(_problem("writers-share-model", None, f"writer_a and writer_b stages both resolve to {model}: lineage cannot tell them apart"))
    reviewers = {}
    for stage in REVIEWER_STAGES:
        model = table[stage]["model"]
        if model == oc.NON_AUTHOR_WRITER or model in oc.SENTINELS:
            continue
        if model in writer_models:
            out.append(_problem("reviewer-is-writer", stage, f"{stage} resolves to {model}, a writer stage's model: the panel would not be independent"))
        if model in reviewers:
            out.append(_problem("reviewers-same-model", stage, f"{stage} and {reviewers[model]} both resolve to {model}"))
        reviewers[model] = stage
    return out


def _inherited_bases(data, table, stage):
    """The specs an inherited stage can be called with, one per role it can inherit from, for checking its overrides."""
    if stage == "reviewer_reask":
        roles = ["reviewer_b", "writer_a", "writer_b"] + ([] if table["reviewer_a"]["model"] in oc.SENTINELS else ["reviewer_a"])
        return [(role, call_spec(data, "reviewer_reask", role, table=table)) for role in roles]
    roles = ["writer_a", "writer_b", "reviewer_b"] + ([] if table["specialist_repair"]["model"] in oc.SENTINELS else ["repair"])
    return [(role, call_spec(data, "review_fix_repair", role, table=table)) for role in roles]


def validate(changes, profile=None):
    """[{code, stage, message}] for a stage table: the profile's stages block (default: the active profile, read now) with `changes`
    ({stage: overrides}; each stage's overrides replace the block's, settings merged) on top. [] when it can be used."""
    name, data = _profile_data(profile)
    try:
        merged = _merge(_stages_block(data), changes or {})
    except StageRefused as exc:
        return [_problem(exc.code, exc.stage, str(exc))]
    data["stages"] = merged
    return _problems(name, data)


def _merge(stages, changes):
    if not isinstance(changes, dict):
        raise StageRefused("bad-stages", "changes must be an object of {stage: overrides}")
    merged = copy.deepcopy(stages)
    for stage, change in changes.items():
        if not isinstance(change, dict):
            raise StageRefused("bad-stages", f"{stage}: overrides must be an object", stage)
        current = dict(merged.get(stage) or {})
        for key, value in change.items():
            if key == "settings" and isinstance(value, dict) and isinstance(current.get("settings"), dict):
                current["settings"] = dict(current["settings"], **value)
            else:
                current[key] = copy.deepcopy(value)
        merged[stage] = current
    return merged


def resolve_stages(profile=None):
    """The resolved, read-only stage table of a profile (a name read now from config/roster.json, default the active one, or a profile
    dict): {stage: {model, via, effort, settings, model_selectable, source}}. Raises StageRefused with the first problem's code."""
    name, data = _profile_data(profile)
    problems = _problems(name, data)
    if problems:
        first = problems[0]
        raise StageRefused(first["code"], first["message"], first["stage"])
    return freeze(_table(data))


# ---------------------------------------------------------------- the per-job snapshot

def snapshot(profile=None, paid_ceiling_usd=None):
    """(snapshot dict, sha256): everything a job's model calls need, frozen at job start. It holds the profile's roles, effort_by_mode,
    repair_max_tokens and stages block, the resolved table, the roster's revision, and its own revision (1). It also freezes the billing of
    every OpenRouter model it names (billing: paid, pin and prices, from config/models.json and the roster) and, when given, the job's approved
    paid ceiling (paid_ceiling_usd: the most USD its paid calls may spend, I44). The sha256 is over its canonical JSON. Raises StageRefused
    (or RosterRefused) when the table cannot be used: the job then makes no call."""
    name, data = _profile_data(profile)
    table = resolve_stages(dict(data, name=name))
    snap = {"schema_version": SNAPSHOT_SCHEMA, "revision": 1, "profile": name, "roster_revision": data.get("roster_revision", 0),
            "taken": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "roster": {"roles": data["roles"], "effort_by_mode": data["effort_by_mode"], "repair_max_tokens": data["repair_max_tokens"],
                       "stages": _stages_block(data)},
            "resolved": thaw(table), "billing": _billing_map(dict(data, name=name), table)}
    if paid_ceiling_usd is not None:
        snap["paid_ceiling_usd"] = _ceiling(paid_ceiling_usd)
    return snap, _sha256(snap)


def profile_of(snap):
    """The profile dict a snapshot resolves from."""
    roster = snap["roster"]
    return {"name": snap["profile"], "roles": roster["roles"], "effort_by_mode": roster["effort_by_mode"],
            "repair_max_tokens": roster["repair_max_tokens"], "stages": roster.get("stages") or {},
            "billing": snap.get("billing") or {}, "paid_ceiling_usd": snap.get("paid_ceiling_usd")}


def ladder_roles(snap):
    """The roles view the ladder reads (p1b_routing._m and panel_for): each role's roster entry with its model replaced by the model of its
    identity stage (a writer's write stage, a reviewer's stage)."""
    data = profile_of(snap)
    table = _table(data)
    view = {}
    for role, spec in data["roles"].items():
        entry = table[IDENTITY[role]]
        view[role] = dict(spec, model=entry["model"], via=entry["via"], settings=entry["settings"])
        if entry["via"] == "claude_code":
            view[role]["effort"] = entry["effort"]
        else:
            view[role].pop("effort", None)
    return view


UNNAMED = object()      # _billing's answer for a model that config/models.json and the roster do not name at all


def _billing(data, model):
    """(paid, provider, pricing) of an OpenRouter model (I36): the billing the snapshot froze for it (data["billing"]) when it holds the model,
    else what config/models.json and the roster name now (openrouter_call.model_billing). None when they disagree (the caller refuses). UNNAMED
    when no source names the model: the caller uses the stage's role values for it, and the call itself is refused, since the adapter sends
    only allow-listed models. The billing belongs to the model, never to the role that names it."""
    frozen = (data.get("billing") or {}).get(model)
    if frozen is not None:
        return frozen["paid"], frozen["provider"], frozen["pricing"]
    billed = oc.model_billing(model, data.get("roles"))
    if billed is None and not oc.model_named(model, data.get("roles")):
        return UNNAMED
    return billed


def _billing_map(data, table):
    """{model: {paid, provider, pricing}} for every OpenRouter model a table or a role names (the snapshot freezes it, so a catalog edit
    during a job changes no call of that job). Raises StageRefused unknown-billing for a model its sources disagree on."""
    out = {}
    names = [entry.get("model") for entry in table.values() if entry.get("via") == "openrouter"]
    names += [spec.get("model") for spec in data["roles"].values() if oc.via_of(spec) == "openrouter"]
    for model in names:
        if not isinstance(model, str) or model in oc.SENTINELS or model in out:
            continue
        billed = _billing(data, model)
        if billed is None:
            raise StageRefused("unknown-billing", f"{model} has no billing that config/models.json and the roster agree on")
        if billed is not UNNAMED:
            out[model] = {"paid": billed[0], "provider": billed[1], "pricing": billed[2]}
    return out


def _ceiling(value):
    """The approved paid ceiling of a job in USD (None: none approved, the job's own review budget applies), a number of at least 0."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise StageRefused("bad-ceiling", "the paid ceiling is a number of USD of at least 0")
    return float(value)


def _spec(data, table, stage, role, entry):
    """A plain call spec: the stage's resolved values, with the billing of its model (paid flag, pin and prices, I36) and the job's approved
    paid ceiling (ceiling_usd, None when the snapshot approves none)."""
    spec = data["roles"][role]
    paid, provider, pricing = spec["paid"], spec["provider"], spec["pricing"]
    if entry["via"] == "openrouter" and entry["model"] not in oc.SENTINELS:
        billed = _billing(data, entry["model"])
        if billed is None:
            raise StageRefused("unknown-billing", f"{stage}: {entry['model']} has no billing that config/models.json and the roster agree on", stage)
        if billed is not UNNAMED:
            paid, provider, pricing = billed
    return {"stage": stage, "role": role, "model": entry["model"], "via": entry["via"], "effort": entry["effort"],
            "settings": dict(entry["settings"]), "paid": paid, "provider": provider, "pricing": pricing,
            "ceiling_usd": data.get("paid_ceiling_usd")}


def _fallback(data, table, stage, role, mode):
    """reviewer_b writing (or repairing as the author) when writer_b is down: reviewer_b's model and via; its own settings with the writer_b
    stage's settings overrides on top; the writer_b stage's effort when both have the same via. Otherwise a Claude Code reviewer_b keeps its
    own stage's effort (that is the effort its model is called with), and an OpenRouter one takes the default for the mode."""
    rb = table["reviewer_b"]
    writer_stage = "writer_b_write" if mode == "write" else "writer_b_repair"
    override = _stages_block(data).get(writer_stage) or {}
    settings = dict(_role_base(data, "reviewer_b", mode)["settings"])
    settings.update((_stages_block(data).get("reviewer_b") or {}).get("settings") or {})
    settings.update(override.get("settings") or {})
    if table[writer_stage]["via"] == rb["via"]:
        effort = table[writer_stage]["effort"]
    elif rb["via"] == "claude_code":
        effort = rb["effort"]
    else:
        effort = _default_effort(data, rb["via"], data["roles"]["reviewer_b"], mode)
    return _spec(data, table, stage, "reviewer_b", dict(rb, settings=settings, effort=effort))


def _writer_reviewing(data, table, role):
    """A writer in a review slot (non_author_writer, or the backup for a failed slot): its write stage's model, via and settings, the review
    effort, and the overrides of a non_author_writer reviewer_a stage."""
    write = table[IDENTITY[role]]
    effort = write["effort"] if write["via"] == "claude_code" else data["effort_by_mode"].get("review")
    entry = dict(write, settings=dict(write["settings"]), effort=effort)
    if table["reviewer_a"]["model"] == oc.NON_AUTHOR_WRITER:
        override = _stages_block(data).get("reviewer_a") or {}
        entry["settings"].update(override.get("settings") or {})
        if "effort" in override:
            entry["effort"] = override["effort"]
    stage = "reviewer_a" if table["reviewer_a"]["model"] == oc.NON_AUTHOR_WRITER else "reviewer_b"
    return _spec(data, table, stage, role, entry)


def call_spec(data, stage, role=None, table=None):
    """The spec one model call of `stage` uses (a plain dict: stage, role, model, via, effort, settings, paid, provider, pricing). `data` is a
    profile dict (profile_of). `role` names who is called when the ladder decides it: the writer for a write by reviewer_b (the fallback
    write), the reviewer for a review by a writer, the reviewer that triggers reviewer_reask, and the current writer for review_fix_repair."""
    table = _table(data) if table is None else table
    base_role, mode = STAGES[stage]
    role = role or base_role
    if stage == "reviewer_reask":
        trigger = call_spec(data, "reviewer_b" if role == "reviewer_b" else "reviewer_a", role, table)
        override = _stages_block(data).get(stage) or {}
        trigger["settings"].update(override.get("settings") or {})
        if "effort" in override:
            trigger["effort"] = override["effort"]
        elif trigger["via"] == "openrouter":
            trigger["effort"] = data["effort_by_mode"].get("review-reask")
        trigger["stage"] = stage
        return trigger
    if stage == "review_fix_repair":
        if role == "reviewer_b":
            spec = _fallback(data, table, stage, "reviewer_b", "repair")
        else:
            spec = _spec(data, table, stage, role, table[REPAIR_OF[role]])
        override = _stages_block(data).get(stage) or {}
        spec["settings"].update(override.get("settings") or {})
        if "effort" in override:
            spec["effort"] = override["effort"]
        return spec
    if stage == "writer_b_write" and role == "reviewer_b":
        return _fallback(data, table, stage, "reviewer_b", "write")
    if stage in REVIEWER_STAGES and role in ("writer_a", "writer_b"):
        return _writer_reviewing(data, table, role)
    return _spec(data, table, stage, role, table[stage])


def dispatch(data, role, mode, fix=False, table=None):
    """(stages started, call spec) for one call the ladder makes: `role` is who is called (writer_a, writer_b, reviewer_b, reviewer_a, or
    repair for the repair step), `mode` its call mode, `fix` True while FIX FIRST findings are staged (the call is review_fix_repair). A write
    by reviewer_b is the fallback write: it starts writer_b_write, and reviewer_b too, since its model then authors code."""
    table = _table(data) if table is None else table
    if mode == "write":
        if role == "reviewer_b":
            return ("writer_b_write", "reviewer_b"), call_spec(data, "writer_b_write", "reviewer_b", table)
        stage = IDENTITY[role]
        return (stage,), call_spec(data, stage, role, table)
    if mode == "repair":
        if fix or role == "reviewer_b":
            return ("review_fix_repair",), call_spec(data, "review_fix_repair", role, table)
        stage = REPAIR_OF[role]
        return (stage,), call_spec(data, stage, role, table)
    if mode == "review-reask":
        return ("reviewer_reask",), call_spec(data, "reviewer_reask", role, table)
    spec = call_spec(data, role if role in REVIEWER_STAGES else "reviewer_a", role, table)
    return (spec["stage"],), spec


# ---------------------------------------------------------------- the job's files

_THREAD_LOCKS = {}
_THREAD_LOCKS_GUARD = threading.Lock()


class job_lock:
    """The job folder's stage lock: one thread at a time in this process (a threading lock) and one process at a time (a lock file). The
    runner takes it to mark a stage started and read the snapshot; apply_future_stage takes it to check and write. So a change and a stage
    start never cross."""

    def __init__(self, job_dir, timeout_s=30.0):
        self.path = Path(job_dir) / LOCK_FILE
        self.timeout_s = timeout_s
        key = os.path.normcase(os.path.realpath(str(self.path)))
        with _THREAD_LOCKS_GUARD:
            self._thread = _THREAD_LOCKS.setdefault(key, threading.Lock())
        self._file = None

    def __enter__(self):
        if not self._thread.acquire(timeout=self.timeout_s):
            raise TimeoutError(f"could not lock {self.path} within {self.timeout_s}s")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._file = oc.FileLock(self.path).acquire(self.timeout_s)
        except BaseException:
            self._thread.release()
            raise
        return self

    def __exit__(self, *exc):
        try:
            if self._file is not None:
                self._file.release()
        finally:
            self._file = None
            self._thread.release()


def _atomic_json(path, data):
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def write_snapshot(job_dir, snap):
    """Writes a fresh job's snapshot (revision 1) and clears the stages a previous run of the folder started. Returns the file's path."""
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    with job_lock(job_dir):
        _atomic_json(job_dir / SNAPSHOT_FILE, snap)
        _atomic_json(job_dir / STARTED_FILE, {"started": {}})
    return job_dir / SNAPSHOT_FILE


def read_snapshot(job_dir):
    """The job's current snapshot (its latest revision), or None when the job has none."""
    path = Path(job_dir) / SNAPSHOT_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def snapshot_evidence(snap):
    """{sha256, revision} of a snapshot: status.json evidence.stage_snapshot."""
    return {"sha256": _sha256(snap), "revision": snap["revision"]}


def begin_call(job_dir, role, mode, fix=False):
    """The call spec for one model call of the job, from its current snapshot, with the call's stages marked started, all under the job's
    stage lock (see dispatch for role, mode and fix). A stage once marked stays started: apply_future_stage refuses it from then on, so the
    call that follows cannot be changed. Raises StageRefused (no-snapshot) when the job folder has no snapshot."""
    job_dir = Path(job_dir)
    with job_lock(job_dir):
        snap = read_snapshot(job_dir)
        if snap is None:
            raise StageRefused("no-snapshot", f"{job_dir} has no {SNAPSHOT_FILE}")
        stages, spec = dispatch(profile_of(snap), role, mode, fix)
        started = _read_started(job_dir)
        fresh = [s for s in stages if s not in started]
        if fresh:
            for stage in fresh:
                started[stage] = {"revision": snap["revision"], "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            _atomic_json(job_dir / STARTED_FILE, {"started": started})
        spec["revision"] = snap["revision"]
        return spec


def _read_started(job_dir):
    try:
        data = json.loads((Path(job_dir) / STARTED_FILE).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    started = data.get("started") if isinstance(data, dict) else None
    return dict(started) if isinstance(started, dict) else {}


# ---------------------------------------------------------------- which stages a job has started

def _role_models(data, table):
    """{role: models its stages resolve to}, for reading the records back."""
    out = {role: set() for role in ("writer_a", "writer_b", "reviewer_b", "reviewer_a", "repair")}
    for stage, (role, _mode) in STAGES.items():
        if role in out and table[stage]["model"] is not None:
            out[role].add(table[stage]["model"])
    out["repair"].add("anthropic")              # the repair step's name in the ladder's steps (p1b_routing.ANTHROPIC)
    return out


def _stages_of(records, data, table):
    """The stages a job's records show started. `records` is [(model, mode)] in call order. Read conservatively: a record that could belong
    to more than one stage starts each of them, and a repair after the first review is a review_fix_repair."""
    roles = _role_models(data, table)

    def who(model):
        return [role for role, models in roles.items() if model in models]

    found, reviewed = set(), False
    for model, mode in records:
        owners = who(model)
        if mode == "write":
            if "reviewer_b" in owners:
                found |= {"writer_b_write", "reviewer_b"}
            hits = {IDENTITY[r] for r in owners if r in ("writer_a", "writer_b")}
            found |= hits or ({"writer_a_write", "writer_b_write"} if "reviewer_b" not in owners else set())
        elif mode == "repair":
            if reviewed:
                found.add("review_fix_repair")
                continue
            hits = {REPAIR_OF[r] for r in owners if r in REPAIR_OF}
            found |= hits or {"writer_a_repair", "writer_b_repair", "specialist_repair", "review_fix_repair"}
        elif mode == "review":
            reviewed = True
            hits = {r for r in owners if r in REVIEWER_STAGES}
            found |= hits or {"reviewer_b", "reviewer_a"}
        elif mode == "review-reask":
            reviewed = True
            found.add("reviewer_reask")
    return found


def _numbered(history, prefix):
    """The history files prefix_NN.json in number order."""
    found = []
    for path in history.glob(prefix + "_*.json") if history.is_dir() else []:
        tail = path.stem[len(prefix) + 1:]
        if tail.isdigit():
            found.append((int(tail), path))
    return [path for _, path in sorted(found)]


def _records(job_dir):
    """Three lists of (model, mode), each in order: the job's status.json steps, its history call records, its failed call records."""
    job_dir = Path(job_dir)
    try:
        status = json.loads((job_dir / "status.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        status = {}
    steps = status.get("steps") if isinstance(status, dict) else None
    from_steps = []
    for step in steps if isinstance(steps, list) else []:
        if not isinstance(step, dict):
            continue
        mode = step.get("call") if step.get("mode") == "failed" else step.get("mode")
        from_steps.append((step.get("model"), mode))
    out = [from_steps]
    history = job_dir / "history"
    for prefix in ("call", "failed_call"):
        records = []
        for path in _numbered(history, prefix):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(record, dict):
                records.append((record.get("model"), record.get("mode") or record.get("call")))
        out.append(records)
    return out


def started_stages(job_dir, snap=None):
    """Every stage the job has started (marked at its start, or shown by a step or a call record)."""
    snap = read_snapshot(job_dir) if snap is None else snap
    found = set(_read_started(job_dir))
    if snap is not None:
        data = profile_of(snap)
        table = _table(data)
        for records in _records(job_dir):
            found |= _stages_of(records, data, table)
    return found


# ---------------------------------------------------------------- apply future stage

def apply_future_stage(job_dir, stage, change):
    """Changes one stage of a running job for its future calls: {"ok": True, "revision": n} or {"ok": False, "code", "message"}.

    Allowed only when the job has not started that stage (no start mark, no step, no call record), when a model change names a selectable
    stage, and when the whole resolved table still passes validation. It writes the job's stage_snapshot.json as revision n+1, atomically,
    under the job's stage lock; the job reads it when the stage next starts. A started stage (in flight or finished) is refused
    (stage-started), so no call in flight is ever changed."""
    def refused(code, message):
        return {"ok": False, "code": code, "message": message}

    job_dir = Path(job_dir)
    if stage not in STAGES:
        return refused("unknown-stage", f"{stage!r} is not a stage; the stages are {', '.join(STAGE_NAMES)}")
    if not isinstance(change, dict) or not change:
        return refused("bad-stages", "a change is a non-empty object of model, via, effort and settings")
    if stage in INHERITED and ("model" in change or "via" in change):
        return refused("model-not-selectable", f"{stage}: its model is inherited from the job as it runs, so it cannot be changed")
    with job_lock(job_dir):
        snap = read_snapshot(job_dir)
        if snap is None:
            return refused("no-snapshot", f"{job_dir} has no {SNAPSHOT_FILE}: the job has not started, or is not a production job")
        if stage in started_stages(job_dir, snap):
            return refused("stage-started", f"{stage} has started for this job (in flight or finished): it cannot be changed")
        data = profile_of(snap)
        try:
            data["stages"] = _merge(data["stages"], {stage: change})
        except StageRefused as exc:
            return refused(exc.code, str(exc))
        problems = _problems(data["name"], data)
        if problems:
            return refused(problems[0]["code"], "; ".join(p["message"] for p in problems))
        fresh = dict(snap, revision=snap["revision"] + 1, updated=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        fresh["roster"] = dict(snap["roster"], stages=data["stages"])
        fresh["resolved"] = thaw(freeze(_table(data)))
        fresh["billing"] = _billing_map(data, _table(data))           # the models the change names are frozen too (I36)
        fresh.setdefault("changes", [])
        fresh["changes"] = list(fresh["changes"]) + [{"revision": fresh["revision"], "stage": stage, "change": change}]
        _atomic_json(job_dir / SNAPSHOT_FILE, fresh)
    return {"ok": True, "revision": fresh["revision"]}
