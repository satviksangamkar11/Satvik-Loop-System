"""Control Center P1 (2026-11-03): the model catalog (config/models.json, scripts/source/model_catalog.py) and the allow-list it widens.
Fakes only (the scripted transports of prod_harness and test_openrouter_call): no network, no model call."""
import copy
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
import claude_code as cc  # noqa: E402
import model_catalog as mc  # noqa: E402
import openrouter_call as oc  # noqa: E402
import prod_harness as ph  # noqa: E402
import shared_limits as sl  # noqa: E402
from test_openrouter_call import Fake  # noqa: E402

LIGHTNING = "nvidia/nemotron-3.5-lightning:free"        # a catalog free model that is on no roster
DEEPSEEK = "deepseek/deepseek-v4.1-flash-1017"           # a paid catalog model with no pin and no prices
QWEN = "qwen/qwen3.8-27b"
GLM = "z-ai/glm-5.3-flash"
NOT_LISTED = "nvidia/nemotron-3.5-content-safety:free"   # answered 404 in the probe: on neither the roster nor the catalog
ANSWERED = ("cohere/north-mini-code:free", "dots-studio/dots-3-note-preview:free", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
            "nvidia/nemotron-3-ultra-550b-a55b:free", LIGHTNING, "nvidia/nemotron-3.5-super-vl:free")   # docs/p1a_free_models_probe.json, ok
STAMP = "2026-09-21T14:13:20Z"                           # the time of the fake clock below (1_790_000_000)


def file_catalog():
    return json.loads(mc.CATALOG_PATH.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def shared_home(tmp_path, monkeypatch):
    folder = tmp_path / "satvik_home"
    folder.mkdir()
    monkeypatch.setenv("SATVIK_HOME", str(folder))
    return folder


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    """catalog(change) writes a copy of config/models.json (after `change(data)`, when given) and points the module and the adapter at it
    (the adapter's per-profile cache starts empty). Returns the file's path."""
    def use(change=None):
        data = file_catalog()
        if change is not None:
            change(data)
        path = tmp_path / "models.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        monkeypatch.setattr(mc, "CATALOG_PATH", path)
        monkeypatch.setattr(oc, "_ROSTER_STATE", {})
        return path
    return use


def entry(data, model_id):
    return mc.entry_of(data, model_id)


def pin_qwen(data):
    e = entry(data, QWEN)
    e["hosting_provider"] = {"name": "Alibaba", "slug": "alibaba"}
    e["price_in_usd_per_mtok"], e["price_out_usd_per_mtok"] = 0.2, 0.6


# ---- 1. the catalog loads and validates

def test_the_catalog_file_loads_and_holds_the_roster_and_the_answering_free_models():
    data = mc.load_catalog()
    ids = [e["model_id"] for e in data["openrouter"]]
    assert 10 <= len(ids) <= 20
    roster = json.loads(oc.ROSTER_PATH.read_text(encoding="utf-8"))["profiles"]
    on_roster = {spec["model"] for p in roster.values() for spec in p["roles"].values()
                 if oc.via_of(spec) == "openrouter" and spec["model"] not in oc.SENTINELS}
    assert on_roster <= set(ids)
    assert set(ANSWERED) <= set(ids)
    assert [(e["model"], e["via"], e["efforts"], e["cost"]) for e in data["claude_code"]] == \
        [(m, "claude_code", list(cc.EFFORTS), None) for m in cc.MODELS]
    for e in data["openrouter"]:
        if not e["paid"]:
            assert e["model_id"].endswith(":free") and e["price_in_usd_per_mtok"] == 0 and e["price_out_usd_per_mtok"] == 0
    assert entry(data, GLM)["hosting_provider"] == {"name": "Z.AI", "slug": "z-ai"}
    assert entry(data, DEEPSEEK)["price_in_usd_per_mtok"] is None and entry(data, DEEPSEEK)["hosting_provider"] is None


def _set(model_id, field, value):
    def change(data):
        target = entry(data, model_id)
        keys = field.split(".")
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value
    return change


@pytest.mark.parametrize("change, code", [
    (lambda d: d.update(schema_version=2), "schema"),
    (lambda d: entry(d, LIGHTNING).pop("structured_outputs"), "entry_shape"),
    (_set(LIGHTNING, "paid", True), "free_mismatch"),
    (_set(DEEPSEEK, "paid", False), "free_mismatch"),
    (_set(LIGHTNING, "price_in_usd_per_mtok", None), "price"),
    (_set(DEEPSEEK, "price_out_usd_per_mtok", -1), "price"),
    (_set(LIGHTNING, "context_tokens", 0), "limit"),
    (_set(LIGHTNING, "reasoning.on_off", "yes"), "reasoning"),
    (_set(LIGHTNING, "temperature", {"min": 2, "max": 0}), "temperature"),
    (_set(LIGHTNING, "contracts.reviewer_verdict", "pass"), "contracts"),
    (_set(LIGHTNING, "hosting_provider", {"name": "X"}), "hosting_provider"),
    (_set(LIGHTNING, "verified_at", {"other": "2026-11-03T00:00:00Z"}), "verified_at"),
    (lambda d: d["openrouter"].append(copy.deepcopy(d["openrouter"][0])), "duplicate"),
    (lambda d: d["claude_code"][0].update(cost=20.0), "claude_code_cost"),
    (lambda d: d["claude_code"][0].update(efforts=["low"]), "claude_code"),
    (lambda d: d["claude_code"][0].update(model="gpt"), "claude_code"),
])
def test_a_bad_catalog_is_refused_with_a_stable_code(change, code):
    data = file_catalog()
    change(data)
    with pytest.raises(mc.CatalogError) as caught:
        mc.validate_catalog(data)
    assert caught.value.code == code


def test_an_unreadable_catalog_is_refused_and_the_adapter_refuses_it_like_a_bad_roster(tmp_path, monkeypatch):
    path = tmp_path / "models.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(mc.CatalogError) as caught:
        mc.load_catalog(path)
    assert caught.value.code == "unreadable"
    monkeypatch.setattr(mc, "CATALOG_PATH", path)
    monkeypatch.setattr(oc, "_ROSTER_STATE", {})
    with pytest.raises(oc.RosterRefused, match="models.json"):
        oc.roster_state()


# ---- 2. refresh_metadata

MODELS_PAYLOAD = {"data": [
    {"id": DEEPSEEK, "pricing": {"prompt": "0.00000027", "completion": "0.0000011"}, "context_length": 163840,
     "top_provider": {"max_completion_tokens": 65536, "context_length": 163840},
     "supported_parameters": ["max_tokens", "temperature", "reasoning", "include_reasoning", "response_format"]},
    {"id": GLM, "pricing": {"prompt": "0.0000001", "completion": "0.0000004"}, "context_length": 200000,
     "top_provider": {"max_completion_tokens": None}, "supported_parameters": ["temperature", "structured_outputs"]},
    {"id": LIGHTNING, "pricing": {"prompt": "0", "completion": "0"}, "context_length": 131072,
     "top_provider": {"max_completion_tokens": 32768}, "supported_parameters": ["max_tokens"]},
    {"id": "some/other-model", "pricing": {"prompt": "0.001", "completion": "0.002"}, "context_length": 4096},
]}


def test_refresh_fills_the_metadata_from_a_fake_models_listing_and_converts_prices_to_per_million(catalog):
    path = catalog()
    fake = Fake()
    fake.on("/models", (200, {}, json.dumps(MODELS_PAYLOAD).encode()))
    report = mc.refresh_metadata(fake, {"free": ph.FREE_KEY}, clock=lambda: 1_790_000_000)
    assert fake.calls[0]["method"] == "GET" and fake.calls[0]["url"] == oc.API + "/models"
    assert set(report["updated"]) == {DEEPSEEK, GLM, LIGHTNING} and QWEN in report["missing"]
    assert "some/other-model" not in path.read_text(encoding="utf-8")       # only catalog models are written
    assert ph.FREE_KEY not in path.read_text(encoding="utf-8")
    data = mc.load_catalog(path)
    deepseek = entry(data, DEEPSEEK)
    assert deepseek["price_in_usd_per_mtok"] == pytest.approx(0.27) and deepseek["price_out_usd_per_mtok"] == pytest.approx(1.1)
    assert deepseek["context_tokens"] == 163840 and deepseek["max_output_tokens"] == 65536
    assert deepseek["reasoning"]["on_off"] is True and deepseek["reasoning"]["efforts"] == [] and deepseek["reasoning"]["token_budget"] is None
    assert deepseek["temperature"] == {"min": 0.0, "max": 2.0} and deepseek["structured_outputs"] is True
    assert deepseek["metadata_at"] == STAMP
    glm = entry(data, GLM)
    assert (glm["price_in_usd_per_mtok"], glm["price_out_usd_per_mtok"]) == (0.15, 0.5)   # pinned: the pinned provider's prices stay
    assert glm["max_output_tokens"] is None and glm["structured_outputs"] is True
    assert glm["reasoning"] == {"on_off": False, "efforts": [], "token_budget": False}
    lightning = entry(data, LIGHTNING)
    assert lightning["price_in_usd_per_mtok"] == 0 and lightning["temperature"] is None and lightning["structured_outputs"] is False
    qwen = entry(data, QWEN)
    assert qwen["metadata_at"] is None and qwen["context_tokens"] is None             # not listed: unchanged
    assert not list(path.parent.glob(".models.*.tmp"))                                  # the atomic write left no temporary file


def test_a_failed_models_read_writes_nothing_and_names_no_key(catalog):
    path = catalog()
    before = path.read_text(encoding="utf-8")
    fake = Fake()
    fake.on("/models", (500, {}, ("upstream down, key " + ph.FREE_KEY).encode()))
    with pytest.raises(mc.CatalogError) as caught:
        mc.refresh_metadata(fake, {"free": ph.FREE_KEY})
    assert caught.value.code == "metadata_unavailable" and ph.FREE_KEY not in str(caught.value)
    assert path.read_text(encoding="utf-8") == before


# ---- 3. verify_model

GOOD = {
    "writer_file_blocks": ph.file_reply((mc.VERIFY_FILE, mc.VERIFY_AFTER)),
    "repair_edit_blocks": f"FILE: {mc.VERIFY_FILE}\n<<<<<<< SEARCH\n{mc.VERIFY_BEFORE}=======\n{mc.VERIFY_AFTER}>>>>>>> REPLACE\n",
    "reviewer_verdict": ph.READY,
}
BAD = {
    "writer_file_blocks": ph.file_reply(("../outside.py", mc.VERIFY_AFTER)),          # a FILE block (check_reply passes) that parse_files refuses
    "repair_edit_blocks": f"FILE: {mc.VERIFY_FILE}\n<<<<<<< SEARCH\ny = 9\n=======\n{mc.VERIFY_AFTER}>>>>>>> REPLACE\n",   # apply_edits: not found
    "reviewer_verdict": "Looks fine to me.",                                           # no verdict line: check_reply rejects it
}


@pytest.mark.parametrize("contract", mc.CONTRACTS)
@pytest.mark.parametrize("passed", [True, False])
def test_verify_records_pass_or_fail_for_each_contract_through_the_adapter_on_the_shared_ledger(tmp_path, catalog, contract, passed):
    path = catalog()
    provider = ph.Provider({LIGHTNING: [(GOOD if passed else BAD)[contract]]})
    client = ph.client(tmp_path, provider)
    result = mc.verify_model(client, LIGHTNING, contract, clock=lambda: 1_790_000_000)
    assert result["passed"] is passed and (result["reason"] is None) is passed
    assert provider.models() == [LIGHTNING]
    request = provider.calls[0][1]
    assert request["max_tokens"] == mc.VERIFY_MAX_TOKENS and "provider" not in request
    assert client.shared is not None and sl.usage(client.shared_home)["free_today"] == 1
    data = mc.load_catalog(path)
    assert entry(data, LIGHTNING)["contracts"] == {c: (passed if c == contract else None) for c in mc.CONTRACTS}
    assert entry(data, LIGHTNING)["verified_at"] == {contract: STAMP}
    assert ph.FREE_KEY not in path.read_text(encoding="utf-8")


def test_verify_keeps_earlier_contracts_and_records_nothing_when_no_reply_came(tmp_path, catalog):
    path = catalog()
    provider = ph.Provider({LIGHTNING: [GOOD["reviewer_verdict"]]})
    mc.verify_model(ph.client(tmp_path, provider), LIGHTNING, "reviewer_verdict")
    before = path.read_text(encoding="utf-8")
    fake = Fake()
    fake.on("/chat/completions", *[(503, {}, b"busy")] * 4)
    client = oc.OpenRouter({"free": ph.FREE_KEY}, tmp_path / "ledger2", transport=fake, sleep=lambda s: None, rand=lambda: 0.0)
    with pytest.raises(oc.ProviderUnavailable):
        mc.verify_model(client, LIGHTNING, "writer_file_blocks")
    assert path.read_text(encoding="utf-8") == before
    assert entry(mc.load_catalog(path), LIGHTNING)["contracts"]["reviewer_verdict"] is True


def test_verify_refuses_paid_unknown_models_and_unknown_contracts_before_any_call(tmp_path, catalog):
    catalog()
    provider = ph.Provider({})
    client = ph.client(tmp_path, provider)
    for model_id, contract, code in ((DEEPSEEK, "reviewer_verdict", "paid_not_verified"), (NOT_LISTED, "reviewer_verdict", "unknown_model"),
                                     (LIGHTNING, "json", "unknown_contract")):
        with pytest.raises(mc.CatalogError) as caught:
            mc.verify_model(client, model_id, contract)
        assert caught.value.code == code
    assert provider.http == []


# ---- 4. capabilities

def test_capabilities_hide_every_unsupported_or_unknown_control(catalog):
    def change(data):
        e = entry(data, DEEPSEEK)
        e["reasoning"] = {"on_off": False, "efforts": ["high"], "token_budget": False}
        e["contracts"] = {"writer_file_blocks": False, "repair_edit_blocks": None, "reviewer_verdict": False}
    path = catalog(change)
    assert mc.capabilities(DEEPSEEK, path) == {"model_id": DEEPSEEK, "via": "openrouter", "paid": True}
    assert mc.capabilities(LIGHTNING, path) == {"model_id": LIGHTNING, "via": "openrouter", "paid": False}


def test_capabilities_show_the_supported_controls_and_only_the_efforts_the_adapter_sends(catalog):
    def change(data):
        e = entry(data, LIGHTNING)
        e["reasoning"] = {"on_off": True, "efforts": ["minimal", "low", "high"], "token_budget": True}
        e["temperature"] = {"min": 0.0, "max": 2.0}
        e["max_output_tokens"] = 32768
        e["contracts"]["reviewer_verdict"] = True
        e["contracts"]["writer_file_blocks"] = False
    path = catalog(change)
    assert mc.capabilities(LIGHTNING, path) == {
        "model_id": LIGHTNING, "via": "openrouter", "paid": False,
        "reasoning": {"modes": ["off", "on"], "efforts": ["low", "high"], "token_budget": True},
        "temperature": {"min": 0.0, "max": 2.0}, "max_output_tokens": 32768, "contracts": ["reviewer_verdict"]}
    for model in cc.MODELS:
        assert mc.capabilities(model, path) == {"model_id": model, "via": "claude_code", "efforts": list(cc.EFFORTS)}
    with pytest.raises(mc.CatalogError) as caught:
        mc.capabilities(NOT_LISTED, path)
    assert caught.value.code == "unknown_model"


# ---- 5. the allow-list

@pytest.mark.parametrize("name", ["test", "production"])
def test_a_catalog_free_model_is_allowed_and_a_model_on_neither_list_is_still_refused(catalog, profile, name):
    catalog()
    profile(name)
    assert oc.resolve(LIGHTNING) == LIGHTNING and LIGHTNING in oc.ALLOWED and LIGHTNING not in oc.PAID
    assert oc.SETTINGS[LIGHTNING] == mc.call_settings(entry(mc.load_catalog(), LIGHTNING))
    for refused in (NOT_LISTED, "openrouter/auto", f"{LIGHTNING},{NOT_LISTED}"):
        with pytest.raises(oc.ModelRefused):
            oc.resolve(refused)


def test_a_catalog_free_model_call_goes_out_free_with_no_provider_pin(tmp_path, catalog):
    catalog()
    provider = ph.Provider({LIGHTNING: [ph.READY]})
    text, info = ph.client(tmp_path, provider).call(LIGHTNING, "s", "u", kind="reviewer")
    assert text == ph.READY and info["model"] == LIGHTNING
    _, request = provider.calls[0]
    assert "provider" not in request and "reasoning" not in request
    assert provider.http[-1][2]["Authorization"] == "Bearer " + ph.FREE_KEY


def test_a_paid_catalog_model_without_a_pin_or_prices_is_refused(catalog, profile):
    catalog()
    profile("production")
    for model in (DEEPSEEK, QWEN):
        assert model not in oc.PAID and model not in oc.ALLOWED
        with pytest.raises(oc.ModelRefused):
            oc.resolve(model)
    catalog(lambda d: entry(d, QWEN).update(hosting_provider={"name": "Alibaba", "slug": "alibaba"}))     # pinned, but no prices
    with pytest.raises(oc.ModelRefused):
        oc.resolve(QWEN)


def test_a_pinned_priced_paid_catalog_model_is_reviewer_only_under_production(tmp_path, catalog, profile):
    catalog(pin_qwen)
    profile("production")
    assert oc.resolve(QWEN) == QWEN and QWEN in oc.PAID and QWEN not in oc.ALLOWED
    assert oc.PINS[QWEN] == {"name": "Alibaba", "slug": "alibaba"}
    assert oc.PRICING[QWEN] == pytest.approx({"prompt": 0.2e-6, "completion": 0.6e-6})
    assert oc.PAID - {QWEN} == {GLM}
    with pytest.raises(oc.ModelRefused):
        oc.resolve(GLM)                                       # a paid roster model is still called only through its role
    fake = Fake()
    client = oc.OpenRouter({"free": ph.FREE_KEY, "paid": ph.PAID_KEY}, tmp_path / "ledger", transport=fake)
    for kind in ("writer", "repair", "text"):
        with pytest.raises(oc.ModelRefused, match="reviewer calls only"):
            client.call(QWEN, "s", "u", kind=kind)
    assert fake.calls == []


def test_the_test_profile_still_refuses_paid_catalog_models(catalog, profile):
    catalog(pin_qwen)
    profile("test")
    assert not oc.PAID
    for model in (QWEN, DEEPSEEK, GLM):
        with pytest.raises(oc.ModelRefused):
            oc.resolve(model)
