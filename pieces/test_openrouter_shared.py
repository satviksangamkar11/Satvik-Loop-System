"""close-1 step 3: the OpenRouter client on the account ledger (shared_limits) with a fake transport. No network, no model call.

Covers: a free request is counted in the shared ledger, the daily cap refuses before anything is sent, and a minute-window wait that
would pass the unit deadline is refused before the POST (the wait counts against the deadline)."""
import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("SATVIK_PROFILE", "production")   # the paid reviewer_a role is in the production roster only
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
import openrouter_call as oc  # noqa: E402
import shared_limits as sl  # noqa: E402

KEYS = {"free": "sk-or-v1-FREETEST000000000000", "paid": "sk-or-v1-PAIDTEST000000000000"}
DOTS = "dots-studio/dots-3-note-preview:free"
REVIEW_OK = "Looks fine.\n## Verdict: READY"


class Clock:
    def __init__(self, start=1_800_000_000.0):
        self.now = float(start)

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Fake:
    def __init__(self, replies):
        self.replies, self.posts = list(replies), 0

    def __call__(self, method, url, headers, body, timeout):
        if method == "POST":
            self.posts += 1
        if not self.replies:
            raise AssertionError(f"no fake reply left for {method} {url}")
        return self.replies.pop(0)


def chat(text, provider="Z.AI"):
    payload = {"id": "gen-1", "model": DOTS, "provider": provider,
               "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
               "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    return 200, {}, json.dumps(payload).encode()


@pytest.fixture
def home(tmp_path, monkeypatch):
    folder = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setenv("SATVIK_HOME", str(folder))
    return folder


def shared_client(tmp_path, fake, clock):
    built = oc.OpenRouter(KEYS, tmp_path / "ledger", transport=fake, clock=clock.time, sleep=clock.sleep, rand=lambda: 0.0)
    built.set_job("step3-job-a", 1)
    return built


def test_free_request_is_counted_in_the_shared_ledger(home, tmp_path):
    fake = Fake([chat("FILE: pieces/calc.py\n```python\nx = 1\n```\n")])
    text, info = shared_client(tmp_path, fake, Clock()).call("writer_a", "system", "user", kind="writer")
    assert text.startswith("FILE:")
    assert info["model"] == DOTS
    assert fake.posts == 1
    assert sl.usage()["free_today"] == 1


def test_daily_cap_refuses_before_anything_is_sent(home, tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "FREE_DAILY_CAP", 0)
    fake = Fake([])
    with pytest.raises(oc.QuotaExhausted):
        shared_client(tmp_path, fake, Clock()).call("writer_a", "system", "user", kind="writer")
    assert fake.posts == 0


def test_paid_send_whose_pace_wait_passes_the_deadline_is_refused_before_the_post(home, tmp_path, monkeypatch, profile):
    profile("production")                                 # the paid reviewer is GLM only in the production roster
    monkeypatch.setattr(sl, "PER_MINUTE", 1)
    sl.reserve_free("step3-filler", 1)                    # one request sent this minute: the account pace is full
    endpoints = {"data": {"endpoints": [{"tag": "z-ai/fp8", "provider_name": "Z.AI",
                                         "pricing": {"prompt": "0.00000015", "completion": "0.0000005"}}]}}
    fake = Fake([(200, {}, json.dumps(endpoints).encode())])
    client = shared_client(tmp_path, fake, Clock())
    client.set_job_deadline(5)
    with pytest.raises(oc.ProviderUnavailable, match="pace"):
        client.call("reviewer_a", "system", "user", kind="reviewer")
    assert fake.posts == 0
