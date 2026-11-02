"""Test support for production runs (P1b wiring, 2026-11-02): a scripted OpenRouter transport, so every model call goes through the real
openrouter_call adapter and no network is used. Each POST pops the next reply queued for its roster model. Not a test module (no test_ prefix)."""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
import openrouter_call as oc  # noqa: E402
from test_fix_loop_review import FENCE, FILE_MARK  # noqa: E402

FREE_KEY = "sk-or-v1-FREETEST000000000000"
PAID_KEY = "sk-or-v1-PAIDTEST000000000000"
NL = "\n"
READY = "The code meets SPEC 1." + NL + "## Verdict: READY" + NL
GLM_ENDPOINT = {"data": {"endpoints": [{"provider_name": "Z.AI", "tag": "z-ai/fp8",
                                        "pricing": {"prompt": "0.00000015", "completion": "0.0000005"}}]}}


def file_reply(*files):
    """A writer reply: one FILE block per (path, text), in the format parse_files reads."""
    return "".join(FILE_MARK + rel + NL + FENCE + "python" + NL + text + FENCE + NL for rel, text in files)


class Provider:
    """The fake OpenRouter transport. `script` maps a roster model ID to its queued reply texts, in call order."""

    def __init__(self, script):
        self.script = {model: list(items) for model, items in script.items()}
        self.calls = []                  # (model, request body) for every completion POST, in order

    def __call__(self, method, url, headers, body, timeout):
        if method == "GET" and url.endswith("/key"):
            return 200, {}, json.dumps({"data": {"is_free_tier": False, "limit": 2}}).encode()
        if method == "GET" and url.endswith("/models/" + oc.ROLES["repair"] + "/endpoints"):
            return 200, {}, json.dumps(GLM_ENDPOINT).encode()
        if method == "POST" and url.endswith("/chat/completions"):
            request = json.loads(body)
            model = request["model"]
            self.calls.append((model, request))
            queue = self.script.get(model) or []
            if not queue:
                raise AssertionError(f"no scripted reply left for {model}")
            item = queue.pop(0)
            text = item(request) if callable(item) else item
            payload = {"model": model, "provider": oc.PAID_PROVIDER_NAME if model == oc.ROLES["repair"] else "Fake",
                       "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
                       "usage": {"prompt_tokens": 10, "completion_tokens": 10}}
            return 200, {}, json.dumps(payload).encode()
        raise AssertionError(f"no route for {method} {url}")

    def models(self):
        return [model for model, _ in self.calls]


def client(tmp_path, provider):
    """The job's OpenRouter client: both keys loaded, no real sleeps, ledger under tmp_path."""
    return oc.OpenRouter({"free": FREE_KEY, "paid": PAID_KEY}, tmp_path / "ledger", transport=provider,
                         sleep=lambda seconds: None, rand=lambda: 0.0)
