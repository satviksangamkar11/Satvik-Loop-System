"""P1a (2026-11-02): the OpenRouter adapter against a fake HTTP transport. No network, no model call.

Covers: the exact roster, reply checks, 429 kinds with Retry-After, retries and the provider breaker, the account-wide free ledger, the DeepSeek
budget and endpoint pricing, 402 and 401/403, redaction, and the private-code block."""
import calendar
import json
import socket
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
import openrouter_call as oc  # noqa: E402

FREE_KEY = "sk-or-v1-FREETEST000000000000"
PAID_KEY = "sk-or-v1-PAIDTEST000000000000"
KEYS = {"free": FREE_KEY, "paid": PAID_KEY}
DAY0 = calendar.timegm((2026, 11, 2, 0, 0, 0))
GLM, QWEN, INKLING, DEEPSEEK = oc.ROSTER["glm"], oc.ROSTER["qwen"], oc.ROSTER["inkling"], oc.ROSTER["deepseek"]
WRITER_OK = "FILE: pieces/calc.py\n```python\nx = 1\n```\n"
REVIEW_OK = "Looks fine.\n## Verdict: READY"
ENDPOINTS = {"data": {"endpoints": [
    {"tag": "deepseek/fp8", "provider_name": "DeepSeek", "pricing": {"prompt": "0.0000002", "completion": "0.0000004"}},
    {"tag": "novita/fp8", "provider_name": "Novita", "pricing": {"prompt": "0.0000003", "completion": "0.0000009"}},
]}}


class Clock:
    def __init__(self, start=DAY0):
        self.now, self.slept = float(start), []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class Fake:
    """Routes by URL suffix. Each queued item is a (status, headers, body) tuple, an exception to raise, or a function of the request body
    that returns one of those."""

    def __init__(self):
        self.routes, self.calls = {}, []

    def on(self, suffix, *items):
        self.routes.setdefault(suffix, []).extend(items)

    def __call__(self, method, url, headers, body, timeout):
        request = json.loads(body) if body else None
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}), "body": request, "timeout": timeout})
        for suffix, queue in self.routes.items():
            if url.endswith(suffix):
                if not queue:
                    raise AssertionError(f"no fake response left for {method} {url}")
                item = queue.pop(0)
                break
        else:
            raise AssertionError(f"no route for {method} {url}")
        if callable(item):
            item = item(request)
        if isinstance(item, BaseException):
            raise item
        return item

    def posts(self):
        return [c for c in self.calls if c["method"] == "POST"]


def ok(text, finish="stop", usage=None, model=None):
    def build(request):
        payload = {"id": "gen-1", "model": model or request["model"], "provider": "Fake",
                   "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": finish}],
                   "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5}}
        return 200, {}, json.dumps(payload).encode()
    return build


def body_of(payload):
    return lambda request: (200, {}, json.dumps(payload).encode())


def fail(status, message, headers=None, metadata=None):
    body = json.dumps({"error": {"code": status, "message": message, "metadata": metadata or {}}}).encode()
    return status, headers or {}, body


def raw(status, text, headers=None):
    return status, headers or {}, text.encode()


def key_reply(usage=0.0):
    return 200, {}, json.dumps({"data": {"label": "test", "limit": None, "usage": usage, "is_free_tier": True,
                                         "limit_remaining": None, "rate_limit": {"requests": 20, "interval": "10s"}}}).encode()


def client(tmp_path, fake, clock=None, keys=KEYS, **options):
    clock = clock or Clock()
    built = oc.OpenRouter(keys, tmp_path / "ledger", transport=fake, clock=clock.time, sleep=clock.sleep, rand=lambda: 0.0, **options)
    return built, clock


# ---- 1. roster and request shape

def test_exact_roster_is_the_allowlist():
    assert oc.ALLOWED == {GLM, QWEN, INKLING, DEEPSEEK}
    assert oc.PAID == {DEEPSEEK}


@pytest.mark.parametrize("name", [
    "openrouter/free", "z-ai/glm-5.3-flash", "deepseek/deepseek-v4.1-flash-1017:free", "unknown/model:free", "",
    ["z-ai/glm-5.3-flash:free", "qwen/qwen3.8-27b:free"], "z-ai/glm-5.3-flash:free,qwen/qwen3.8-27b:free", None,
])
def test_unknown_ids_and_fallback_lists_are_refused_before_any_request(tmp_path, name):
    fake = Fake()
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.ModelRefused):
        built.call(name, "system", "user")
    assert fake.calls == []


def test_request_names_one_model_and_carries_no_fallback_list(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok("hello"))
    built, _ = client(tmp_path, fake)
    text, info = built.call("glm", "system", "user")
    body = fake.posts()[0]["body"]
    assert text == "hello" and info["model"] == GLM
    assert body["model"] == GLM and "models" not in body and "route" not in body
    assert fake.posts()[0]["headers"]["Authorization"] == "Bearer " + FREE_KEY


# ---- 2. reply checks

@pytest.mark.parametrize("reply", [
    raw(200, ""),                                                   # not JSON
    raw(200, "[]"),                                                 # JSON, but not an object
    body_of({"model": GLM, "choices": []}),                         # no choices
    body_of({"model": GLM, "choices": [{"message": {"content": None}, "finish_reason": "stop"}]}),
    ok("   "),                                                      # empty
    ok("partial", finish="content_filter"),                         # not stop
])
def test_malformed_or_empty_replies_count_as_nothing(tmp_path, reply):
    fake = Fake()
    fake.on("/chat/completions", reply)
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.ReplyRejected):
        built.call("glm", "system", "user")
    assert len(fake.posts()) == 1


def test_model_mismatch_counts_as_unavailable_and_is_not_retried(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok(WRITER_OK, model=QWEN))
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.ProviderUnavailable, match="came from"):
        built.call("glm", "system", "user", kind="writer")
    assert len(fake.posts()) == 1 and built.breaker.streak[GLM] == 1


def test_writer_reply_needs_a_file_block(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok("I would rather not write files."), ok(WRITER_OK))
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.ReplyRejected, match="FILE"):
        built.call("glm", "system", "user", kind="writer")
    text, info = built.call("glm", "system", "user", kind="writer")
    assert text == WRITER_OK and info["finish"] == "stop"


@pytest.mark.parametrize("text", [
    "Looks fine.",                                   # no verdict at all
    "## Verdict: NONE",                              # NONE is never a reviewer's answer
    "## Verdict: READY\nthanks",                     # verdict is not the last line
    "## Verdict: MAYBE",
])
def test_reviewer_reply_must_end_with_a_real_verdict(tmp_path, text):
    fake = Fake()
    fake.on("/chat/completions", ok(text))
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.ReplyRejected, match="Verdict"):
        built.call("inkling", "system", "user", kind="reviewer")


@pytest.mark.parametrize("verdict", ["READY", "FIX FIRST"])
def test_reviewer_accepts_ready_or_fix_first(tmp_path, verdict):
    fake = Fake()
    fake.on("/chat/completions", ok(f"Notes.\n## Verdict: {verdict}"))
    built, _ = client(tmp_path, fake)
    assert built.call("inkling", "system", "user", kind="reviewer")[0].endswith(verdict)


def test_truncated_reply_is_retried_once_with_double_max_tokens(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok(WRITER_OK[:10], finish="length"), ok(WRITER_OK))
    built, _ = client(tmp_path, fake)
    text, info = built.call("glm", "system", "user", kind="writer", max_tokens=1000)
    posts = fake.posts()
    assert text == WRITER_OK and info["attempts"] == 2
    assert [p["body"]["max_tokens"] for p in posts] == [1000, 2000]


def test_truncated_twice_is_rejected(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok("FILE: a.py", finish="length"), ok("FILE: a.py", finish="length"))
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.ReplyRejected, match="cut off"):
        built.call("glm", "system", "user", kind="writer", max_tokens=1000)
    assert len(fake.posts()) == 2


# ---- 3. 429 kinds and Retry-After

def test_per_minute_429_waits_retry_after_and_is_not_a_provider_error(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", fail(429, "Rate limit exceeded: free-models-per-min", {"retry-after": "7"}), ok("hi"))
    built, clock = client(tmp_path, fake)
    assert built.call("glm", "system", "user")[0] == "hi"
    assert clock.slept == [7.0] and built.breaker.streak.get(GLM, 0) == 0


def test_daily_429_is_quota_exhausted_and_stays_so_without_a_new_snapshot(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", fail(429, "Rate limit exceeded: free-models-per-day"))
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.QuotaExhausted):
        built.call("glm", "system", "user")
    with pytest.raises(oc.QuotaExhausted):
        built.call("glm", "system", "user")
    assert len(fake.posts()) == 1


def test_upstream_429_is_a_provider_error_with_backoff(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", fail(429, "Provider returned error", metadata={"provider_name": "Chutes", "raw": "rate-limited upstream"}),
             ok("hi"))
    built, clock = client(tmp_path, fake)
    assert built.call("glm", "system", "user")[0] == "hi"
    assert clock.slept == [oc.BACKOFF_S[0]] and built.breaker.streak.get(GLM, 0) == 0


def test_upstream_429_uses_retry_after_when_it_is_longer(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", fail(429, "Provider returned error", {"retry-after": "30"}), ok("hi"))
    built, clock = client(tmp_path, fake)
    built.call("glm", "system", "user")
    assert clock.slept == [30.0]


def test_unclear_429_is_never_read_as_a_daily_limit():
    assert oc.classify_429("Too many requests") == "upstream"
    assert oc.classify_429("Rate limit exceeded: free-models-per-day") == "daily"
    assert oc.classify_429("Rate limit exceeded: free-models-per-min") == "minute"


def test_minute_429_gives_up_after_the_retry_limit(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", *[fail(429, "Rate limit exceeded: free-models-per-min") for _ in range(4)])
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.ProviderUnavailable, match="gave up after 4 attempts"):
        built.call("glm", "system", "user")
    assert len(fake.posts()) == 4 and built.breaker.streak.get(GLM, 0) == 0


def test_a_wait_longer_than_the_cap_ends_the_call_without_sleeping(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", fail(429, "Rate limit exceeded: free-models-per-min", {"retry-after": "3600"}))
    built, clock = client(tmp_path, fake)
    with pytest.raises(oc.ProviderUnavailable, match="longer than"):
        built.call("glm", "system", "user")
    assert clock.slept == [] and len(fake.posts()) == 1


# ---- 4. retries, breaker, timeouts

def test_two_timeouts_in_a_row_drop_the_model_for_the_job(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", socket.timeout("timed out"), socket.timeout("timed out"), ok("never reached"))
    built, clock = client(tmp_path, fake)
    with pytest.raises(oc.ProviderUnavailable, match="dropped"):
        built.call("glm", "system", "user")
    assert len(fake.posts()) == 2 and clock.slept == [oc.BACKOFF_S[0], oc.BACKOFF_S[1]]
    with pytest.raises(oc.ProviderUnavailable, match="dropped"):
        built.call("glm", "system", "user")
    assert len(fake.posts()) == 2


def test_one_server_error_is_retried_and_a_good_answer_resets_the_breaker(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", fail(503, "busy"), ok("hi"))
    built, clock = client(tmp_path, fake)
    assert built.call("glm", "system", "user")[0] == "hi"
    assert clock.slept == [oc.BACKOFF_S[0]] and built.breaker.streak[GLM] == 0


def test_timeout_is_passed_to_the_transport(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok("hi"))
    built, _ = client(tmp_path, fake)
    built.call("inkling", "system", "user", kind="text")
    assert fake.posts()[0]["timeout"] == 600


def test_client_error_is_not_retried(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", fail(400, "bad request"))
    built, clock = client(tmp_path, fake)
    with pytest.raises(oc.OpenRouterError, match="HTTP 400"):
        built.call("glm", "system", "user")
    assert len(fake.posts()) == 1 and clock.slept == []


# ---- 5. account-wide free ledger

def test_daily_cap_stops_the_next_call_before_it_is_sent(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok("a"), ok("b"), ok("c"))
    built, _ = client(tmp_path, fake, daily_cap=3)
    for _ in range(3):
        built.call("glm", "system", "user")
    with pytest.raises(oc.QuotaExhausted):
        built.call("glm", "system", "user")
    assert len(fake.posts()) == 3


def test_failed_attempts_count_against_the_ledger(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", raw(400, "bad"), fail(503, "busy"), ok("hi"))
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.OpenRouterError):
        built.call("glm", "system", "user")
    built.call("glm", "system", "user")
    used = json.loads((tmp_path / "ledger" / "openrouter_free_ledger.json").read_text(encoding="utf-8"))["used"]
    assert used == len(fake.posts()) == 3


def test_both_free_keys_draw_on_one_account_ledger(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok("a"), ok("b"), ok("c"))
    first, clock = client(tmp_path, fake, daily_cap=2)
    second, _ = client(tmp_path, fake, clock=clock, daily_cap=2, keys={"free": "sk-or-v1-SECONDFREE000000"})
    first.call("glm", "system", "user")
    second.call("qwen", "system", "user")
    with pytest.raises(oc.QuotaExhausted):
        first.call("glm", "system", "user")
    assert len(fake.posts()) == 2


def test_fifteen_a_minute_then_the_window_is_waited_out(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", *[ok("x") for _ in range(16)])
    built, clock = client(tmp_path, fake)
    for _ in range(15):
        built.call("glm", "system", "user")
    assert clock.slept == []
    built.call("glm", "system", "user")
    assert clock.slept == [60.0] and len(fake.posts()) == 16


def test_snapshot_from_the_provider_caps_the_day(tmp_path):
    ledger = oc.FreeLedger(tmp_path / "ledger", clock=Clock().time)
    ledger.set_snapshot(1)
    assert ledger.reserve() == 0.0
    with pytest.raises(oc.QuotaExhausted):
        ledger.reserve()


def test_a_new_utc_day_resets_the_count(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok("a"), ok("b"))
    built, clock = client(tmp_path, fake, daily_cap=1)
    built.call("glm", "system", "user")
    clock.now += 86400
    built.call("glm", "system", "user")
    assert len(fake.posts()) == 2


def test_ledger_uses_the_shared_key_ledger_lock(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok("a"))
    built, _ = client(tmp_path, fake)
    built.call("glm", "system", "user")
    assert (tmp_path / "ledger" / "key_ledger.lock").exists()


def test_unreadable_ledger_refuses_instead_of_guessing(tmp_path):
    fake = Fake()
    built, _ = client(tmp_path, fake)
    (tmp_path / "ledger").mkdir(parents=True, exist_ok=True)
    (tmp_path / "ledger" / "openrouter_free_ledger.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(oc.OpenRouterError, match="unreadable"):
        built.call("glm", "system", "user")
    assert fake.calls == []


def test_job_free_ceiling_is_enforced_before_the_ledger(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok("a"), ok("b"))
    built, _ = client(tmp_path, fake, budget=oc.JobBudget(free_requests=2))
    built.call("glm", "system", "user")
    built.call("glm", "system", "user")
    with pytest.raises(oc.BudgetExceeded):
        built.call("glm", "system", "user")
    assert len(fake.posts()) == 2


# ---- 6. DeepSeek: paid key, endpoint pricing, budget before sending

def paid_fake(reply):
    fake = Fake()
    fake.on("/endpoints", (200, {}, json.dumps(ENDPOINTS).encode()))
    fake.on("/chat/completions", reply)
    return fake


def test_deepseek_uses_the_paid_key_pinned_to_the_cheapest_endpoint(tmp_path):
    fake = paid_fake(ok("hi", usage={"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.00005}))
    built, _ = client(tmp_path, fake)
    _, info = built.call("deepseek", "system", "user", max_tokens=500)
    post = fake.posts()[0]
    assert post["headers"]["Authorization"] == "Bearer " + PAID_KEY
    assert post["body"]["provider"] == {"order": ["deepseek"], "allow_fallbacks": False}
    assert info["endpoint"] == "deepseek" and info["cost_usd"] == 0.00005
    assert built.budget.paid_spent == pytest.approx(0.00005) and built.budget.paid_reserved == pytest.approx(0.0)


def test_deepseek_budget_is_reserved_before_the_request_is_sent(tmp_path):
    seen = {}

    def check(request):
        seen["reserved"] = built.budget.paid_reserved
        return ok("hi")(request)

    fake = paid_fake(check)
    built, _ = client(tmp_path, fake)
    built.call("deepseek", "system", "user", max_tokens=500)
    expected = oc.estimate_cost("system", "user", 500, {"prompt": 0.0000002, "completion": 0.0000004})
    assert seen["reserved"] == pytest.approx(expected)


def test_cost_falls_back_to_tokens_at_the_endpoint_price(tmp_path):
    fake = paid_fake(ok("hi", usage={"prompt_tokens": 100, "completion_tokens": 20}))
    built, _ = client(tmp_path, fake)
    built.call("deepseek", "system", "user")
    assert built.budget.paid_spent == pytest.approx(100 * 0.0000002 + 20 * 0.0000004)


def test_a_failed_paid_attempt_keeps_its_estimate_as_spent(tmp_path):
    fake = paid_fake(fail(503, "busy"))
    fake.on("/chat/completions", ok("hi", usage={"prompt_tokens": 1, "completion_tokens": 1}))
    built, _ = client(tmp_path, fake)
    built.call("deepseek", "system", "user", max_tokens=500)
    first = oc.estimate_cost("system", "user", 500, {"prompt": 0.0000002, "completion": 0.0000004})
    assert built.budget.paid_spent == pytest.approx(first + 1 * 0.0000002 + 1 * 0.0000004)


def test_paid_estimate_over_the_job_ceiling_sends_nothing(tmp_path):
    fake = paid_fake(ok("hi"))
    built, _ = client(tmp_path, fake, budget=oc.JobBudget(paid_usd=0.00001))
    with pytest.raises(oc.BudgetExceeded):
        built.call("deepseek", "system", "user", max_tokens=4000)
    assert fake.posts() == []


def test_zero_paid_budget_authorizes_no_paid_call(tmp_path):
    fake = paid_fake(ok("hi"))
    built, _ = client(tmp_path, fake, budget=oc.JobBudget(paid_usd=0.0))
    with pytest.raises(oc.BudgetExceeded):
        built.call("deepseek", "system", "user", max_tokens=10)
    assert fake.posts() == []


def test_deepseek_without_a_live_price_is_refused(tmp_path):
    fake = Fake()
    fake.on("/endpoints", (200, {}, json.dumps({"data": {"endpoints": []}}).encode()))
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.ProviderUnavailable, match="no priced endpoint"):
        built.call("deepseek", "system", "user")
    assert fake.posts() == []


def test_deepseek_endpoint_read_failure_sends_nothing(tmp_path):
    fake = Fake()
    fake.on("/endpoints", fail(500, "down"))
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.ProviderUnavailable, match="unavailable"):
        built.call("deepseek", "system", "user")
    assert fake.posts() == []


def test_deepseek_without_the_paid_key_is_refused_and_free_models_never_use_it(tmp_path):
    fake = Fake()
    built, _ = client(tmp_path, fake, keys={"paid": PAID_KEY})
    with pytest.raises(oc.ModelRefused, match="no free key"):
        built.call("glm", "system", "user")
    with pytest.raises(oc.ModelRefused):
        client(tmp_path, fake, keys={"free": FREE_KEY})[0].call("deepseek", "system", "user")
    assert fake.calls == []


# ---- 7. 402, auth, redaction, privacy

def test_402_is_account_blocked_with_no_retry(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", fail(402, "Insufficient credits"))
    built, clock = client(tmp_path, fake)
    with pytest.raises(oc.AccountBlocked, match="402"):
        built.call("glm", "system", "user")
    assert len(fake.posts()) == 1 and clock.slept == []


@pytest.mark.parametrize("status", [401, 403])
def test_rejected_key_is_account_blocked(tmp_path, status):
    fake = Fake()
    fake.on("/chat/completions", fail(status, "no"))
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.AccountBlocked):
        built.call("glm", "system", "user")
    assert len(fake.posts()) == 1


def test_errors_never_carry_a_key_value(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", fail(400, f"your key {FREE_KEY} is wrong, Bearer abc.def"))
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.OpenRouterError) as info:
        built.call("glm", "system", "user")
    message = str(info.value)
    assert FREE_KEY not in message and "abc.def" not in message and "[REDACTED]" in message


def test_redact_removes_known_keys_and_key_shapes():
    text = oc.redact("a sk-or-v1-XYZ123 b PAIR-SECRET c Bearer tok.en", secrets=["PAIR-SECRET"])
    assert text == "a [REDACTED] b [REDACTED] c [REDACTED]"


@pytest.mark.parametrize("name", [GLM, QWEN, INKLING, DEEPSEEK])
def test_private_code_is_blocked_for_every_roster_model(tmp_path, name):
    fake = Fake()
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.ModelRefused, match="private"):
        built.call(name, "system", "user", private=True)
    assert fake.posts() == []


def test_private_route_is_used_only_when_approved_and_then_denies_training(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok("hi"))
    built, _ = client(tmp_path, fake, approved_private={GLM})
    built.call("glm", "system", "user", private=True)
    assert fake.posts()[0]["body"]["provider"] == {"data_collection": "deny"}
    with pytest.raises(oc.ModelRefused):
        built.call("qwen", "system", "user", private=True)


# ---- 8. keys and the probe

def test_keys_come_from_the_environment_or_the_secrets_folder(tmp_path):
    (tmp_path / "openrouter_free_key.txt").write_text("sk-or-v1-FILE-FREE\n", encoding="utf-8")
    assert oc.load_keys(secrets_dir=tmp_path, environ={"OPENROUTER_PAID_KEY": "sk-or-v1-ENV-PAID"}) == {
        "free": "sk-or-v1-FILE-FREE", "paid": "sk-or-v1-ENV-PAID"}
    assert oc.load_keys(secrets_dir=tmp_path, environ={"OPENROUTER_FREE_KEY": "env-wins"})["free"] == "env-wins"


def test_probe_makes_one_call_per_roster_model_and_prints_no_key(tmp_path):
    fake = Fake()
    fake.on("/key", key_reply(), key_reply(), key_reply(), key_reply())
    fake.on("/endpoints", (200, {}, json.dumps(ENDPOINTS).encode()))
    fake.on("/chat/completions", ok("OK"), ok("OK"), ok("OK"), ok("OK"))
    built, _ = client(tmp_path, fake)
    out = tmp_path / "probe.json"
    summary = oc.probe(built, str(out))
    assert [p["body"]["model"] for p in fake.posts()] == [GLM, QWEN, INKLING, DEEPSEEK]
    assert all(p["ok"] for p in summary["probes"].values())
    assert summary["probes"]["deepseek"]["endpoint"] == "deepseek"
    assert FREE_KEY not in out.read_text(encoding="utf-8") and PAID_KEY not in out.read_text(encoding="utf-8")
