"""P1a (2026-11-02): the OpenRouter adapter against a fake HTTP transport. No network, no model call.

Covers: the owner's roster (roles and exact IDs), refusal of anything else, reply checks, 429 kinds with Retry-After, retries and the provider
breaker, the account-wide free ledger, the repair model's paid budget and endpoint pricing, 402 and 401/403, redaction, and the private-code block.
The model IDs below are copied from the production roster in config/roster.json; test_roster_config.py checks the file itself.
The adapter runs under the production profile here, so the paid reviewer's calls can be tested."""
import calendar
import json
import os
import socket
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
import launch  # noqa: E402
import openrouter_call as oc  # noqa: E402

FREE_KEY = "sk-or-v1-FREETEST000000000000"
PAID_KEY = "sk-or-v1-PAIDTEST000000000000"
KEYS = {"free": FREE_KEY, "paid": PAID_KEY}
DAY0 = calendar.timegm((2026, 11, 2, 0, 0, 0))
DOTS = "dots-studio/dots-3-note-preview:free"
XING = "xingchen-agi/xing4.0-29b-a4b:free"
NORTH = "cohere/north-mini-code:free"
GLM_PAID = "z-ai/glm-5.3-flash"
REMOVED = ("deepseek/deepseek-v4.1-flash-1017", "z-ai/glm-5.3-flash:free", "qwen/qwen3.8-27b:free", "thinkingmachines/inkling-small:free")
WRITER_OK = "FILE: pieces/calc.py\n```python\nx = 1\n```\n"
REVIEW_OK = "Looks fine.\n## Verdict: READY"
ZAI_PRICE = {"prompt": 0.00000015, "completion": 0.0000005}   # docs/p1a_glm_endpoint.json, read 2026-11-02
# The cheaper Relace entry is listed first on purpose: the pin must ignore it (cheapest-endpoint logic is removed).
ENDPOINTS = {"data": {"endpoints": [
    {"tag": "relace/fp4", "provider_name": "Relace", "pricing": {"prompt": "0.0000000285", "completion": "0.0000000923"}},
    {"tag": "z-ai/fp8", "provider_name": "Z.AI", "pricing": {"prompt": "0.00000015", "completion": "0.0000005"}},
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


def ok(text, finish="stop", usage=None, model=None, provider="Z.AI"):
    def build(request):
        payload = {"id": "gen-1", "model": model or request["model"], "provider": provider,
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


@pytest.fixture(autouse=True)
def shared_home(tmp_path, monkeypatch):
    """Track L: every free call counts on the loop home (SATVIK_HOME); each test gets its own folder."""
    folder = tmp_path / "satvik_home"
    folder.mkdir()
    monkeypatch.setenv("SATVIK_HOME", str(folder))
    return folder


def client(tmp_path, fake, clock=None, keys=KEYS, **options):
    clock = clock or Clock()
    built = oc.OpenRouter(keys, tmp_path / "ledger", transport=fake, clock=clock.time, sleep=clock.sleep, rand=lambda: 0.0, **options)
    return built, clock


# ---- 1. roster and request shape

def test_a_paid_model_is_refused_by_its_own_id_and_is_called_only_through_its_role():
    assert oc.PAID and not (oc.PAID & oc.ALLOWED)
    assert all(model.endswith(":free") for model in oc.ALLOWED)
    for model in oc.PAID:
        with pytest.raises(oc.ModelRefused):
            oc.resolve(model)
    for role, model in oc.ROLES.items():
        if model in oc.PAID:
            assert oc.resolve(role) == model


@pytest.mark.parametrize("name", list(REMOVED))
def test_removed_models_are_refused_before_any_request(tmp_path, name):
    fake = Fake()
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.ModelRefused):
        built.call(name, "system", "user")
    assert fake.calls == []


@pytest.mark.parametrize("name", [
    "openrouter/free", "dots-studio/dots-3-note-preview", "xingchen-agi/xing4.0-29b-a4b", "cohere/north-mini-code",
    "z-ai/glm-5.3-flash", "reviewer_backup", "writer_c", "unknown/model:free", "",
    [DOTS, XING], f"{DOTS},{XING}", None,
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
    text, info = built.call("writer_a", "system", "user")
    body = fake.posts()[0]["body"]
    assert text == "hello" and info["model"] == DOTS
    assert body["model"] == DOTS and "models" not in body and "route" not in body
    assert fake.posts()[0]["headers"]["Authorization"] == "Bearer " + FREE_KEY


# ---- 2. reply checks

@pytest.mark.parametrize("reply", [
    raw(200, ""),                                                   # not JSON
    raw(200, "[]"),                                                 # JSON, but not an object
    body_of({"model": DOTS, "choices": []}),                        # no choices
    body_of({"model": DOTS, "choices": [{"message": {"content": None}, "finish_reason": "stop"}]}),
    ok("   "),                                                      # empty
    ok("partial", finish="content_filter"),                         # not stop
])
def test_malformed_or_empty_replies_count_as_nothing(tmp_path, reply):
    fake = Fake()
    fake.on("/chat/completions", reply)
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.ReplyRejected):
        built.call("writer_a", "system", "user")
    assert len(fake.posts()) == 1


def test_model_mismatch_counts_as_unavailable_and_is_not_retried(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok(WRITER_OK, model=XING))
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.ProviderUnavailable, match="came from"):
        built.call("writer_a", "system", "user", kind="writer")
    assert len(fake.posts()) == 1 and built.breaker.streak[DOTS] == 1


def test_writer_reply_needs_a_file_block(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok("I would rather not write files."), ok(WRITER_OK))
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.ReplyRejected, match="FILE"):
        built.call("writer_a", "system", "user", kind="writer")
    text, info = built.call("writer_a", "system", "user", kind="writer")
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
        built.call("reviewer_b", "system", "user", kind="reviewer")


@pytest.mark.parametrize("verdict", ["READY", "FIX FIRST"])
def test_reviewer_accepts_ready_or_fix_first(tmp_path, verdict):
    fake = Fake()
    fake.on("/chat/completions", ok(f"Notes.\n## Verdict: {verdict}"))
    built, _ = client(tmp_path, fake)
    assert built.call("reviewer_b", "system", "user", kind="reviewer")[0].endswith(verdict)


def test_truncated_reply_is_retried_once_at_the_retry_limit(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok(WRITER_OK[:10], finish="length"), ok(WRITER_OK))
    built, _ = client(tmp_path, fake)
    text, info = built.call("writer_a", "system", "user", kind="writer", max_tokens=1000)
    posts = fake.posts()
    assert text == WRITER_OK and info["attempts"] == 2
    assert [p["body"]["max_tokens"] for p in posts] == [1000, 72000]


def test_truncated_twice_is_rejected(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok("FILE: a.py", finish="length"), ok("FILE: a.py", finish="length"))
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.ReplyRejected, match="cut off"):
        built.call("writer_a", "system", "user", kind="writer", max_tokens=1000)
    assert len(fake.posts()) == 2


def _null_content(finish):
    def build(request):
        payload = {"id": "gen-1", "model": request["model"], "provider": "Z.AI",
                   "choices": [{"index": 0, "message": {"role": "assistant", "content": None, "reasoning": "thinking"}, "finish_reason": finish}],
                   "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        return 200, {}, json.dumps(payload).encode()
    return build


def test_null_content_cut_off_at_max_tokens_is_retried_with_reasoning_off_at_the_same_size(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", _null_content("length"), ok(WRITER_OK))
    built, _ = client(tmp_path, fake)
    text, info = built.call("writer_a", "system", "user", kind="writer", max_tokens=1000)
    assert text == WRITER_OK and info["attempts"] == 2
    first, second = (p["body"] for p in fake.posts())
    assert [first["max_tokens"], second["max_tokens"]] == [1000, 1000]     # R1: the budget does not grow
    assert "reasoning" in first and second["reasoning"] == {"enabled": False}   # R1: the retry turns reasoning off, explicitly


def test_null_content_with_another_finish_reason_is_rejected_without_a_retry(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", _null_content("content_filter"))
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.ReplyRejected, match="no message text"):
        built.call("writer_a", "system", "user", kind="writer", max_tokens=1000)
    assert len(fake.posts()) == 1


# ---- 3. 429 kinds and Retry-After

def test_per_minute_429_waits_retry_after_and_is_not_a_provider_error(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", fail(429, "Rate limit exceeded: free-models-per-min", {"retry-after": "7"}), ok("hi"))
    built, clock = client(tmp_path, fake)
    assert built.call("writer_a", "system", "user")[0] == "hi"
    assert clock.slept == [7.0] and built.breaker.streak.get(DOTS, 0) == 0


def test_daily_429_is_quota_exhausted_and_stays_so_without_a_new_snapshot(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", fail(429, "Rate limit exceeded: free-models-per-day"), fail(429, "Rate limit exceeded: free-models-per-day"))
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.QuotaExhausted):
        built.call("writer_a", "system", "user")
    with pytest.raises(oc.QuotaExhausted):
        built.call("writer_a", "system", "user")
    assert len(fake.posts()) == 2     # Track L: a daily 429 no longer caps the next call locally; the provider answers it


def test_upstream_429_is_a_provider_error_with_backoff(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", fail(429, "Provider returned error", metadata={"provider_name": "Chutes", "raw": "rate-limited upstream"}),
             ok("hi"))
    built, clock = client(tmp_path, fake)
    assert built.call("writer_a", "system", "user")[0] == "hi"
    assert clock.slept == [oc.BACKOFF_S[0]] and built.breaker.streak.get(DOTS, 0) == 0


def test_upstream_429_uses_retry_after_when_it_is_longer(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", fail(429, "Provider returned error", {"retry-after": "30"}), ok("hi"))
    built, clock = client(tmp_path, fake)
    built.call("writer_a", "system", "user")
    assert clock.slept == [30.0]


def test_counter_429_per_model_counts_minute_and_upstream_429s_not_daily(tmp_path, monkeypatch):
    monkeypatch.setenv("SATVIK_HOME", str(tmp_path / "home"))
    fake = Fake()
    fake.on("/chat/completions", fail(429, "Rate limit exceeded: free-models-per-min", {"retry-after": "7"}),
             fail(429, "Provider returned error", metadata={"raw": "rate-limited upstream"}), ok("hi"))
    built, _ = client(tmp_path, fake)
    model = oc.resolve("writer_a")
    assert built.call("writer_a", "system", "user")[0] == "hi"
    assert f"429:{model} = 2" in launch.counter_lines(tmp_path / "home")


def test_unclear_429_is_never_read_as_a_daily_limit():
    assert oc.classify_429("Too many requests") == "upstream"
    assert oc.classify_429("Rate limit exceeded: free-models-per-day") == "daily"
    assert oc.classify_429("Rate limit exceeded: free-models-per-min") == "minute"


def test_minute_429_gives_up_after_the_retry_limit(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", *[fail(429, "Rate limit exceeded: free-models-per-min") for _ in range(4)])
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.ProviderUnavailable, match="gave up after 4 attempts"):
        built.call("writer_a", "system", "user")
    assert len(fake.posts()) == 4 and built.breaker.streak.get(DOTS, 0) == 0


def test_a_wait_longer_than_the_cap_ends_the_call_without_sleeping(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", fail(429, "Rate limit exceeded: free-models-per-min", {"retry-after": "3600"}))
    built, clock = client(tmp_path, fake)
    with pytest.raises(oc.ProviderUnavailable, match="longer than"):
        built.call("writer_a", "system", "user")
    assert clock.slept == [] and len(fake.posts()) == 1


# ---- 4. retries, breaker, timeouts

def test_two_timeouts_in_a_row_drop_the_model_for_the_job(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", socket.timeout("timed out"), socket.timeout("timed out"), ok("never reached"))
    built, clock = client(tmp_path, fake)
    with pytest.raises(oc.ProviderUnavailable, match="dropped"):
        built.call("writer_a", "system", "user")
    assert len(fake.posts()) == 2 and clock.slept == [oc.BACKOFF_S[0], oc.BACKOFF_S[1]]
    with pytest.raises(oc.ProviderUnavailable, match="dropped"):
        built.call("writer_a", "system", "user")
    assert len(fake.posts()) == 2


def test_one_server_error_is_retried_and_a_good_answer_resets_the_breaker(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", fail(503, "busy"), ok("hi"))
    built, clock = client(tmp_path, fake)
    assert built.call("writer_a", "system", "user")[0] == "hi"
    assert clock.slept == [oc.BACKOFF_S[0]] and built.breaker.streak[DOTS] == 0


def test_timeout_is_passed_to_the_transport(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok("hi"))
    built, _ = client(tmp_path, fake)
    built.call("reviewer_b", "system", "user", kind="text")
    assert fake.posts()[0]["timeout"] == 600


def test_client_error_is_not_retried(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", fail(400, "bad request"))
    built, clock = client(tmp_path, fake)
    with pytest.raises(oc.OpenRouterError, match="HTTP 400"):
        built.call("writer_a", "system", "user")
    assert len(fake.posts()) == 1 and clock.slept == []


# ---- 5. account-wide free ledger

def test_daily_cap_stops_the_next_call_before_it_is_sent(tmp_path, monkeypatch):
    monkeypatch.setattr(oc._shared_limits(), "FREE_DAILY_CAP", 3)
    fake = Fake()
    fake.on("/chat/completions", ok("a"), ok("b"), ok("c"))
    built, _ = client(tmp_path, fake)
    for _ in range(3):
        built.call("writer_a", "system", "user")
    with pytest.raises(oc.QuotaExhausted):
        built.call("writer_a", "system", "user")
    assert len(fake.posts()) == 3


def test_failed_attempts_count_against_the_ledger(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", raw(400, "bad"), fail(503, "busy"), ok("hi"))
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.OpenRouterError):
        built.call("writer_a", "system", "user")
    built.call("writer_a", "system", "user")
    assert oc._shared_limits().usage()["free_today"] == len(fake.posts()) == 3


def test_both_free_keys_draw_on_one_account_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(oc._shared_limits(), "FREE_DAILY_CAP", 2)
    fake = Fake()
    fake.on("/chat/completions", ok("a"), ok("b"), ok("c"))
    first, clock = client(tmp_path, fake)
    second, _ = client(tmp_path, fake, clock=clock, keys={"free": "sk-or-v1-SECONDFREE000000"})
    first.call("writer_a", "system", "user")
    second.call("writer_b", "system", "user")
    with pytest.raises(oc.QuotaExhausted):
        first.call("writer_a", "system", "user")
    assert len(fake.posts()) == 2


def test_fifteen_a_minute_then_the_window_is_refused_at_the_unit_deadline(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", *[ok("x") for _ in range(16)])
    built, clock = client(tmp_path, fake)
    for _ in range(15):
        built.call("writer_a", "system", "user")
    built.set_job_deadline(5)
    with pytest.raises(oc.ProviderUnavailable, match="minute window"):
        built.call("writer_a", "system", "user")
    assert len(fake.posts()) == 15


def test_free_calls_count_on_the_shared_ledger_not_the_key_file(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok("a"))
    built, _ = client(tmp_path, fake)
    built.call("writer_a", "system", "user")
    assert oc._shared_limits().usage()["free_today"] == 1
    assert not (tmp_path / "ledger" / "openrouter_free_ledger.json").exists()


def test_an_unreadable_key_file_does_not_stop_a_call(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok("a"))
    built, _ = client(tmp_path, fake)
    (tmp_path / "ledger").mkdir(parents=True, exist_ok=True)
    (tmp_path / "ledger" / "openrouter_free_ledger.json").write_text("{not json", encoding="utf-8")
    text, _ = built.call("writer_a", "system", "user")
    assert text == "a" and len(fake.posts()) == 1


def test_job_free_ceiling_is_enforced_before_the_ledger(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok("a"), ok("b"))
    built, _ = client(tmp_path, fake, budget=oc.JobBudget(free_requests=2))
    built.call("writer_a", "system", "user")
    built.call("writer_a", "system", "user")
    with pytest.raises(oc.BudgetExceeded):
        built.call("writer_a", "system", "user")
    assert len(fake.posts()) == 2


# ---- 6. GLM reviewer (paid key): Z.AI pin, review ceiling reserved before sending, never a writer

def paid_fake(reply):
    fake = Fake()
    fake.on("/endpoints", (200, {}, json.dumps(ENDPOINTS).encode()))
    fake.on("/chat/completions", reply)
    return fake


def test_glm_is_pinned_to_z_ai_even_when_a_cheaper_endpoint_exists(tmp_path):
    fake = paid_fake(ok(REVIEW_OK, usage={"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.00005}))
    built, _ = client(tmp_path, fake)
    _, info = built.call("reviewer_a", "system", "user", kind="reviewer")
    post = fake.posts()[0]
    assert post["headers"]["Authorization"] == "Bearer " + PAID_KEY
    assert post["body"]["model"] == GLM_PAID
    assert post["body"]["provider"] == {"order": ["z-ai"], "only": ["z-ai"], "allow_fallbacks": False}
    assert info["endpoint"] == "z-ai" and info["provider"] == "Z.AI" and info["cost_usd"] == 0.00005
    assert built.budget.review_spent == pytest.approx(0.00005) and built.budget.review_reserved == pytest.approx(0.0)


def test_glm_is_never_given_a_writer_or_a_text_call_and_nothing_is_sent(tmp_path):
    fake = paid_fake(ok(WRITER_OK))
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.ModelRefused):
        built.call("reviewer_a", "system", "user", kind="writer")
    with pytest.raises(oc.ModelRefused):
        built.call("reviewer_a", "system", "user", kind="text")
    assert fake.calls == []


def test_glm_review_budget_is_reserved_before_the_request_is_sent(tmp_path):
    seen = {}

    def check(request):
        seen["reserved"] = built.budget.review_reserved
        return ok(REVIEW_OK)(request)

    fake = paid_fake(check)
    built, _ = client(tmp_path, fake)
    built.call("reviewer_a", "system", "user", kind="reviewer")
    # 10 characters of prompt -> ceil(10 / 3) = 4 prompt tokens, plus the 16,000-token output allowance
    assert seen["reserved"] == pytest.approx(4 * 0.15e-6 + 16000 * 0.50e-6)
    assert oc.review_reservation("system", "user", 16000, oc.PRICING[GLM_PAID]) == pytest.approx(4 * 0.15e-6 + 16000 * 0.50e-6)


def test_cost_falls_back_to_tokens_at_the_spec_prices(tmp_path):
    fake = paid_fake(ok(REVIEW_OK, usage={"prompt_tokens": 100, "completion_tokens": 20}))
    built, _ = client(tmp_path, fake)
    built.call("reviewer_a", "system", "user", kind="reviewer")
    assert built.budget.review_spent == pytest.approx(100 * 0.15e-6 + 20 * 0.50e-6)


def test_a_failed_glm_attempt_keeps_its_reservation_as_spent(tmp_path):
    fake = paid_fake(fail(503, "busy"))
    fake.on("/chat/completions", ok(REVIEW_OK, usage={"prompt_tokens": 1, "completion_tokens": 1}))
    built, _ = client(tmp_path, fake)
    built.call("reviewer_a", "system", "user", kind="reviewer")
    first = oc.review_reservation("system", "user", 16000, oc.PRICING[GLM_PAID])
    assert built.budget.review_spent == pytest.approx(first + 1 * 0.15e-6 + 1 * 0.50e-6)


def test_glm_call_over_the_review_ceiling_sends_nothing(tmp_path):
    fake = paid_fake(ok(REVIEW_OK))
    built, _ = client(tmp_path, fake, budget=oc.JobBudget(review_usd=0.00001))
    with pytest.raises(oc.ReviewBudgetExceeded):
        built.call("reviewer_a", "system", "user", kind="reviewer")
    assert fake.posts() == []


def test_zero_review_budget_authorizes_no_glm_call(tmp_path):
    fake = paid_fake(ok(REVIEW_OK))
    built, _ = client(tmp_path, fake, budget=oc.JobBudget(review_usd=0.0))
    with pytest.raises(oc.ReviewBudgetExceeded):
        built.call("reviewer_a", "system", "user", kind="reviewer")
    assert fake.posts() == []


def test_the_call_that_would_pass_the_five_cent_ceiling_is_refused_before_sending(tmp_path):
    spend = {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.009}     # each real call costs $0.009
    fake = paid_fake(ok(REVIEW_OK, usage=spend))
    fake.on("/chat/completions", *[ok(REVIEW_OK, usage=spend)] * 4)           # five calls: $0.045 spent
    built, _ = client(tmp_path, fake)
    for _ in range(5):
        built.call("reviewer_a", "system", "user", kind="reviewer")
    with pytest.raises(oc.ReviewBudgetExceeded):                             # $0.045 + the next worst case ($0.008) passes $0.05
        built.call("reviewer_a", "system", "user", kind="reviewer")
    assert len(fake.posts()) == 5


def test_glm_without_a_z_ai_endpoint_is_refused(tmp_path):
    fake = Fake()
    fake.on("/endpoints", (200, {}, json.dumps({"data": {"endpoints": [ENDPOINTS["data"]["endpoints"][0]]}}).encode()))
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.ProviderUnavailable, match="Z.AI"):
        built.call("reviewer_a", "system", "user", kind="reviewer")
    assert fake.posts() == []


def test_glm_endpoint_read_failure_sends_nothing(tmp_path):
    fake = Fake()
    fake.on("/endpoints", (503, {}, b"down"))
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.ProviderUnavailable):
        built.call("reviewer_a", "system", "user", kind="reviewer")
    assert fake.posts() == []


# ---- 7. 402, auth, redaction, privacy

def test_402_is_account_blocked_with_no_retry(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", fail(402, "Insufficient credits"))
    built, clock = client(tmp_path, fake)
    with pytest.raises(oc.AccountBlocked, match="402"):
        built.call("writer_a", "system", "user")
    assert len(fake.posts()) == 1 and clock.slept == []


@pytest.mark.parametrize("status", [401])
def test_rejected_key_is_account_blocked(tmp_path, status):
    fake = Fake()
    fake.on("/chat/completions", fail(status, "no"))
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.AccountBlocked):
        built.call("writer_a", "system", "user")
    assert len(fake.posts()) == 1


def test_errors_never_carry_a_key_value(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", fail(400, f"your key {FREE_KEY} is wrong, Bearer abc.def"))
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.OpenRouterError) as info:
        built.call("writer_a", "system", "user")
    message = str(info.value)
    assert FREE_KEY not in message and "abc.def" not in message and "[REDACTED]" in message


def test_redact_removes_known_keys_and_key_shapes():
    text = oc.redact("a sk-or-v1-XYZ123 b PAIR-SECRET c Bearer tok.en", secrets=["PAIR-SECRET"])
    assert text == "a [REDACTED] b [REDACTED] c [REDACTED]"


@pytest.mark.parametrize("role", ["writer_a", "writer_b", "reviewer_a", "reviewer_b"])
def test_private_code_is_blocked_for_every_roster_role(tmp_path, role):
    fake = Fake()
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.ModelRefused, match="private"):
        built.call(role, "system", "user", private=True)
    assert fake.posts() == []


def test_private_route_is_used_only_when_approved_and_then_denies_training(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok("hi"))
    built, _ = client(tmp_path, fake, approved_private={DOTS})
    built.call("writer_a", "system", "user", private=True)
    assert fake.posts()[0]["body"]["provider"] == {"data_collection": "deny"}
    with pytest.raises(oc.ModelRefused):
        built.call("writer_b", "system", "user", private=True)


# ---- 8. keys and the probe

def test_keys_come_from_the_environment_or_the_secrets_folder(tmp_path):
    (tmp_path / "openrouter_free_key.txt").write_text("sk-or-v1-FILE-FREE\n", encoding="utf-8")
    assert oc.load_keys(secrets_dir=tmp_path, environ={"OPENROUTER_PAID_KEY": "sk-or-v1-ENV-PAID"}) == {
        "free": "sk-or-v1-FILE-FREE", "paid": "sk-or-v1-ENV-PAID"}
    assert oc.load_keys(secrets_dir=tmp_path, environ={"OPENROUTER_FREE_KEY": "env-wins"})["free"] == "env-wins"


def test_probe_makes_one_free_call_per_probe_model_and_never_calls_glm(tmp_path):
    fake = Fake()
    fake.on("/key", key_reply(), key_reply(), key_reply(), key_reply())     # the free and paid key states, before and after
    fake.on("/chat/completions", ok("OK"), ok("OK"), ok("OK"))
    built, _ = client(tmp_path, fake)
    out = tmp_path / "probe.json"
    summary = oc.probe(built, str(out))
    posts = fake.posts()
    assert [p["body"]["model"] for p in posts] == [DOTS, XING, NORTH]
    assert all(p["headers"]["Authorization"] == "Bearer " + FREE_KEY for p in posts)
    assert set(summary["probes"]) == {"writer_a", "writer_b", "reviewer_b"}
    assert all(p["ok"] for p in summary["probes"].values())
    assert FREE_KEY not in out.read_text(encoding="utf-8") and PAID_KEY not in out.read_text(encoding="utf-8")


# ---- 9. P1a.2: owner limits, reasoning cap, sampling, reasoning tokens

def test_writer_defaults_are_the_owner_limits(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok(WRITER_OK))
    built, _ = client(tmp_path, fake)
    built.call("writer_a", "system", "user", kind="writer")
    post = fake.posts()[0]
    assert post["body"]["max_tokens"] == 40000 and post["timeout"] == 1200


def test_reviewer_defaults_are_the_owner_limits(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok(REVIEW_OK))
    built, _ = client(tmp_path, fake)
    built.call("reviewer_b", "system", "user", kind="reviewer")
    post = fake.posts()[0]
    assert post["body"]["max_tokens"] == 16000 and post["timeout"] == 600


def test_glm_defaults_are_the_reviewer_limits(tmp_path):
    fake = paid_fake(ok(REVIEW_OK))
    built, _ = client(tmp_path, fake)
    built.call("reviewer_a", "system", "user", kind="reviewer")
    post = fake.posts()[0]
    assert post["body"]["max_tokens"] == 16000 and post["timeout"] == 600


def test_retry_after_truncation_uses_the_retry_limits(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok(WRITER_OK[:10], finish="length"), ok(WRITER_OK))
    built, _ = client(tmp_path, fake)
    built.call("writer_a", "system", "user", kind="writer")
    posts = fake.posts()
    assert [p["body"]["max_tokens"] for p in posts] == [40000, 72000]
    assert [p["timeout"] for p in posts] == [1200, 1800]


def test_every_request_carries_the_reasoning_cap(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok(WRITER_OK), ok(REVIEW_OK))
    built, _ = client(tmp_path, fake)
    built.call("writer_a", "system", "user", kind="writer")
    built.call("reviewer_b", "system", "user", kind="reviewer")
    assert all(p["body"]["reasoning"] == {"max_tokens": 8000} for p in fake.posts())


def test_xing_uses_its_sampling_settings_and_the_others_do_not(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok(WRITER_OK), ok(WRITER_OK))
    built, _ = client(tmp_path, fake)
    built.call("writer_b", "system", "user", kind="writer")
    built.call("writer_a", "system", "user", kind="writer")
    xing, dots = fake.posts()[0]["body"], fake.posts()[1]["body"]
    assert (xing["temperature"], xing["top_p"], xing["repetition_penalty"]) == (0.2, 0.95, 1.05)
    assert dots["temperature"] == 0.1 and "top_p" not in dots and "repetition_penalty" not in dots


def test_reasoning_tokens_are_recorded_and_a_thinking_overrun_is_not_rejected(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok(WRITER_OK, usage={"prompt_tokens": 10, "completion_tokens": 9100,
                                                       "completion_tokens_details": {"reasoning_tokens": 9000}}))
    built, _ = client(tmp_path, fake)
    text, info = built.call("writer_a", "system", "user", kind="writer")
    assert text == WRITER_OK and info["reasoning_tokens"] == 9000


def test_reasoning_tokens_are_none_when_the_provider_does_not_report_them(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok(WRITER_OK))
    built, _ = client(tmp_path, fake)
    assert built.call("writer_a", "system", "user", kind="writer")[1]["reasoning_tokens"] is None


# ---- 10. P1a.3: GLM pinned to Z.AI, wrong provider refused

def test_reply_from_another_provider_is_unavailable_and_its_spend_counts(tmp_path):
    fake = paid_fake(ok("hi", provider="Relace", usage={"prompt_tokens": 100, "completion_tokens": 20}))
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.ProviderUnavailable, match="provider"):
        built.call("reviewer_a", "system", "user", kind="reviewer", max_tokens=500)
    assert built.budget.review_spent == pytest.approx(100 * ZAI_PRICE["prompt"] + 20 * ZAI_PRICE["completion"])
    assert built.budget.review_reserved == pytest.approx(0.0) and built.breaker.streak[GLM_PAID] == 1


def test_wrong_provider_reply_is_never_returned_as_an_answer(tmp_path):
    fake = paid_fake(ok("hi", provider="Relace"))
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.ProviderUnavailable):
        built.call("reviewer_a", "system", "user", kind="reviewer", max_tokens=500)
    assert len(fake.posts()) == 1


# ---- 11. final roster: the GLM review ceiling, every failed attempt counted, no repair rounds

def test_review_ceiling_is_five_cents_and_there_is_no_repair_round_or_paid_usd_budget():
    assert oc.JOB_REVIEW_USD == 0.05 and oc.JobBudget().review_usd == 0.05
    assert not hasattr(oc, "PAID_ROUNDS") and not hasattr(oc, "PAID_ATTEMPTS") and not hasattr(oc, "JOB_PAID_USD")


def test_failed_glm_attempts_all_count_toward_the_review_ceiling(tmp_path):
    limit = fail(429, "Rate limit exceeded: free-models-per-min", {"retry-after": "1"})
    fake = paid_fake(limit)
    fake.on("/chat/completions", limit, limit, limit)
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.ProviderUnavailable, match="gave up after 4 attempts"):
        built.call("reviewer_a", "system", "user", kind="reviewer")
    assert len(fake.posts()) == 4
    assert built.budget.review_spent == pytest.approx(4 * oc.review_reservation("system", "user", 16000, oc.PRICING[GLM_PAID]))
    assert built.budget.review_reserved == pytest.approx(0.0)


def test_reservation_over_the_ceiling_is_refused_before_sending(tmp_path):
    fake = paid_fake(ok(REVIEW_OK))
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.ReviewBudgetExceeded):
        built.call("reviewer_a", "system", "x" * 4_000_000, kind="reviewer")
    assert fake.posts() == []


# ---- 12. P1a.5: key separation

def test_free_roles_use_the_free_key_and_glm_uses_the_paid_key_when_both_are_loaded(tmp_path):
    fake = paid_fake(ok(WRITER_OK))
    fake.on("/chat/completions", ok(REVIEW_OK))
    built, _ = client(tmp_path, fake)
    built.call("writer_a", "system", "user", kind="writer")
    built.call("reviewer_a", "system", "user", kind="reviewer")
    assert [p["headers"]["Authorization"] for p in fake.posts()] == ["Bearer " + FREE_KEY, "Bearer " + PAID_KEY]


# ---- 13. P1a.6: key snapshot before each job, quota rule

def test_begin_job_saves_the_full_key_json_and_uses_the_provider_quota(tmp_path):
    body = {"data": {"is_free_tier": False, "limit": 2, "free_model_daily_requests": {"used": 9, "limit": 1000, "remaining": 991}}}
    fake = Fake()
    fake.on("/key", (200, {}, json.dumps(body).encode()))
    built, _ = client(tmp_path, fake, keys={"free": FREE_KEY})
    summary = built.begin_job()
    saved = list((tmp_path / "ledger" / "key_snapshots").glob("key_snapshot_free_*.json"))
    assert len(saved) == 1 and json.loads(saved[0].read_text(encoding="utf-8")) == body
    assert not (tmp_path / "ledger" / "openrouter_free_ledger.json").exists()     # the snapshot is evidence only
    assert summary["free"]["quota"] == "provider"


def test_without_a_quota_field_a_paid_account_is_saved_with_a_local_note(tmp_path):
    fake = Fake()
    fake.on("/key", (200, {}, json.dumps({"data": {"is_free_tier": False, "limit": 2}}).encode()))
    built, _ = client(tmp_path, fake, keys={"free": FREE_KEY})
    summary = built.begin_job()
    assert summary["free"]["quota"] == "local"
    assert "free_model_daily_requests absent" in summary["free"]["note"]


def test_begin_job_saves_the_paid_key_state_without_touching_the_free_ledger(tmp_path):
    fake = Fake()
    fake.on("/key", (200, {}, json.dumps({"data": {"is_free_tier": False, "limit": 2}}).encode()))
    built, _ = client(tmp_path, fake, keys={"paid": PAID_KEY})
    built.begin_job()
    assert list((tmp_path / "ledger" / "key_snapshots").glob("key_snapshot_paid_*.json"))
    assert not (tmp_path / "ledger" / "openrouter_free_ledger.json").exists()


# ---- 13b. the day's free cap: the live allowance from GET /api/v1/key, the fallback when it is not read

def key_with_allowance(limit):
    return (200, {}, json.dumps({"data": {"is_free_tier": True, "limit": None,
                                          "free_model_daily_requests": {"used": 0, "limit": limit, "remaining": limit}}}).encode())


def test_the_live_daily_allowance_is_the_days_free_cap(tmp_path):
    fake = Fake()
    fake.on("/key", key_with_allowance(3))
    fake.on("/chat/completions", ok("a"), ok("b"), ok("c"))
    built, _ = client(tmp_path, fake, keys={"free": FREE_KEY})
    summary = built.begin_job()
    assert summary["free"]["cap"] == 3 and summary["free"]["cap_source"] == "live"
    for _ in range(3):
        built.call("writer_a", "system", "user")
    with pytest.raises(oc.QuotaExhausted, match=r"cap 3 \(live\)"):
        built.call("writer_a", "system", "user")
    assert len(fake.posts()) == 3
    assert oc._shared_limits().usage()["free_cap"] == 3


def test_the_key_limit_at_the_top_level_is_not_the_daily_allowance(tmp_path):
    fake = Fake()
    fake.on("/key", (200, {}, json.dumps({"data": {"is_free_tier": False, "limit": 2}}).encode()))
    built, _ = client(tmp_path, fake, keys={"free": FREE_KEY})
    summary = built.begin_job()
    assert summary["free"]["cap"] == 1000 and summary["free"]["cap_source"] == "fallback"
    assert oc._shared_limits().usage()["free_cap_source"] == "fallback"


def test_a_failed_free_key_read_records_the_fallback_and_the_job_goes_on(tmp_path):
    fake = Fake()
    fake.on("/key", fail(503, "busy"))
    fake.on("/chat/completions", ok("a"))
    built, _ = client(tmp_path, fake, keys={"free": FREE_KEY})
    summary = built.begin_job()
    assert summary["free"]["saved"] is False and summary["free"]["quota"] == "unavailable"
    assert summary["free"]["cap"] == 1000 and summary["free"]["cap_source"] == "fallback"
    assert not list((tmp_path / "ledger" / "key_snapshots").glob("key_snapshot_free_*.json"))
    built.call("writer_a", "system", "user")
    assert len(fake.posts()) == 1


def test_a_later_read_replaces_the_cap_and_a_failed_read_goes_back_to_the_fallback(tmp_path):
    fake = Fake()
    fake.on("/key", key_with_allowance(3), fail(503, "busy"))
    built, _ = client(tmp_path, fake, keys={"free": FREE_KEY})
    built.begin_job()
    assert oc._shared_limits().usage()["free_cap"] == 3
    built.begin_job()
    assert oc._shared_limits().usage()["free_cap"] == 1000
    assert oc._shared_limits().usage()["free_cap_source"] == "fallback"


def test_the_reviewer_retry_check_counts_against_the_live_cap(tmp_path):
    fake = Fake()
    fake.on("/key", key_with_allowance(1))
    fake.on("/chat/completions", upstream_429(), ok(REVIEW_OK))
    built, clock = client(tmp_path, fake, keys={"free": FREE_KEY})
    built.begin_job()
    with pytest.raises(oc.QuotaExhausted):
        built.call("reviewer_b", "system", "user", kind="reviewer")
    assert len(fake.posts()) == 1 and clock.slept == []


def test_the_probe_evidence_holds_the_daily_allowance(tmp_path):
    fake = Fake()
    fake.on("/key", key_with_allowance(3), key_with_allowance(3))
    fake.on("/chat/completions", ok("OK"), ok("OK"), ok("OK"))
    built, _ = client(tmp_path, fake, keys={"free": FREE_KEY})
    summary = oc.probe(built)
    assert summary["key_before"]["free"]["free_model_daily_requests"]["limit"] == 3


# ---- 14. P1a.7: 403 is a model refusal, not an account block

def test_403_is_a_model_refusal_not_an_account_block_and_the_other_roles_still_work(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", fail(403, "model refused"), ok(WRITER_OK))
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.ModelDenied, match="403"):
        built.call("writer_a", "system", "user", kind="writer")
    assert not issubclass(oc.ModelDenied, oc.AccountBlocked) and len(fake.posts()) == 1
    assert built.call("writer_b", "system", "user", kind="writer")[1]["model"] == XING
    with pytest.raises(oc.ProviderUnavailable, match="dropped"):
        built.call("writer_a", "system", "user", kind="writer")


# ---- 5. reviewer upstream 429 window (patch 2026-11-02): reviewers only, 15/30/60/120/180 s, Retry-After, 600 s review timeout

def upstream_429(headers=None):
    return fail(429, "Provider returned error", headers, metadata={"raw": "rate-limited upstream"})


def test_reviewer_upstream_429_three_times_then_ready_passes_with_three_logged_retries(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", upstream_429(), upstream_429(), upstream_429(), ok(REVIEW_OK))
    built, clock = client(tmp_path, fake)
    text, _ = built.call("reviewer_b", "system", "user", kind="reviewer")
    assert text.endswith("## Verdict: READY")
    assert clock.slept == [15.0, 30.0, 60.0]
    assert [r["wait_s"] for r in built.retry_log] == [15.0, 30.0, 60.0]
    assert [r["attempt"] for r in built.retry_log] == [2, 3, 4]
    assert all(r["model"] == NORTH and "rate-limited upstream" in r["error"] for r in built.retry_log)
    assert built.breaker.streak.get(NORTH, 0) == 0


def test_reviewer_upstream_429_on_every_call_ends_the_window_and_drops_the_model(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", *[upstream_429() for _ in range(6)])
    built, clock = client(tmp_path, fake)
    with pytest.raises(oc.ProviderUnavailable, match="window is used up") as caught:
        built.call("reviewer_b", "system", "user", kind="reviewer")
    assert len(fake.posts()) == 6 and clock.slept == [15.0, 30.0, 60.0, 120.0, 180.0]
    assert "rate-limited upstream" in str(caught.value)
    assert built.breaker.open(NORTH)


def test_retry_after_on_the_first_retry_sets_the_wait(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", upstream_429({"retry-after": "90"}), ok(REVIEW_OK))
    built, clock = client(tmp_path, fake)
    built.call("reviewer_b", "system", "user", kind="reviewer")
    assert clock.slept == [90.0] and built.retry_log[0]["wait_s"] == 90.0


def test_daily_limit_429_on_a_reviewer_stops_at_once(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", fail(429, "Rate limit exceeded: free-models-per-day"), ok(REVIEW_OK))
    built, clock = client(tmp_path, fake)
    with pytest.raises(oc.QuotaExhausted):
        built.call("reviewer_b", "system", "user", kind="reviewer")
    assert len(fake.posts()) == 1 and clock.slept == []


def test_only_two_free_requests_left_in_the_job_allow_two_retries_then_the_slot_fails(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", *[upstream_429() for _ in range(5)])
    built, clock = client(tmp_path, fake, budget=oc.JobBudget(free_requests=3, review_usd=0))
    with pytest.raises(oc.ProviderUnavailable, match="free requests"):
        built.call("reviewer_b", "system", "user", kind="reviewer")
    assert len(fake.posts()) == 3 and clock.slept == [15.0, 30.0]
    assert len(built.retry_log) == 2


def test_a_retry_is_not_sent_when_the_daily_count_has_no_room(tmp_path, monkeypatch):
    monkeypatch.setattr(oc._shared_limits(), "FREE_DAILY_CAP", 1)
    fake = Fake()
    fake.on("/chat/completions", upstream_429(), ok(REVIEW_OK))
    built, clock = client(tmp_path, fake)
    with pytest.raises(oc.QuotaExhausted):
        built.call("reviewer_b", "system", "user", kind="reviewer")
    assert len(fake.posts()) == 1 and clock.slept == []


def test_a_retry_that_would_start_after_the_unit_deadline_is_not_sent(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", upstream_429(), ok(REVIEW_OK))
    built, clock = client(tmp_path, fake)
    built.set_job_deadline(10)
    with pytest.raises(oc.ProviderUnavailable, match="unit deadline"):
        built.call("reviewer_b", "system", "user", kind="reviewer")
    assert len(fake.posts()) == 1 and clock.slept == []


def test_a_next_wait_past_the_600_second_review_timeout_ends_the_retries(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", upstream_429({"retry-after": "580"}), upstream_429(), ok(REVIEW_OK))
    built, clock = client(tmp_path, fake)
    with pytest.raises(oc.ProviderUnavailable, match="review timeout"):
        built.call("reviewer_b", "system", "user", kind="reviewer")
    assert len(fake.posts()) == 2 and clock.slept == [580.0]


def test_a_reviewer_server_error_still_counts_toward_the_two_error_rule(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", fail(503, "busy"), fail(503, "busy"), ok(REVIEW_OK))
    built, clock = client(tmp_path, fake)
    with pytest.raises(oc.ProviderUnavailable, match="dropped"):
        built.call("reviewer_b", "system", "user", kind="reviewer")
    assert len(fake.posts()) == 2 and clock.slept == [oc.BACKOFF_S[0], oc.BACKOFF_S[1]]


def test_a_writer_upstream_429_waits_in_the_reviewer_window_and_is_not_a_breaker_failure(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", upstream_429(), upstream_429(), ok(WRITER_OK))
    built, clock = client(tmp_path, fake)
    text, _ = built.call("writer_a", "system", "user", kind="writer")
    assert text == WRITER_OK
    assert clock.slept == [oc.REVIEWER_429_WAITS_S[0], oc.REVIEWER_429_WAITS_S[1]] and len(fake.posts()) == 3   # R1: 15 s then 30 s (jitter 0 here)
    assert built.breaker.streak.get(DOTS, 0) == 0


@pytest.fixture(autouse=True)
def _production_profile(profile):
    """Every test in this file runs on the production roster (the paid GLM reviewer lives there). Set per test, never at import."""
    profile("production")


# ---- 10. probe roles follow the active profile (read per call)

def test_step9_probe_roles_follow_the_active_profile_and_skip_the_paid_reviewer(profile):
    profile("test")
    assert oc.probe_roles() == ["writer_a", "writer_b", "reviewer_b", "reviewer_a"]      # the test roster's reviewer_a is free nemotron
    profile("production")
    assert oc.probe_roles() == ["writer_a", "writer_b", "reviewer_b"]                    # the production reviewer_a is paid GLM: never probed


def test_step9_models_matching_no_role_exit_nonzero_without_a_call(tmp_path, monkeypatch, capsys):
    fake = Fake()
    monkeypatch.setattr(oc, "default_transport", fake)
    monkeypatch.setattr(oc, "load_keys", lambda: {"free": FREE_KEY, "paid": PAID_KEY})
    code = oc.main(["probe", "--ledger", str(tmp_path / "ledger"), "--models", "glm,planner"])
    assert code != 0
    assert "no matching role" in capsys.readouterr().err
    assert fake.posts() == []


# ---- 11. every per-role setting comes from config/roster.json: a changed roster value changes the call (fakes only, no network)

def _roster_with(tmp_path, monkeypatch, edit):
    """A copy of the roster with `edit` applied, read by the adapter in place of the repo's file. The per-profile cache is emptied so the copy
    is read; monkeypatch undoes both."""
    data = json.loads(oc.ROSTER_PATH.read_text(encoding="utf-8"))
    edit(data)
    path = tmp_path / "roster_copy.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(oc, "ROSTER_PATH", path)
    monkeypatch.setattr(oc, "_ROSTER_STATE", {})


def test_a_changed_roster_value_changes_the_request_settings(tmp_path, monkeypatch):
    def edit(data):
        data["profiles"]["production"]["roles"]["writer_a"]["settings"].update(
            temperature=0.33, reasoning={"max_tokens": 1234}, max_tokens=4321, timeout_s=77)
    _roster_with(tmp_path, monkeypatch, edit)
    fake = Fake()
    fake.on("/chat/completions", ok(WRITER_OK))
    built, _ = client(tmp_path, fake)
    built.call("writer_a", "system", "user", kind="text")
    post = fake.posts()[0]
    assert post["body"]["max_tokens"] == 4321
    assert post["body"]["temperature"] == 0.33
    assert post["body"]["reasoning"] == {"max_tokens": 1234}
    assert post["timeout"] == 77


def test_a_changed_reviewer_cap_and_timeout_change_the_truncation_retry(tmp_path, monkeypatch):
    def edit(data):
        data["profiles"]["production"]["roles"]["reviewer_b"]["settings"].update(max_tokens=1000, max_tokens_cap=1500, timeout_s=300)
    _roster_with(tmp_path, monkeypatch, edit)
    fake = Fake()
    fake.on("/chat/completions", ok(REVIEW_OK[:10], finish="length"), ok(REVIEW_OK))
    built, _ = client(tmp_path, fake)
    built.call("reviewer_b", "system", "user", kind="reviewer")
    assert [p["body"]["max_tokens"] for p in fake.posts()] == [1000, 1500]      # the retry doubles 1000 but stops at the roster cap 1500
    assert [p["timeout"] for p in fake.posts()] == [300, 300]


def test_the_reviewer_429_window_ends_at_the_roster_timeout(tmp_path, monkeypatch):
    _roster_with(tmp_path, monkeypatch, lambda data: data["profiles"]["production"]["roles"]["reviewer_b"]["settings"].update(timeout_s=300))
    fake = Fake()
    fake.on("/chat/completions", upstream_429({"retry-after": "280"}), upstream_429(), ok(REVIEW_OK))
    built, clock = client(tmp_path, fake)
    with pytest.raises(oc.ProviderUnavailable, match="review timeout"):      # 280 s wait, then the next 30 s wait would pass 300 s
        built.call("reviewer_b", "system", "user", kind="reviewer")
    assert len(fake.posts()) == 2 and clock.slept == [280.0]


def test_the_probe_token_cap_comes_from_the_roster(tmp_path, monkeypatch):
    _roster_with(tmp_path, monkeypatch, lambda data: data["profiles"]["production"].update(probe_max_tokens=99))
    fake = Fake()
    fake.on("/key", key_reply(), key_reply(), key_reply(), key_reply())
    fake.on("/chat/completions", ok("OK"))
    built, _ = client(tmp_path, fake)
    oc.probe(built, None, names=["writer_a"])
    assert fake.posts()[0]["body"]["max_tokens"] == 99


def test_a_role_without_max_tokens_cap_is_refused_when_the_roster_loads(tmp_path):
    data = json.loads(oc.ROSTER_PATH.read_text(encoding="utf-8"))
    del data["profiles"]["production"]["roles"]["reviewer_b"]["settings"]["max_tokens_cap"]
    path = tmp_path / "roster_copy.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(oc.RosterRefused, match="settings must hold"):
        oc.load_profile("test", path=path)


# ---- 9. every request is kept (2026-11-03, Track G2): usage, finish_reason, seconds, failed or not

def test_every_request_is_kept_with_its_usage_finish_reason_and_seconds(tmp_path):
    fake, clock = Fake(), Clock()

    def slow(method, url, headers, body, timeout):
        clock.now += 2.5
        return fake(method, url, headers, body, timeout)

    usage = {"prompt_tokens": 12, "completion_tokens": 7, "completion_tokens_details": {"reasoning_tokens": 3}}
    fake.on("/chat/completions", ok("hi", usage=usage))
    built = oc.OpenRouter(KEYS, tmp_path / "ledger", transport=slow, clock=clock.time, sleep=clock.sleep, rand=lambda: 0.0)
    built.call("writer_a", "s", "u")
    assert built.requests == [{"model": DOTS, "status": 200, "failed": False, "seconds": 2.5, "finish_reason": "stop",
                               "usage": {"prompt_tokens": 12, "completion_tokens": 7}, "reasoning_tokens": 3, "reasoning_over_cap": False}]


def test_a_failed_request_is_kept_as_a_request_too(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", fail(503, "upstream down"), ok("hi"))
    built, _ = client(tmp_path, fake)
    built.call("writer_a", "s", "u")
    assert [r["status"] for r in built.requests] == [503, 200]
    assert [r["failed"] for r in built.requests] == [True, False]


def test_a_reply_with_no_message_text_keeps_its_finish_reason_usage_keys_and_reasoning_lengths_but_no_text(tmp_path):
    fake = Fake()

    def no_text(request):
        payload = {"model": request["model"], "provider": "Z.AI", "usage": {"prompt_tokens": 9, "completion_tokens": 4, "completion_tokens_details": {"reasoning_tokens": 4}},
                   "choices": [{"index": 0, "finish_reason": "stop",
                                "message": {"role": "assistant", "reasoning": "thinking!", "reasoning_content": "more", FREE_KEY: "x"}}]}
        return 200, {}, json.dumps(payload).encode()

    fake.on("/chat/completions", no_text)
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.ReplyRejected):
        built.call("writer_a", "s", "u")
    facts = built.failed_attempts[0]["facts"]
    assert facts["finish_reason"] == "stop" and facts["usage"] == {"prompt_tokens": 9, "completion_tokens": 4}
    assert sorted(facts["message_keys"]) == ["[REDACTED]", "reasoning", "reasoning_content", "role"]
    assert facts["reasoning_len"] == len("thinking!") and facts["reasoning_content_len"] == len("more")
    assert "thinking!" not in json.dumps(built.failed_attempts[0]) and FREE_KEY not in json.dumps(built.failed_attempts[0])


# ---- R1: reasoning per mode, overrun flag, roster check

def test_a_free_call_sends_the_effort_it_is_given_and_off_sends_no_reasoning_field(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok(WRITER_OK), ok(WRITER_OK), ok(WRITER_OK))
    built, _ = client(tmp_path, fake)
    built.call("writer_a", "system", "user", kind="writer", effort="low")
    built.call("writer_a", "system", "user", kind="writer", effort="off")
    built.call("writer_a", "system", "user", kind="writer")
    low, off, role_default = (p["body"] for p in fake.posts())
    assert low["reasoning"] == {"effort": "low"}
    assert off["reasoning"] == {"enabled": False}                     # the exact off body: an explicit switch, not a missing field
    assert role_default["reasoning"] == {"max_tokens": 8000}          # no effort given: the role's own setting


def test_a_reviewer_on_a_free_model_takes_the_mode_effort_too(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok("Notes.\n## Verdict: READY"))
    built, _ = client(tmp_path, fake)
    built.call("reviewer_b", "system", "user", kind="reviewer", effort="low")
    assert fake.posts()[0]["body"]["reasoning"] == {"effort": "low"}


def test_the_mode_efforts_come_from_the_roster_and_a_missing_table_is_refused(tmp_path):
    assert oc.effort_for_mode("write") == "high" and oc.effort_for_mode("repair") == "low" and oc.effort_for_mode("text") is None
    roster = json.loads(Path(oc.ROSTER_PATH).read_text(encoding="utf-8"))
    del roster["profiles"]["test"]["effort_by_mode"]
    bad = tmp_path / "roster.json"
    bad.write_text(json.dumps(roster), encoding="utf-8")
    with pytest.raises(oc.RosterRefused, match="effort_by_mode"):
        oc.load_roster_file(bad)


def test_a_reply_that_spends_more_reasoning_than_its_cap_is_flagged(tmp_path):
    over = {"model": DOTS, "choices": [{"message": {"content": "x"}, "finish_reason": "stop"}],
            "usage": {"completion_tokens_details": {"reasoning_tokens": 9000}}}
    under = dict(over, usage={"completion_tokens_details": {"reasoning_tokens": 100}})
    assert oc.reply_facts(over)["reasoning_over_cap"] is True
    assert oc.reply_facts(under)["reasoning_over_cap"] is False
