"""OpenRouter adapter (P1a, architecture Revision 2). The only path to OpenRouter.

Roster roles or exact free IDs only: no `openrouter/free`, no fallback lists. Free IDs use the free key and one account-wide ledger; every
free attempt is reserved there under the lock p1_claims uses (key_ledger.lock), and a failed attempt keeps its reservation. The repair role
(z-ai/glm-5.3-flash) is the only paid ID: it uses the paid key, is priced from the endpoint it is pinned to, and needs a per-job budget. Every reply is checked before it counts. Errors are
named exceptions; nothing falls back to another model or key. Private code is refused unless a route is approved in APPROVED_PRIVATE, which
is empty: no approved non-training route exists yet.

Importing this module calls no model. `main()` with `probe` makes one call per roster model, by hand."""
import http.client
import importlib.util
import json
import math
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
API = "https://openrouter.ai/api/v1"
# Owner roster, 2026-11-02. Roles are the only names production code uses; exact IDs are accepted only where listed in ALLOWED.
ROLES = {
    "writer_a": "dots-studio/dots-3-note-preview:free",
    "writer_b": "xingchen-agi/xing4.0-29b-a4b:free",
    "reviewer": "cohere/north-mini-code:free",
    "repair": "z-ai/glm-5.3-flash",
}
REVIEWER_BACKUP = ("google/gemma-4-31b-it:free", "google/gemma-4-26b-a4b-it:free")
REVIEWER_BACKUP_ENABLED = False     # owner: disabled; the probe may still call these IDs with allow_disabled_backup=True
PAID = frozenset({ROLES["repair"]})
ALLOWED = frozenset(v for v in ROLES.values() if v not in PAID) | frozenset(REVIEWER_BACKUP)
APPROVED_PRIVATE = frozenset()      # owner decision 2026-11-02: no approved non-training route yet, so private jobs are blocked
FREE_DAILY_CAP = 1000               # per account, once $10 has been bought (50 below that)
FREE_PER_MINUTE = 15                # OpenRouter allows 20
JOB_FREE_REQUESTS = 40
JOB_PAID_USD = 0.15                # owner: $0.15 ceiling per job, paid calls only
MAX_RETRIES = 3                     # HTTP retries after the first attempt
BACKOFF_S = (2, 8, 30)              # plus up to 1 s of jitter
MINUTE_WAIT_DEFAULT_S = 5.0
MAX_WAIT_S = 120.0                  # a longer wait than this ends the call instead of sleeping
BREAKER_LIMIT = 2                   # consecutive provider errors drop a model for the job
TRUNCATION_RETRIES = 1              # one retry with a doubled max_tokens
MAX_TOKENS_CAP = 72000              # the largest max_tokens any retry asks for
LIMITS = {                          # owner limits, 2026-11-02; text (the probe) keeps the old defaults
    "writer": {"max_tokens": 40000, "retry_max_tokens": 72000, "timeout_s": 1200, "retry_timeout_s": 1800},
    "repair": {"max_tokens": 40000, "retry_max_tokens": 72000, "timeout_s": 1200, "retry_timeout_s": 1800},
    "reviewer": {"max_tokens": 16000, "timeout_s": 600},
    "text": {"max_tokens": 4000, "timeout_s": 600},
}
REASONING_MAX = 8000                # reasoning.max_tokens on every call; the provider does not enforce it, so thinking tokens are recorded only
XING_SAMPLING = {"temperature": 0.8, "top_p": 0.95, "repetition_penalty": 1.05}
PAID_PROVIDER_NAME, PAID_PROVIDER_SLUG = "Z.AI", "z-ai"     # docs/p1a_glm_endpoint.json, read 2026-11-02
PAID_ROUNDS, PAID_ATTEMPTS = 2, 4   # per job: logical repair rounds, and physical paid attempts (retries included)
KEY_FILES = {"free": ("OPENROUTER_FREE_KEY", "openrouter_free_key.txt"), "paid": ("OPENROUTER_PAID_KEY", "openrouter_paid_key.txt")}
KEY_FIELDS = ("limit", "limit_remaining", "usage", "is_free_tier", "rate_limit")
FILE_BLOCK = re.compile(r"^FILE:[ \t]*\S+", re.M)
VERDICT_LINE = re.compile(r"^## Verdict: (READY|FIX FIRST)$")
SECRET_SHAPES = re.compile(r"sk-or-[A-Za-z0-9_\-]+|Bearer\s+\S+")


def _load_p1_claims():
    spec = importlib.util.spec_from_file_location("p1_claims_for_openrouter", REPO / "pieces" / "p1_claims.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_claims = _load_p1_claims()
FileLock, LedgerError = _claims.FileLock, _claims.LedgerError


class OpenRouterError(RuntimeError):
    """Base class. Messages never contain a key value."""


class ModelRefused(OpenRouterError):
    """Not on the roster, a fallback list, a missing key, or private code without an approved route. Nothing was sent."""


class ReplyRejected(OpenRouterError):
    """The reply counts as nothing: empty, malformed, cut off, or missing its FILE: block or Verdict line."""


class QuotaExhausted(OpenRouterError):
    """The account's free requests for today are used up (a confirmed daily 429, or the local count). Resume after the UTC reset."""


class BudgetExceeded(OpenRouterError):
    """The next call would pass the job's free-request or paid-USD ceiling. Nothing was sent."""


class ProviderUnavailable(OpenRouterError):
    """Timeouts, upstream busy, 5xx, a model that answered as another model, a dropped model, or retries spent."""


class AccountBlocked(OpenRouterError):
    """401, 402 or 403: the key or the balance cannot make calls. No retry."""


class ModelDenied(OpenRouterError):
    """HTTP 403 on one model: that model refuses this request. The account is not blocked; the model is dropped for the job."""


class _Truncated(ReplyRejected):
    pass


class _Mismatch(OpenRouterError):
    pass


def redact(text, secrets=()):
    text = str(text)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return SECRET_SHAPES.sub("[REDACTED]", text)


def utc_day(now):
    return time.strftime("%Y-%m-%d", time.gmtime(now))


def resolve(name, allow_disabled_backup=False):
    """The roster ID for a role name or an exact free roster ID. Anything else (a list, a comma list, openrouter/free, a bare paid ID, a
    disabled backup role) is refused before a request is built."""
    if isinstance(name, str) and name in ROLES:
        return ROLES[name]
    if isinstance(name, str) and name in ALLOWED:
        if name in REVIEWER_BACKUP and not REVIEWER_BACKUP_ENABLED and not allow_disabled_backup:
            raise ModelRefused(f"{name} is a disabled reviewer backup; it is not called")
        return name
    raise ModelRefused(f"not on the roster: {name!r} (roles or exact free IDs only; no openrouter/free, no fallback lists)")


def parse_response(status, raw):
    """(status, data, message). data is the JSON object or None. An error object inside a body takes that error's code as the status."""
    text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw or "")
    try:
        data = json.loads(text) if text.strip() else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return status, None, text[:300]
    err = data.get("error")
    if isinstance(err, dict):
        code = err.get("code")
        status = code if isinstance(code, int) else (status if status != 200 else 502)
        return status, None, f"{err.get('message') or ''} {json.dumps(err.get('metadata') or {}, sort_keys=True)}"[:600]
    return status, data, text[:300]


def classify_429(message):
    """Which limit a 429 names. Only a message that names the day is the daily limit; anything unclear is treated as a provider error."""
    text = message.lower()
    if "per-day" in text or "per day" in text or "daily" in text:
        return "daily"
    if "per-min" in text or "per minute" in text or "per-minute" in text:
        return "minute"
    return "upstream"


def retry_after(headers):
    try:
        return max(float((headers or {}).get("retry-after")), 0.0)
    except (TypeError, ValueError):
        return None


def check_reply(kind, requested, data):
    """The reply text, or the reason it counts as nothing. kind: text | writer (needs a FILE: block) | reviewer (needs a final Verdict line)."""
    if data.get("model") != requested:
        raise _Mismatch(f"reply came from {data.get('model')!r}, not {requested!r}")
    choices = data.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
    message = choice.get("message") if choice else None
    text = message.get("content") if isinstance(message, dict) else None
    if not isinstance(text, str):
        raise ReplyRejected("malformed reply: no message text")
    finish = choice.get("finish_reason")
    if finish == "length":
        raise _Truncated("reply cut off at max_tokens")
    if finish != "stop":
        raise ReplyRejected(f"finish_reason {finish!r} is not stop")
    if not text.strip():
        raise ReplyRejected("empty reply")
    if kind == "writer" and not FILE_BLOCK.search(text):
        raise ReplyRejected("writer reply has no FILE: block")
    if kind == "reviewer":
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines or not VERDICT_LINE.match(lines[-1]):
            raise ReplyRejected("reviewer reply does not end with a '## Verdict: READY' or '## Verdict: FIX FIRST' line")
    return text


def estimate_cost(system, user, max_tokens, endpoint):
    """Input at about 3 characters a token (deliberately high) plus the full output allowance, at the pinned endpoint's price."""
    return math.ceil(len(system + user) / 3) * endpoint["prompt"] + max_tokens * endpoint["completion"]


def actual_cost(data, endpoint):
    """usage.cost when the provider reports it, else the reported tokens at the endpoint price, else None."""
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return None
    if isinstance(usage.get("cost"), (int, float)):
        return float(usage["cost"])
    if endpoint and isinstance(usage.get("prompt_tokens"), int) and isinstance(usage.get("completion_tokens"), int):
        return usage["prompt_tokens"] * endpoint["prompt"] + usage["completion_tokens"] * endpoint["completion"]
    return None


class FreeLedger:
    """Account-wide free-request ledger, shared by every free key and every job. Reservations are written before a request is sent and are
    never returned, so a failed request still counts. `snapshot` is the provider's remaining count when it was last read, and the local count
    since then is taken off it; a daily 429 sets it to 0 until a new snapshot shows requests again."""

    def __init__(self, directory, clock=time.time, daily_cap=FREE_DAILY_CAP, per_minute=FREE_PER_MINUTE, lock_timeout_s=30.0):
        folder = str(directory)
        os.makedirs(folder, exist_ok=True)
        self.folder = folder
        self.path = os.path.join(folder, "openrouter_free_ledger.json")
        self._lock = FileLock(os.path.join(folder, "key_ledger.lock"))
        self.clock, self.daily_cap, self.per_minute, self.lock_timeout_s = clock, daily_cap, per_minute, lock_timeout_s

    def _acquire(self):
        try:
            self._lock.acquire(self.lock_timeout_s)
        except LedgerError as exc:
            raise OpenRouterError(f"free ledger lock: {exc}") from exc

    def _read(self, now):
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            data = {}
        except (OSError, ValueError) as exc:
            raise OpenRouterError(f"free ledger unreadable, so today's count is unknown; not guessing: {exc}") from exc
        if not isinstance(data, dict) or (data.get("day") == utc_day(now) and not isinstance(data.get("used"), int)):
            raise OpenRouterError("free ledger is malformed; not guessing today's count")
        if data.get("day") != utc_day(now):
            data = {"day": utc_day(now), "used": 0, "snapshot": None, "snapshot_used": 0, "minute": []}
        data["minute"] = [t for t in data.get("minute", []) if isinstance(t, (int, float)) and now - t < 60]
        return data

    def _save(self, data):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, self.path)

    def _remaining(self, data):
        left = self.daily_cap - data["used"]
        if data["snapshot"] is not None:
            left = min(left, data["snapshot"] - (data["used"] - data["snapshot_used"]))
        return left

    def reserve(self):
        """0.0 when one request is reserved; otherwise the seconds to wait for the per-minute window. Raises QuotaExhausted for the day."""
        self._acquire()
        try:
            now = self.clock()
            data = self._read(now)
            if self._remaining(data) <= 0:
                raise QuotaExhausted("free requests for today are used up on this account")
            if len(data["minute"]) >= self.per_minute:
                return max(min(data["minute"]) + 60 - now, 0.05)
            data["used"] += 1
            data["minute"].append(now)
            self._save(data)
            return 0.0
        finally:
            self._lock.release()

    def set_snapshot(self, remaining):
        self._acquire()
        try:
            data = self._read(self.clock())
            data["snapshot"] = max(int(remaining), 0)
            data["snapshot_used"] = data["used"]
            self._save(data)
        finally:
            self._lock.release()


class JobBudget:
    """Per-job ceilings. Every free attempt counts. A paid attempt's estimate is reserved before it is sent and is kept as spent when the
    real cost is unknown."""

    def __init__(self, free_requests=JOB_FREE_REQUESTS, paid_usd=JOB_PAID_USD):
        self.free_requests, self.paid_usd = free_requests, paid_usd
        self.free_used, self.paid_reserved, self.paid_spent = 0, 0.0, 0.0
        self.paid_rounds, self.paid_attempts = 0, 0

    def check_free(self):
        if self.free_used >= self.free_requests:
            raise BudgetExceeded("free requests for this job are used up")

    def reserve_paid(self, estimate):
        if self.paid_usd <= 0 or self.paid_spent + self.paid_reserved + estimate > self.paid_usd:
            raise BudgetExceeded(f"paid estimate ${estimate:.6f} would pass the job ceiling ${self.paid_usd:.2f}")
        self.paid_reserved += estimate

    def settle_paid(self, reserved, spent):
        self.paid_reserved -= reserved
        self.paid_spent += spent

    def start_round(self):
        if self.paid_rounds >= PAID_ROUNDS:
            raise BudgetExceeded(f"repair rounds for this job are used up ({PAID_ROUNDS})")
        self.paid_rounds += 1

    def count_attempt(self):
        if self.paid_attempts >= PAID_ATTEMPTS:
            raise BudgetExceeded(f"paid attempts for this job are used up ({PAID_ATTEMPTS}); the next one is not sent")
        self.paid_attempts += 1


class Breaker:
    """Per job: BREAKER_LIMIT provider errors in a row drop a model. A good answer resets its count."""

    def __init__(self):
        self.streak = {}

    def open(self, model):
        return self.streak.get(model, 0) >= BREAKER_LIMIT

    def failed(self, model):
        self.streak[model] = self.streak.get(model, 0) + 1

    def ok(self, model):
        self.streak[model] = 0

    def drop(self, model):
        self.streak[model] = BREAKER_LIMIT


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def default_transport(method, url, headers, body, timeout):
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()


def load_keys(secrets_dir=None, environ=None):
    """{"free": ..., "paid": ...} from an environment variable, else a file in secrets/. Values are never printed or logged."""
    env = os.environ if environ is None else environ
    folder = Path(secrets_dir) if secrets_dir else REPO / "secrets"
    keys = {}
    for slot, (var, filename) in KEY_FILES.items():
        value = (env.get(var) or "").strip()
        path = folder / filename
        if not value and path.is_file():
            value = path.read_text(encoding="utf-8").strip()
        if value:
            keys[slot] = value
    return keys


class OpenRouter:
    """One job's client. `call` returns (text, info) and raises an OpenRouterError subclass for anything that does not count."""

    def __init__(self, keys, ledger_dir, budget=None, transport=None, clock=time.time, sleep=time.sleep, rand=random.random,
                 approved_private=APPROVED_PRIVATE, daily_cap=FREE_DAILY_CAP, per_minute=FREE_PER_MINUTE):
        self.keys = {slot: value for slot, value in dict(keys).items() if value}
        self._secrets = list(self.keys.values())
        self.ledger = FreeLedger(ledger_dir, clock=clock, daily_cap=daily_cap, per_minute=per_minute)
        self.budget = budget or JobBudget()
        self.breaker = Breaker()
        self.transport = transport or default_transport
        self.clock, self.sleep, self.rand = clock, sleep, rand
        self.approved_private = frozenset(approved_private)
        self._endpoints = {}
        self.failed_attempts = []           # every failed attempt: model, HTTP status (None for a network error), first 500 chars of its message

    def _err(self, cls, message):
        return cls(redact(message, self._secrets))

    def _note(self, model, status, message):
        """Keeps one failed attempt. The message is redacted and cut to its first 500 characters."""
        self.failed_attempts.append({"model": model, "status": status, "message": redact(str(message), self._secrets)[:500]})

    def _last_failures(self, model):
        """The last BREAKER_LIMIT raw failures of a model, for the error that drops it."""
        mine = [f for f in self.failed_attempts if f["model"] == model][-BREAKER_LIMIT:]
        return " | ".join(f"HTTP {f['status']}: {f['message']}" if f["status"] is not None else f"no HTTP status: {f['message']}" for f in mine)

    def failures_for(self, model):
        return [f for f in self.failed_attempts if f["model"] == model]


    def endpoint(self, model):
        """The paid model's Z.AI endpoint, read live once per job: per-token prices and the slug the requests are pinned to.
        A reply from any other provider is refused in call()."""
        if model in self._endpoints:
            return self._endpoints[model]
        status, _, raw = self.transport("GET", f"{API}/models/{model}/endpoints", {}, None, 60)
        _, data, message = parse_response(status, raw)
        body = data.get("data") if isinstance(data, dict) else None
        endpoints = body.get("endpoints", []) if isinstance(body, dict) else []
        if status != 200 or not isinstance(endpoints, list):
            raise self._err(ProviderUnavailable, f"endpoint prices for {model} unavailable: HTTP {status} {message[:200]}")
        pinned = None
        for ep in endpoints:
            if not isinstance(ep, dict) or ep.get("provider_name") != PAID_PROVIDER_NAME:
                continue
            if str(ep.get("tag") or "").split("/")[0] != PAID_PROVIDER_SLUG:
                continue
            pricing = ep.get("pricing") if isinstance(ep.get("pricing"), dict) else {}
            try:
                pinned = {"slug": PAID_PROVIDER_SLUG, "name": PAID_PROVIDER_NAME,
                          "prompt": float(pricing["prompt"]), "completion": float(pricing["completion"])}
            except (KeyError, TypeError, ValueError):
                pinned = None
            break
        if pinned is None:
            raise self._err(ProviderUnavailable, f"no priced {PAID_PROVIDER_NAME} endpoint for {model}; repair is refused (no other provider is used)")
        self._endpoints[model] = pinned
        return pinned

    def _key_payload(self, slot):
        """The full GET /api/v1/key JSON for a loaded slot (no completion call)."""
        status, _, raw = self.transport("GET", f"{API}/key", {"Authorization": "Bearer " + self.keys[slot]}, None, 30)
        _, data, message = parse_response(status, raw)
        if status != 200 or not isinstance(data, dict) or not isinstance(data.get("data"), dict):
            raise self._err(OpenRouterError, f"key state unavailable: HTTP {status} {message[:200]}")
        return data

    def key_info(self, slot):
        return self._key_payload(slot)["data"]

    def begin_job(self):
        """Before each job: saves every loaded key's full JSON under key_snapshots/ and sets the free ledger from it. The provider's
        free_model_daily_requests (limit, remaining) is used when present; when absent, the local cap is 1000 for a paid-tier key
        (is_free_tier false) and 50 otherwise. Returns a summary with no key values."""
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(self.clock()))
        folder = os.path.join(self.ledger.folder, "key_snapshots")
        os.makedirs(folder, exist_ok=True)
        summary = {}
        for slot in self.keys:
            payload = self._key_payload(slot)
            with open(os.path.join(folder, f"key_snapshot_{slot}_{stamp}.json"), "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, sort_keys=True)
            if slot != "free":
                summary[slot] = {"saved": True}
                continue
            info = payload["data"]
            quota = info.get("free_model_daily_requests")
            if isinstance(quota, dict) and isinstance(quota.get("limit"), int) and isinstance(quota.get("remaining"), int):
                self.ledger.daily_cap = quota["limit"]
                self.ledger.set_snapshot(quota["remaining"])
                summary[slot] = {"saved": True, "quota": "provider", "note": "free_model_daily_requests read"}
            else:
                self.ledger.daily_cap = 1000 if info.get("is_free_tier") is False else 50
                summary[slot] = {"saved": True, "quota": "local",
                                 "note": "free_model_daily_requests absent; local cap " + str(self.ledger.daily_cap)}
        return summary

    def call(self, name, system, user, kind="text", private=False, max_tokens=None, timeout_s=None, allow_disabled_backup=False):
        """One checked answer from a roster model: (text, info). kind: text, writer or reviewer (see check_reply)."""
        if kind not in ("text", "writer", "reviewer"):
            raise ValueError(f"unknown reply kind {kind!r}")
        model = resolve(name, allow_disabled_backup)
        if private and model not in self.approved_private:
            raise self._err(ModelRefused, f"private code is blocked: no approved non-training route for {model}")
        slot = "paid" if model in PAID else "free"
        if slot not in self.keys:
            raise self._err(ModelRefused, f"no {slot} key loaded, so {model} is not called")
        limits = LIMITS["repair" if slot == "paid" else kind]
        if slot == "paid":
            self.budget.start_round()
        started = self.clock()
        tokens = max_tokens if max_tokens is not None else limits["max_tokens"]
        timeout = timeout_s if timeout_s is not None else limits["timeout_s"]
        attempts, retries, truncations = 0, 0, 0
        while True:
            if attempts and "retry_timeout_s" in limits:
                timeout = limits["retry_timeout_s"]
            if self.breaker.open(model):
                raise self._err(ProviderUnavailable,
                                f"{model} dropped for this job after {BREAKER_LIMIT} provider errors in a row; last: {self._last_failures(model)}")
            attempts += 1
            reserved = self._reserve(model, slot, system, user, tokens)
            try:
                status, headers, raw = self._post(model, slot, system, user, tokens, private, timeout)
            except (OSError, http.client.HTTPException) as exc:       # refused, reset, timed out (socket.timeout is an OSError)
                self._note(model, None, f"{type(exc).__name__}: {exc}")
                self.breaker.failed(model)
                self._settle(slot, model, reserved, None)
                self._wait_before_retry(model, retries, self._backoff_s(retries, None), f"{type(exc).__name__}: {exc}")
                retries += 1
                continue
            status, data, message = parse_response(status, raw)
            spent = self._settle(slot, model, reserved, data)
            if status == 200:
                if data is None:
                    self._note(model, 200, "malformed reply: not a JSON object")
                    raise self._err(ReplyRejected, f"{model}: malformed reply (not a JSON object)")
                if slot == "paid" and data.get("provider") != PAID_PROVIDER_NAME:
                    self._note(model, 200, f"reply came from provider {data.get('provider')!r}")
                    self.breaker.failed(model)
                    raise self._err(ProviderUnavailable, f"{model}: reply came from provider {data.get('provider')!r}, not {PAID_PROVIDER_NAME!r}")
                try:
                    text = check_reply(kind, model, data)
                except _Truncated as exc:
                    self._note(model, 200, str(exc))
                    if truncations == TRUNCATION_RETRIES:
                        raise self._err(ReplyRejected, f"{model}: reply cut off at max_tokens={tokens} again")
                    truncations += 1
                    tokens = limits.get("retry_max_tokens") or min(tokens * 2, MAX_TOKENS_CAP)
                    continue
                except _Mismatch as exc:
                    self._note(model, 200, str(exc))
                    self.breaker.failed(model)
                    raise self._err(ProviderUnavailable, f"{model}: {exc}")
                except ReplyRejected as exc:
                    self._note(model, 200, str(exc))
                    raise self._err(ReplyRejected, f"{model}: {exc}")
                self.breaker.ok(model)
                return text, self._info(model, data, spent, attempts, tokens, started)
            if status == 429:
                self._note(model, 429, message)
                limit = classify_429(message)
                if limit == "daily":
                    self.ledger.set_snapshot(0)
                    raise self._err(QuotaExhausted, f"{model}: daily free limit reached: {message[:200]}")
                if limit == "minute":
                    self._wait_before_retry(model, retries, retry_after(headers) or MINUTE_WAIT_DEFAULT_S, "rate limited per minute")
                else:
                    self.breaker.failed(model)
                    self._wait_before_retry(model, retries, self._backoff_s(retries, retry_after(headers)), "provider rate limited upstream")
                retries += 1
                continue
            if status == 403:
                self._note(model, 403, message)
                self.breaker.drop(model)
                raise self._err(ModelDenied, f"{model}: HTTP 403: {message[:200]}")
            if status in (401, 402):
                self._note(model, status, message)
                raise self._err(AccountBlocked, f"{model}: HTTP {status}: {message[:200]}")
            if status >= 500:
                self._note(model, status, message)
                self.breaker.failed(model)
                self._wait_before_retry(model, retries, self._backoff_s(retries, None), f"HTTP {status}")
                retries += 1
                continue
            self._note(model, status, message)
            raise self._err(OpenRouterError, f"{model}: HTTP {status}: {message[:200]}")

    def _reserve(self, model, slot, system, user, tokens):
        """Takes this attempt's budget before anything is sent. Free: the job count, then the account ledger (waiting out the minute window).
        Paid: the endpoint estimate."""
        if slot == "paid":
            self.budget.count_attempt()
            estimate = estimate_cost(system, user, tokens, self.endpoint(model))
            self.budget.reserve_paid(estimate)
            return estimate
        self.budget.check_free()
        while True:
            wait = self.ledger.reserve()
            if not wait:
                break
            self.sleep(wait)
        self.budget.free_used += 1
        return 0.0

    def _settle(self, slot, model, reserved, data):
        """What the attempt cost: the reply's usage.cost, else the tokens at the endpoint price, else the whole reservation."""
        cost = actual_cost(data, self._endpoints.get(model))
        if slot == "free":
            return cost or 0.0
        spent = reserved if cost is None else cost
        self.budget.settle_paid(reserved, spent)
        return spent

    def _post(self, model, slot, system, user, tokens, private, timeout_s):
        body = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "max_tokens": tokens, "temperature": 0.1, "reasoning": {"max_tokens": REASONING_MAX}}
        if model == ROLES["writer_b"]:
            body.update(XING_SAMPLING)
        provider = {}
        if private:
            provider["data_collection"] = "deny"
        if slot == "paid":
            provider.update(order=[self._endpoints[model]["slug"]], only=[self._endpoints[model]["slug"]], allow_fallbacks=False)
        if provider:
            body["provider"] = provider
        headers = {"Authorization": "Bearer " + self.keys[slot], "Content-Type": "application/json"}
        status, raw_headers, raw = self.transport("POST", f"{API}/chat/completions", headers, json.dumps(body).encode("utf-8"), timeout_s)
        return status, {k.lower(): v for k, v in dict(raw_headers or {}).items()}, raw

    def _backoff_s(self, retries, floor):
        return max(BACKOFF_S[min(retries, len(BACKOFF_S) - 1)] + self.rand(), floor or 0.0)

    def _wait_before_retry(self, model, retries, seconds, why):
        if retries >= MAX_RETRIES:
            raise self._err(ProviderUnavailable, f"{model}: {why}; gave up after {retries + 1} attempts")
        if seconds > MAX_WAIT_S:
            raise self._err(ProviderUnavailable, f"{model}: {why}; asked to wait {seconds:.0f} s, which is longer than {MAX_WAIT_S:.0f} s")
        self.sleep(seconds)

    def _info(self, model, data, spent, attempts, tokens, started):
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        endpoint = self._endpoints.get(model)
        details = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), dict) else {}
        reasoning = details.get("reasoning_tokens") if isinstance(details.get("reasoning_tokens"), int) else None
        return {"model": model, "provider": data.get("provider"), "endpoint": endpoint["slug"] if endpoint else None,
                "status": 200, "finish": "stop", "usage": {k: v for k, v in usage.items() if isinstance(v, int)},
                "reasoning_tokens": reasoning, "cost_usd": spent, "latency_s": round(self.clock() - started, 3),
                "attempts": attempts, "max_tokens": tokens}


PROBE_SYSTEM = "You are a connectivity check. Answer exactly as instructed."
PROBE_USER = "Reply with the single word OK."


def probe(client, out_path=None, names=None):
    """One live call per roster model (kind text), plus each loaded key's state before and after. `names` limits the run to some of the
    probe names. The summary holds no key value; every failed attempt is kept in full (status and first 500 characters)."""
    def key_states():
        return {slot: {k: v for k, v in client.key_info(slot).items() if k in KEY_FIELDS} for slot in client.keys}

    before = key_states()
    results = {}
    targets = [("writer_a", False), ("writer_b", False), ("reviewer", False), (REVIEWER_BACKUP[0], True), (REVIEWER_BACKUP[1], True)]
    if names is not None:
        targets = [t for t in targets if t[0] in names]
    for name, backup in targets:
        model = resolve(name, backup)
        try:
            text, info = client.call(name, PROBE_SYSTEM, PROBE_USER, kind="text", max_tokens=256, allow_disabled_backup=backup)
            results[name] = dict(info, ok=True, reply=text.strip()[:60], failed_attempts=client.failures_for(model))
        except OpenRouterError as exc:
            results[name] = {"model": model, "ok": False, "error": str(exc), "failed_attempts": client.failures_for(model)}
    summary = {"probed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(client.clock())), "key_before": before,
               "key_after": key_states(), "probes": results}
    if out_path:
        Path(out_path).write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="OpenRouter adapter: one live call per roster model (P1a evidence).")
    ap.add_argument("command", choices=["probe"])
    ap.add_argument("--out", help="also write the JSON summary to this file")
    ap.add_argument("--ledger", default=str(REPO / "_loop" / "openrouter"))
    ap.add_argument("--models", help="comma list of probe names to run (writer_a, writer_b, reviewer, or a backup model ID); default all")
    args = ap.parse_args(argv)
    keys = load_keys()
    if not keys:
        print("no key found: set OPENROUTER_FREE_KEY and/or OPENROUTER_PAID_KEY, or put them in secrets/", file=sys.stderr)
        return 2
    names = [m.strip() for m in args.models.split(",") if m.strip()] if args.models else None
    try:
        summary = probe(OpenRouter(keys, args.ledger), args.out, names)
    except OpenRouterError as exc:
        print(f"probe stopped: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if all(p.get("ok") for p in summary["probes"].values()) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
