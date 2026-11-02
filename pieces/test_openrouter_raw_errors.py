"""P1a raw errors (2026-11-02): every failed attempt keeps its HTTP status (None for a network error) and the first 500 characters of its
message, including when the breaker drops a model; the probe keeps them in full and can be limited to named models. Fake HTTP only."""
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
DOTS = "dots-studio/dots-3-note-preview:free"
NORTH = "cohere/north-mini-code:free"


class Clock:
    def __init__(self):
        self.now, self.slept = 1_800_000_000.0, []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class Fake:
    def __init__(self):
        self.queue, self.calls = {}, []

    def on(self, suffix, *items):
        self.queue.setdefault(suffix, []).extend(items)

    def __call__(self, method, url, headers, body, timeout):
        request = json.loads(body) if body else None
        self.calls.append({"method": method, "url": url, "body": request})
        for suffix, items in self.queue.items():
            if url.endswith(suffix):
                if not items:
                    raise AssertionError(f"no fake response left for {url}")
                item = items.pop(0)
                if callable(item):
                    item = item(request)
                if isinstance(item, BaseException):
                    raise item
                return item
        raise AssertionError(f"no route for {url}")

    def posts(self):
        return [c for c in self.calls if c["method"] == "POST"]


def ok(text):
    def build(request):
        payload = {"model": request["model"], "provider": "Fake",
                   "choices": [{"message": {"content": text}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        return 200, {}, json.dumps(payload).encode()
    return build


def fail(status, message, headers=None):
    return status, headers or {}, json.dumps({"error": {"code": status, "message": message, "metadata": {}}}).encode()


def client(tmp_path, fake, clock=None, keys=None):
    clock = clock or Clock()
    keys = keys if keys is not None else {"free": FREE_KEY, "paid": PAID_KEY}
    return oc.OpenRouter(keys, tmp_path / "ledger", transport=fake, clock=clock.time, sleep=clock.sleep, rand=lambda: 0.0), clock


def key_reply():
    return 200, {}, json.dumps({"data": {"is_free_tier": False, "limit": 2}}).encode()


def test_each_failed_attempt_keeps_its_status_and_the_first_500_characters(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", fail(503, "E" * 1000), ok("hi"))
    built, _ = client(tmp_path, fake)
    assert built.call("writer_a", "s", "u")[0] == "hi"
    assert len(built.failed_attempts) == 1
    entry = built.failed_attempts[0]
    assert entry["model"] == DOTS and entry["status"] == 503 and entry["message"] == "E" * 500


def test_a_network_error_is_kept_with_no_status(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", socket.timeout("timed out"), ok("hi"))
    built, _ = client(tmp_path, fake)
    built.call("writer_a", "s", "u")
    entry = built.failed_attempts[0]
    assert entry["status"] is None and "timed out" in entry["message"] and entry["model"] == DOTS


def test_a_rejected_200_reply_is_kept_with_status_200(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok("   "))
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.ReplyRejected):
        built.call("writer_a", "s", "u")
    assert built.failed_attempts[0]["status"] == 200 and "empty" in built.failed_attempts[0]["message"]


def test_a_429_of_any_kind_is_kept_with_its_status(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", fail(429, "Rate limit exceeded: free-models-per-min", {"retry-after": "1"}), ok("hi"))
    built, _ = client(tmp_path, fake)
    built.call("writer_a", "s", "u")
    assert built.failed_attempts[0]["status"] == 429 and "per-min" in built.failed_attempts[0]["message"]


def test_a_dropped_model_error_carries_the_raw_status_and_text(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", fail(429, "Provider returned error"), fail(429, "Provider returned error"), ok("never"))
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.ProviderUnavailable) as info:
        built.call("writer_a", "s", "u")
    message = str(info.value)
    assert "dropped" in message and "HTTP 429: Provider returned error" in message
    assert len(fake.posts()) == 2


def test_probe_keeps_the_failed_attempts_of_a_dropped_model_in_full(tmp_path, profile):
    profile("production")                       # the three probe roles of the production roster (reviewer_a is paid, never probed)
    fake = Fake()
    fake.on("/key", key_reply(), key_reply(), key_reply(), key_reply())
    # writer_a and writer_b each fail twice (the breaker drops them), then reviewer_b answers
    fake.on("/chat/completions", fail(429, "Provider returned error"), fail(429, "Provider returned error"),
            fail(429, "Provider returned error"), fail(429, "Provider returned error"), ok("OK"))
    built, _ = client(tmp_path, fake)
    results = oc.probe(built, str(tmp_path / "probe.json"))["probes"]
    dots = results["writer_a"]
    assert dots["ok"] is False and "HTTP 429" in dots["error"]
    assert [f["status"] for f in dots["failed_attempts"]] == [429, 429]
    assert all(f["message"].startswith("Provider returned error") for f in dots["failed_attempts"])
    assert results["writer_b"]["ok"] is False and len(results["writer_b"]["failed_attempts"]) == 2
    assert results["reviewer_b"]["ok"] is True


def test_probe_can_be_limited_to_named_models(tmp_path):
    fake = Fake()
    fake.on("/key", key_reply(), key_reply(), key_reply(), key_reply())
    fake.on("/chat/completions", ok("OK"), ok("OK"))
    built, _ = client(tmp_path, fake)
    summary = oc.probe(built, None, names=["writer_a", "reviewer_b"])
    assert [p["body"]["model"] for p in fake.posts()] == [DOTS, NORTH]
    assert set(summary["probes"]) == {"writer_a", "reviewer_b"}


def test_cli_models_option_runs_only_the_named_models(tmp_path, monkeypatch):
    fake = Fake()
    fake.on("/key", key_reply(), key_reply(), key_reply(), key_reply())
    fake.on("/chat/completions", ok("OK"))
    monkeypatch.setattr(oc, "default_transport", fake)
    monkeypatch.setattr(oc, "load_keys", lambda: {"free": FREE_KEY, "paid": PAID_KEY})
    code = oc.main(["probe", "--out", str(tmp_path / "probe.json"), "--ledger", str(tmp_path / "ledger"), "--models", "reviewer_b"])
    assert code == 0
    assert [p["body"]["model"] for p in fake.posts()] == [NORTH]
