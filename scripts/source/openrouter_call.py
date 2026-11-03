"""OpenRouter adapter (P1a, architecture Revision 2). The only path to OpenRouter.

Models come only from config/roster.json, read for the profile named by SATVIK_PROFILE (default "test"). Roster roles or exact free roster IDs
only: no auto-routing alias, no fallback lists. Free IDs use the free key; every free attempt is reserved on the shared ledger
(shared_limits, in the loop home) under its job, including solo runs, and a failed attempt keeps its reservation. The per-key folder keeps
only the saved key snapshots: it never changes the count. A paid OpenRouter role (production reviewer_a)
uses the paid key, is pinned to its roster provider, is reviewer-only (any other kind is refused before a request), and its reviews share one
per-job ceiling that is reserved before each call. The test profile refuses any paid role when the roster is loaded, before any request.
Every reply is checked before it counts. Errors are named exceptions; nothing falls back to another model or key. Private code is refused
unless a route is approved in APPROVED_PRIVATE, which is empty: no approved non-training route exists yet.

Importing this module calls no model. `main()` with `probe` makes one call per writer and reviewer role, by hand."""
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
ROSTER_PATH = REPO / "config" / "roster.json"
PROFILE_VAR = "SATVIK_PROFILE"      # "test" (the default) or "production"
WRITER_ROLES = ("writer_a", "writer_b")
REVIEWER_ROLES = ("reviewer_b", "reviewer_a")     # the review panel's order: the free reviewer first, the paid one last (after a READY)
ROLE_NAMES = ("writer_a", "writer_b", "reviewer_a", "reviewer_b", "repair", "planner")
SETTING_KEYS = ("temperature", "reasoning", "max_tokens", "timeout_s", "sampling", "max_tokens_cap")   # every role's numbers live in config/roster.json
NON_AUTHOR_WRITER = "non_author_writer"  # reviewer_a under the test profile: the free writer that did not write the code
SENTINELS = frozenset({NON_AUTHOR_WRITER, "off", "fake"})   # roster values that are not model IDs
APPROVED_PRIVATE = frozenset()      # owner decision 2026-11-02: no approved non-training route yet, so private jobs are blocked
JOB_FREE_REQUESTS = 40
JOB_REVIEW_USD_BY_PROFILE = {"test": 0.0, "production": 0.05}   # owner 2026-11-02: the paid-review ceiling per job is exactly 0 under test, exactly 0.05 in production
JOB_REVIEW_USD = JOB_REVIEW_USD_BY_PROFILE["production"]        # reserved before each paid review call
MAX_RETRIES = 3                     # HTTP retries after the first attempt
BACKOFF_S = (2, 8, 30)              # plus up to 1 s of jitter
MINUTE_WAIT_DEFAULT_S = 5.0
MAX_WAIT_S = 120.0                  # a longer wait than this ends the call instead of sleeping
REVIEWER_429_WAITS_S = (15, 30, 60, 120, 180)   # an upstream 429 on a reviewer call: these waits, plus up to 1 s of jitter (Retry-After when longer)
BREAKER_LIMIT = 2                   # consecutive provider errors drop a model for the job
TRUNCATION_RETRIES = 1              # one retry with a doubled max_tokens
KEY_FILES = {"free": ("OPENROUTER_FREE_KEY", "openrouter_free_key.txt"), "paid": ("OPENROUTER_PAID_KEY", "openrouter_paid_key.txt")}
KEY_FIELDS = ("limit", "limit_remaining", "usage", "is_free_tier", "rate_limit")
FILE_BLOCK = re.compile(r"^FILE:[ \t]*\S+", re.M)
VERDICT_LINE = re.compile(r"^## Verdict: (READY|FIX FIRST)$")
SECRET_SHAPES = re.compile(r"\bsk-[A-Za-z0-9_\-]{6,}|Bearer\s+\S+|(?i:authorization)[\"']?\s*[:=]\s*[^\r\n,}\"']+")
FAILURE_CHARS = 2000                # the first characters of a failed call's message that are kept (after redaction)


def _shared_limits():
    """package E's account ledger (scripts/source/shared_limits.py), imported on first use."""
    source = str(REPO / "scripts" / "source")
    if source not in sys.path:
        sys.path.insert(0, source)
    import shared_limits
    return shared_limits


def _loop_home():
    """scripts/source/loop_home.py, imported on first use (the same path rule as _shared_limits)."""
    source = str(REPO / "scripts" / "source")
    if source not in sys.path:
        sys.path.insert(0, source)
    import loop_home
    return loop_home


def shared_job(name, fallback):
    """A job id and its generation on the loop home (shared_limits), for a client that the launcher did not start (a solo run, a probe, a
    test). The home is SATVIK_HOME, else `fallback`; loop_home refuses a home inside a git worktree."""
    lh = _loop_home()
    home = lh.home(loops=1, fallback=fallback)
    job_id = lh.new_job_id(name, home_dir=home)
    return job_id, lh.Lease(home).acquire(job_id), home


def _count_429(model, home=None):
    """One upstream or per-minute 429 for `model`, as the registry counter "429:<model>" (loop_home, step 8). The loop home is the client's
    job home when it has one (Track L), else SATVIK_HOME, else the repo's _loop, the same fallback p1_claims uses."""
    source = str(REPO / "scripts" / "source")
    if source not in sys.path:
        sys.path.insert(0, source)
    import loop_home
    loop_home.Registry(home if home is not None else loop_home.home(fallback=REPO / "_loop")).bump("429:" + model)


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
    """The next call would pass the job's free-request ceiling. Nothing was sent."""


class ReviewBudgetExceeded(BudgetExceeded):
    """A GLM review's worst case would pass the job's review ceiling. Nothing was sent."""


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


class RosterRefused(ValueError):
    """config/roster.json is malformed, names an unknown profile, or the test profile names a paid role. Raised while the roster loads: no
    client exists and no request is sent."""


def _check_role(profile, role, spec):
    where = f"{profile}/{role}"
    if not isinstance(spec, dict) or any(key not in spec for key in ("model", "provider", "paid", "pricing", "settings")):
        raise RosterRefused(f"{where}: needs model, provider, paid, pricing and settings")
    if not isinstance(spec["model"], str) or not spec["model"].strip():
        raise RosterRefused(f"{where}: model must be a non-empty string")
    if spec["provider"] is not None and (not isinstance(spec["provider"], dict) or not {"name", "slug"} <= set(spec["provider"])):
        raise RosterRefused(f"{where}: provider must be null or {{name, slug}}")
    if not isinstance(spec["paid"], bool):
        raise RosterRefused(f"{where}: paid must be true or false")
    if not isinstance(spec["settings"], dict) or any(key not in spec["settings"] for key in SETTING_KEYS):
        raise RosterRefused(f"{where}: settings must hold {', '.join(SETTING_KEYS)}")
    if spec["paid"] and role in WRITER_ROLES + REVIEWER_ROLES and (spec["provider"] is None or spec["pricing"] is None):
        raise RosterRefused(f"{where}: a paid OpenRouter role needs a pinned provider and prices")


def load_roster_file(path=None):
    """The profiles in config/roster.json, every role checked. Raises RosterRefused for anything malformed."""
    try:
        data = json.loads(Path(path or ROSTER_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RosterRefused(f"config/roster.json cannot be read: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise RosterRefused("config/roster.json: schema_version must be 1")
    profiles = data.get("profiles")
    if not isinstance(profiles, dict) or set(profiles) != {"test", "production"}:
        raise RosterRefused("config/roster.json: profiles must be exactly test and production")
    for name, profile in profiles.items():
        roles = profile.get("roles") if isinstance(profile, dict) else None
        if not isinstance(roles, dict) or set(roles) != set(ROLE_NAMES):
            raise RosterRefused(f"profile {name}: roles must be exactly {', '.join(ROLE_NAMES)}")
        cap = profile.get("probe_max_tokens")
        if not isinstance(cap, int) or isinstance(cap, bool) or cap <= 0:
            raise RosterRefused(f"profile {name}: probe_max_tokens must be a positive whole number")
        for role, spec in roles.items():
            _check_role(name, role, spec)
    return profiles


def probe_max_tokens(profile=None):
    """The probe's output cap for a profile (default: the active one), from config/roster.json."""
    return load_roster_file()[profile or active_profile()]["probe_max_tokens"]


def load_profile(name=None, path=None):
    """{role: spec} for a profile: the name given, else SATVIK_PROFILE, else test. The test profile refuses any paid role here, before any
    request can be built."""
    name = (name or os.environ.get(PROFILE_VAR) or "test").strip()
    profiles = load_roster_file(path)
    if name not in profiles:
        raise RosterRefused(f"unknown profile {name!r}: SATVIK_PROFILE is test (the default) or production")
    roles = profiles[name]["roles"]
    if name == "test":
        paid = sorted(role for role, spec in roles.items() if spec["paid"])
        if paid:
            raise RosterRefused(f"the test profile refuses paid roles; the roster names these as paid: {paid}")
    return roles


_ROSTER_STATE = {}                  # profile name -> the tables derived from its roster; keyed by the name, so a changed SATVIK_PROFILE is read again


def active_profile():
    """The profile named by SATVIK_PROFILE, read now (default test). Never cached: the roster is read per call, not at import."""
    return (os.environ.get(PROFILE_VAR) or "test").strip()


def roster_state(profile=None):
    """The tables for a profile (default: the active one): ROLE_SPECS, ROLES, PAID, ALLOWED, PINS, PRICING, SETTINGS, PROFILE. Loaded inside
    this function and cached by profile name, so each profile's roster is checked once and a test can switch profiles with the environment."""
    name = profile or active_profile()
    if name not in _ROSTER_STATE:
        specs = load_profile(name)
        roles = {role: spec["model"] for role, spec in specs.items()}
        api = [role for role in WRITER_ROLES + REVIEWER_ROLES if roles[role] not in SENTINELS]
        _ROSTER_STATE[name] = {
            "PROFILE": name, "ROLE_SPECS": specs, "ROLES": roles,
            "PAID": frozenset(roles[r] for r in api if specs[r]["paid"]),
            "ALLOWED": frozenset(roles[r] for r in api if not specs[r]["paid"]),
            "PINS": {roles[r]: specs[r]["provider"] for r in api if specs[r]["paid"]},       # paid model -> {name, slug}
            "PRICING": {roles[r]: specs[r]["pricing"] for r in api if specs[r]["paid"]},     # paid model -> USD per token
            "SETTINGS": {roles[r]: specs[r]["settings"] for r in api},                       # model -> its role's settings
        }
    return _ROSTER_STATE[name]


_ROSTER_NAMES = frozenset({"PROFILE", "ROLE_SPECS", "ROLES", "PAID", "ALLOWED", "PINS", "PRICING", "SETTINGS"})


def __getattr__(name):
    """Module attributes for the roster tables, read from the active profile at each access (PEP 562)."""
    if name in _ROSTER_NAMES:
        return roster_state()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def redact(text, secrets=()):
    text = str(text)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return SECRET_SHAPES.sub("[REDACTED]", text)


def utc_day(now):
    return time.strftime("%Y-%m-%d", time.gmtime(now))


def resolve(name):
    """The roster model for a writer or reviewer role name, or an exact free roster ID. Anything else (a list, a comma list, an alias, a
    bare paid ID, the repair or planner role, a sentinel such as non_author_writer) is refused before a request is built."""
    if isinstance(name, str) and name in WRITER_ROLES + REVIEWER_ROLES:
        roles = roster_state()["ROLES"]
        if roles[name] in SENTINELS:
            raise ModelRefused(f"{name} is {roles[name]!r} in the {active_profile()} profile, not a model: nothing is called")
        return roles[name]
    if isinstance(name, str) and name in roster_state()["ALLOWED"]:
        return name
    raise ModelRefused(f"not on the roster: {name!r} (writer or reviewer roles, or exact free roster IDs only; no fallback lists)")


def parse_response(status, raw):
    """(status, data, message). data is the JSON object or None. An error object inside a body takes that error's code as the status."""
    text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw or "")
    try:
        data = json.loads(text) if text.strip() else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return status, None, text[:FAILURE_CHARS]
    err = data.get("error")
    if isinstance(err, dict):
        code = err.get("code")
        status = code if isinstance(code, int) else (status if status != 200 else 502)
        return status, None, f"{err.get('message') or ''} {json.dumps(err.get('metadata') or {}, sort_keys=True)}"[:FAILURE_CHARS]
    return status, data, text[:FAILURE_CHARS]


def _length(value):
    return len(value) if isinstance(value, (str, list, dict)) else None


def reply_facts(data, secrets=()):
    """What a reply says about itself, never its text: finish_reason, the integer usage fields and the reasoning tokens. A reply with no
    message text (choices[0].message.content is not a string) also keeps the keys of that message (redacted) and the lengths of its reasoning
    and reasoning_content fields. {} when there is no reply object."""
    if not isinstance(data, dict):
        return {}
    choices = data.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    details = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), dict) else {}
    facts = {"finish_reason": choice.get("finish_reason"), "usage": {k: v for k, v in usage.items() if isinstance(v, int)},
             "reasoning_tokens": details.get("reasoning_tokens") if isinstance(details.get("reasoning_tokens"), int) else None}
    if not isinstance(message.get("content"), str):
        facts["message_keys"] = sorted(redact(str(k), secrets) for k in message)
        facts["reasoning_len"] = _length(message.get("reasoning"))
        facts["reasoning_content_len"] = _length(message.get("reasoning_content"))
    return facts


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


def review_reservation(system, user, max_tokens, prices):
    """The worst case of one paid call: the prompt at about 3 characters a token (deliberately high) at the roster's prompt price, plus the
    whole output allowance at the roster's completion price. `prices` is {"prompt", "completion"} in USD per token."""
    return math.ceil(len(system + user) / 3) * prices["prompt"] + max_tokens * prices["completion"]


def _reported_cost(data):
    """usage.cost when the provider reports it, else None."""
    usage = data.get("usage") if isinstance(data, dict) else None
    cost = usage.get("cost") if isinstance(usage, dict) else None
    return float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None


def review_cost(data, prices):
    """What a paid reply costs: usage.cost when reported, else its tokens at the roster's prices, else None."""
    if _reported_cost(data) is not None:
        return _reported_cost(data)
    usage = data.get("usage") if isinstance(data, dict) else None
    if isinstance(usage, dict) and isinstance(usage.get("prompt_tokens"), int) and isinstance(usage.get("completion_tokens"), int):
        return usage["prompt_tokens"] * prices["prompt"] + usage["completion_tokens"] * prices["completion"]
    return None


class JobBudget:
    """Per-job ceilings. Every free attempt counts. The GLM reviews share one review ceiling: each call's worst case is reserved before it is
    sent; what its reply reports (or the whole reservation when no reply came) is kept as spent."""

    def __init__(self, free_requests=JOB_FREE_REQUESTS, review_usd=JOB_REVIEW_USD):
        self.free_requests, self.review_usd = free_requests, review_usd
        self.free_used, self.review_reserved, self.review_spent = 0, 0.0, 0.0

    def check_free(self):
        if self.free_used >= self.free_requests:
            raise BudgetExceeded("free requests for this job are used up")

    def check_review(self, estimate):
        if self.review_spent + self.review_reserved + estimate > self.review_usd + 1e-12:
            raise ReviewBudgetExceeded(f"the review worst case ${estimate:.6f} would pass the job's review ceiling ${self.review_usd:.2f}; not sent")

    def reserve_review(self, estimate):
        self.check_review(estimate)
        self.review_reserved += estimate

    def settle_review(self, reserved, spent):
        self.review_reserved -= reserved
        self.review_spent += spent


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
    if ("free" in keys) != ("paid" in keys):          # one key serves both roles (owner 2026-11-02): the paid allowlist and dollar budget still apply
        only = keys.get("free") or keys.get("paid")
        keys = {"free": only, "paid": only}
    return keys


class OpenRouter:
    """One job's client. `call` returns (text, info) and raises an OpenRouterError subclass for anything that does not count."""

    def __init__(self, keys, ledger_dir, budget=None, transport=None, clock=time.time, sleep=time.sleep, rand=random.random,
                 approved_private=APPROVED_PRIVATE):
        self.keys = {slot: value for slot, value in dict(keys).items() if value}
        self._secrets = list(self.keys.values())
        self.ledger_dir = str(ledger_dir)     # holds the key snapshots only: the free count is the shared ledger (shared_limits)
        os.makedirs(self.ledger_dir, exist_ok=True)
        self.budget = budget or JobBudget()
        self.breaker = Breaker()
        self.transport = transport or default_transport
        self.clock, self.sleep, self.rand = clock, sleep, rand
        self.approved_private = frozenset(approved_private)
        self._endpoints = {}
        self.failed_attempts = []           # every failed attempt: model, HTTP status (None for a network error), first 2000 chars of its message, reply_facts
        self.requests = []                  # every request sent, failed or not: model, status, failed, seconds, reply_facts (production writes each as call_NN.json)
        self.retry_log = []                 # every retry: model, the attempt it starts, the wait in seconds, the error (scrubbed); the job's status keeps it
        self.deadline_at = None             # the unit deadline on this client's clock; no retry may start after it (set_job_deadline)
        self.shared = None                  # (job_id, generation) once set_job puts this client on the account ledger (package E)
        self.shared_home = None             # the loop home that job lives in (None: SATVIK_HOME)

    def set_job_deadline(self, seconds):
        """The unit deadline, `seconds` from now. p1b_production sets it just before the unit's walk starts."""
        self.deadline_at = self.clock() + seconds

    def set_job(self, job_id, gen, home=None):
        """Puts every request on the account ledger (shared_limits, package E) under this job and generation: reserve, pace and the model
        slot are taken there. Without it the client keeps its own ledger (the probe and the older tests use that)."""
        self.shared = (job_id, gen)
        self.shared_home = home

    def _deadline_left(self):
        return None if self.deadline_at is None else self.deadline_at - self.clock()

    def _err(self, cls, message):
        return cls(redact(message, self._secrets))

    def _log_retry(self, model, attempt, seconds, text):
        self.retry_log.append({"model": model, "attempt": attempt, "wait_s": round(float(seconds), 3),
                               "error": redact(str(text), self._secrets)[:300]})

    def _check_retry_fits(self, model, message):
        """Before a reviewer's 429 retry: the job's free-request count and the account's daily count must leave room for one more request."""
        if self.budget.free_used >= self.budget.free_requests:
            raise self._err(ProviderUnavailable, f"{model}: the job's free requests are used up; the retry is not sent; last 429: {message[:300]}")
        if self.shared is not None and _shared_limits().usage(self.shared_home)["free_today"] >= _shared_limits().FREE_DAILY_CAP:
            raise self._err(QuotaExhausted, f"{model}: free requests for today are used up; the retry is not sent; last 429: {message[:300]}")

    def _reviewer_window_wait(self, model, started, used, floor, message, attempt):
        """One upstream 429 on a reviewer call: wait the next step of REVIEWER_429_WAITS_S (plus jitter, or Retry-After when longer), then retry.
        The window is used up after its last step, or when the next wait would pass the model's roster timeout_s from the call's start; the model
        is then dropped for the job. A stop for the unit deadline, or for the job's free-request or daily count, raises without dropping the model."""
        review_timeout = roster_state()["SETTINGS"][model]["timeout_s"]
        if used >= len(REVIEWER_429_WAITS_S):
            self.breaker.drop(model)
            raise self._err(ProviderUnavailable, f"{model}: still rate-limited upstream (429) after {used} retries; the window is used up; last 429: {message[:300]}")
        seconds = max(REVIEWER_429_WAITS_S[used] + self.rand(), floor or 0.0)
        if self.clock() - started + seconds > review_timeout:
            self.breaker.drop(model)
            raise self._err(ProviderUnavailable, f"{model}: the next wait ({seconds:.0f} s) would pass the {review_timeout} s review timeout; the window is used up; last 429: {message[:300]}")
        if self.deadline_at is not None and self.clock() + seconds > self.deadline_at:
            raise self._err(ProviderUnavailable, f"{model}: the next retry would start after the unit deadline; last 429: {message[:300]}")
        self._check_retry_fits(model, message)
        self._log_retry(model, attempt, seconds, message)
        self.sleep(seconds)

    def _keep_request(self, model, status, seconds, data=None):
        """Keeps one request the client sent, failed or not: its raw HTTP status (None for a network error), its seconds, and reply_facts."""
        self.requests.append({"model": model, "status": status, "failed": False, "seconds": round(float(seconds), 3), **reply_facts(data, self._secrets)})

    def _note(self, model, status, message, data=None, request=True):
        """Keeps one failed attempt: its raw HTTP status (None for a network error), its message (redacted, cut to FAILURE_CHARS) and reply_facts.
        A chat attempt comes right after its request (_keep_request), which it marks failed; the endpoint price read is no chat request (request=False)."""
        self.failed_attempts.append({"model": model, "status": status, "message": redact(str(message), self._secrets)[:FAILURE_CHARS],
                                     "facts": reply_facts(data, self._secrets)})
        if request:
            self.requests[-1]["failed"] = True

    def _last_failures(self, model):
        """The last BREAKER_LIMIT raw failures of a model, for the error that drops it."""
        mine = [f for f in self.failed_attempts if f["model"] == model][-BREAKER_LIMIT:]
        return " | ".join(f"HTTP {f['status']}: {f['message'][:300]}" if f["status"] is not None else f"no HTTP status: {f['message'][:300]}" for f in mine)

    def failures_for(self, model):
        return [f for f in self.failed_attempts if f["model"] == model]


    def endpoint(self, model):
        """The paid model's Z.AI endpoint, read live once per job: per-token prices and the slug the requests are pinned to.
        A reply from any other provider is refused in call()."""
        if model in self._endpoints:
            return self._endpoints[model]
        pin = roster_state()["PINS"].get(model)
        if pin is None:
            raise self._err(ModelRefused, f"{model} has no pinned provider in the roster; nothing is sent")
        status, _, raw = self.transport("GET", f"{API}/models/{model}/endpoints", {}, None, 60)
        _, data, message = parse_response(status, raw)
        body = data.get("data") if isinstance(data, dict) else None
        endpoints = body.get("endpoints", []) if isinstance(body, dict) else []
        if status != 200 or not isinstance(endpoints, list):
            self._note(model, status, message, request=False)
            raise self._err(ProviderUnavailable, f"endpoint prices for {model} unavailable: HTTP {status} {message[:200]}")
        pinned = None
        for ep in endpoints:
            if not isinstance(ep, dict) or ep.get("provider_name") != pin["name"]:
                continue
            if str(ep.get("tag") or "").split("/")[0] != pin["slug"]:
                continue
            pricing = ep.get("pricing") if isinstance(ep.get("pricing"), dict) else {}
            try:
                pinned = {"slug": pin["slug"], "name": pin["name"],
                          "prompt": float(pricing["prompt"]), "completion": float(pricing["completion"])}
            except (KeyError, TypeError, ValueError):
                pinned = None
            break
        if pinned is None:
            raise self._err(ProviderUnavailable, f"no priced {pin['name']} endpoint for {model}; the review is refused (no other provider is used)")
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
        folder = os.path.join(self.ledger_dir, "key_snapshots")
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
                summary[slot] = {"saved": True, "quota": "provider", "note": "free_model_daily_requests read (evidence only; not counted)"}
            else:
                summary[slot] = {"saved": True, "quota": "local", "note": "free_model_daily_requests absent (evidence only; not counted)"}
        return summary

    def call(self, name, system, user, kind="text", private=False, max_tokens=None, timeout_s=None):
        """One checked answer from a roster model: (text, info). kind: text, writer or reviewer (see check_reply). The model's settings
        come from its roster role; the retry values apply to writer calls only."""
        if kind not in ("text", "writer", "reviewer"):
            raise ValueError(f"unknown reply kind {kind!r}")
        model = resolve(name)
        if private and model not in self.approved_private:
            raise self._err(ModelRefused, f"private code is blocked: no approved non-training route for {model}")
        slot = "paid" if model in roster_state()["PAID"] else "free"
        if slot == "paid" and kind != "reviewer":
            raise self._err(ModelRefused, f"{model} is the GLM reviewer: it takes reviewer calls only, so this {kind} call is refused before any request")
        if slot not in self.keys:
            raise self._err(ModelRefused, f"no {slot} key loaded, so {model} is not called")
        settings = roster_state()["SETTINGS"][model]
        retry = settings if kind == "writer" else {}           # retry values belong to writer calls only
        started = self.clock()
        tokens = max_tokens if max_tokens is not None else settings["max_tokens"]
        timeout = timeout_s if timeout_s is not None else settings["timeout_s"]
        attempts, retries, truncations, window = 0, 0, 0, 0     # window: upstream 429 retries a reviewer call has used (REVIEWER_429_WAITS_S)
        while True:
            if attempts and retry.get("retry_timeout_s"):
                timeout = retry["retry_timeout_s"]
            if self.breaker.open(model):
                raise self._err(ProviderUnavailable,
                                f"{model} dropped for this job after {BREAKER_LIMIT} provider errors in a row; last: {self._last_failures(model)}")
            attempts += 1
            reserved = self._reserve(model, slot, system, user, tokens)
            sent = self.clock()
            try:
                status, headers, raw = self._send(model, slot, system, user, tokens, private, timeout)
            except (OSError, http.client.HTTPException) as exc:       # refused, reset, timed out (socket.timeout is an OSError)
                self._keep_request(model, None, self.clock() - sent)
                self._note(model, None, f"{type(exc).__name__}: {exc}")
                self.breaker.failed(model)
                self._settle(slot, model, reserved, None)
                self._wait_before_retry(model, retries, self._backoff_s(retries, None), f"{type(exc).__name__}: {exc}", attempts + 1)
                retries += 1
                continue
            http_status = status                                  # the raw HTTP status; parse_response may replace it with a body error code
            status, data, message = parse_response(status, raw)
            self._keep_request(model, http_status, self.clock() - sent, data)
            spent = self._settle(slot, model, reserved, data)
            if status == 200:
                if data is None:
                    self._note(model, 200, f"malformed reply: not a JSON object: {message}")
                    raise self._err(ReplyRejected, f"{model}: malformed reply (not a JSON object)")
                pin_name = roster_state()["PINS"][model]["name"] if slot == "paid" else None
                if slot == "paid" and data.get("provider") != pin_name:
                    self._note(model, 200, f"reply came from provider {data.get('provider')!r}", data)
                    self.breaker.failed(model)
                    raise self._err(ProviderUnavailable, f"{model}: reply came from provider {data.get('provider')!r}, not {pin_name!r}")
                try:
                    text = check_reply(kind, model, data)
                except _Truncated as exc:
                    self._note(model, 200, str(exc), data)
                    if truncations == TRUNCATION_RETRIES or slot == "paid":
                        raise self._err(ReplyRejected, f"{model}: reply cut off at max_tokens={tokens}")
                    truncations += 1
                    tokens = retry.get("retry_max_tokens") or min(tokens * 2, settings["max_tokens_cap"])
                    continue
                except _Mismatch as exc:
                    self._note(model, 200, str(exc), data)
                    self.breaker.failed(model)
                    raise self._err(ProviderUnavailable, f"{model}: {exc}")
                except ReplyRejected as exc:
                    self._note(model, 200, str(exc), data)
                    raise self._err(ReplyRejected, f"{model}: {exc}")
                self.breaker.ok(model)
                return text, self._info(model, data, spent, attempts, tokens, started)
            if status == 429:
                self._note(model, http_status, message)
                limit = classify_429(message)
                if limit == "daily":
                    raise self._err(QuotaExhausted, f"{model}: daily free limit reached: {message[:200]}")
                _count_429(model, self.shared_home)     # upstream or per-minute only; a daily 429 stops above and is not counted
                if limit == "minute":
                    self._wait_before_retry(model, retries, retry_after(headers) or MINUTE_WAIT_DEFAULT_S, "rate limited per minute", attempts + 1)
                    retries += 1
                elif kind == "reviewer":                # reviewer upstream 429: its own window; these do not count toward the breaker
                    self._reviewer_window_wait(model, started, window, retry_after(headers), message, attempts + 1)
                    window += 1
                else:
                    self.breaker.failed(model)
                    self._wait_before_retry(model, retries, self._backoff_s(retries, retry_after(headers)), "provider rate limited upstream", attempts + 1)
                    retries += 1
                continue
            if status == 403:
                self._note(model, http_status, message)
                self.breaker.drop(model)
                raise self._err(ModelDenied, f"{model}: HTTP 403: {message[:200]}")
            if status in (401, 402):
                self._note(model, http_status, message)
                raise self._err(AccountBlocked, f"{model}: HTTP {status}: {message[:200]}")
            if status >= 500:
                self._note(model, http_status, message)
                self.breaker.failed(model)
                self._wait_before_retry(model, retries, self._backoff_s(retries, None), f"HTTP {status}", attempts + 1)
                retries += 1
                continue
            self._note(model, http_status, message)
            raise self._err(OpenRouterError, f"{model}: HTTP {status}: {message[:200]}")

    def _reserve(self, model, slot, system, user, tokens):
        """Takes this attempt's budget before anything is sent. Free: the job count, then the account ledger (waiting out the minute window).
        Paid: the endpoint estimate."""
        if slot == "free" and self.shared is None:      # solo, probe and tests: a job on the loop home, so the call counts on the shared ledger
            self.set_job(*shared_job("openrouter-client", self.ledger_dir))
        if self.shared is not None:
            return self._reserve_shared(model, slot, system, user, tokens)
        if slot == "paid":
            estimate = review_reservation(system, user, tokens, roster_state()["PRICING"][model])
            self.budget.check_review(estimate)          # refused before the endpoint is read or anything is sent
            self.endpoint(model)
            self.budget.reserve_review(estimate)
            return estimate
        raise AssertionError("unreachable: a free call always takes the shared ledger")

    def _reserve_shared(self, model, slot, system, user, tokens):
        """The account ledger (package E). A wait for the minute window or the pace counts against the unit deadline: it is cut off there."""
        sl = _shared_limits()
        job_id, gen = self.shared
        home = self.shared_home
        if slot == "paid":
            estimate = review_reservation(system, user, tokens, roster_state()["PRICING"][model])
            self.budget.check_review(estimate)
            self.endpoint(model)
            self.budget.reserve_review(estimate)
            try:
                sl.reserve_review(job_id, gen, estimate, home_dir=home)
            except sl.ReviewCapReached as exc:
                self.budget.settle_review(estimate, 0.0)
                raise self._err(ReviewBudgetExceeded, str(exc))
            try:
                sl.pace(timeout_s=self._deadline_left(), home_dir=home)
            except TimeoutError as exc:
                sl.settle_review(job_id, gen, estimate, 0.0, home_dir=home)
                self.budget.settle_review(estimate, 0.0)
                raise self._err(ProviderUnavailable, f"{model}: no send pace before the unit deadline ({exc})")
            return estimate
        self.budget.check_free()
        while True:
            try:
                sl.reserve_free(job_id, gen, home_dir=home)
                break
            except sl.DailyCapReached as exc:
                raise self._err(QuotaExhausted, str(exc))
            except sl.MinuteFull:
                left = self._deadline_left()
                if left is not None and left <= 0:
                    raise self._err(ProviderUnavailable, f"{model}: the account minute window stayed full until the unit deadline")
                self.sleep(1.0 if left is None else min(1.0, left))
        self.budget.free_used += 1
        return 0.0

    def _send(self, model, slot, system, user, tokens, private, timeout_s):
        """_post, inside the model's slot on the account ledger when the client is on it. The wait for the slot is bounded by the unit
        deadline, so it counts against the job's time."""
        if self.shared is None:
            return self._post(model, slot, system, user, tokens, private, timeout_s)
        sl = _shared_limits()
        job_id, gen = self.shared
        try:
            sl.slot_name(model)
        except ValueError as exc:
            raise self._err(ModelDenied, str(exc))
        left = self._deadline_left()
        if left is not None and left <= 0:
            raise self._err(ProviderUnavailable, f"{model}: the unit deadline passed before a slot was free")
        slot_cm = sl.model_slot(model, gen, timeout_s if left is None else left, home_dir=self.shared_home)
        try:
            slot_cm.__enter__()
        except TimeoutError as exc:
            raise self._err(ProviderUnavailable, f"{model}: no free model slot before the unit deadline ({exc})")
        try:
            return self._post(model, slot, system, user, tokens, private, timeout_s)
        finally:
            slot_cm.__exit__(None, None, None)

    def _settle(self, slot, model, reserved, data):
        """A free attempt costs what its reply reports (0 when it reports nothing). A GLM attempt releases its reservation and keeps what the
        reply costs; with no reply, the whole reservation is kept as spent."""
        if slot == "free":
            return _reported_cost(data) or 0.0
        spent = review_cost(data, roster_state()["PRICING"][model]) if data is not None else None
        if spent is None:
            spent = reserved
        self.budget.settle_review(reserved, spent)
        if self.shared is not None:
            job_id, gen = self.shared
            _shared_limits().settle_review(job_id, gen, reserved, spent, home_dir=self.shared_home)
        return spent

    def _post(self, model, slot, system, user, tokens, private, timeout_s):
        settings = roster_state()["SETTINGS"][model]
        body = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "max_tokens": tokens, "temperature": settings["temperature"]}
        if settings["reasoning"] is not None:
            body["reasoning"] = dict(settings["reasoning"])
        if settings["sampling"]:
            body.update(settings["sampling"])
        if slot == "paid":                              # a paid role's request never asks for more than its roster max_tokens
            body["max_tokens"] = min(tokens, settings["max_tokens"])
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

    def _wait_before_retry(self, model, retries, seconds, why, attempt):
        if retries >= MAX_RETRIES:
            raise self._err(ProviderUnavailable, f"{model}: {why}; gave up after {retries + 1} attempts")
        if seconds > MAX_WAIT_S:
            raise self._err(ProviderUnavailable, f"{model}: {why}; asked to wait {seconds:.0f} s, which is longer than {MAX_WAIT_S:.0f} s")
        self._log_retry(model, attempt, seconds, why)
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


def probe_roles() -> list[str]:
    """The probe names, from the active roster profile (read per call): every writer and reviewer role whose model is a real free model (no
    sentinel, no paid role). The paid reviewer (GLM in production) is never probed, so the probe spends nothing."""
    state = roster_state()
    return [r for r in WRITER_ROLES + REVIEWER_ROLES if state["ROLES"][r] not in SENTINELS and not state["ROLE_SPECS"][r]["paid"]]


def probe(client, out_path=None, names=None):
    """One live call per roster model (kind text), plus each loaded key's state before and after. `names` limits the run to some of the
    probe names. The summary holds no key value; every failed attempt is kept in full (status and first 2000 characters)."""
    def key_states():
        return {slot: {k: v for k, v in client.key_info(slot).items() if k in KEY_FIELDS} for slot in client.keys}

    before = key_states()
    results = {}
    targets = probe_roles()
    if names is not None:
        targets = [t for t in targets if t in names]
    for name in targets:
        model = resolve(name)
        try:
            text, info = client.call(name, PROBE_SYSTEM, PROBE_USER, kind="text", max_tokens=probe_max_tokens())
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
    ap.add_argument("--models", help="comma list of probe names to run (the probe roles of the active profile); default all")
    args = ap.parse_args(argv)
    names = [m.strip() for m in args.models.split(",") if m.strip()] if args.models else None
    if names is not None and not set(names) & set(probe_roles()):
        print(f"no matching role: {args.models!r}; probe roles in the {active_profile()} profile: {', '.join(probe_roles())}", file=sys.stderr)
        return 2
    keys = load_keys()
    if not keys:
        print("no key found: set OPENROUTER_FREE_KEY and/or OPENROUTER_PAID_KEY, or put them in secrets/", file=sys.stderr)
        return 2
    try:
        summary = probe(OpenRouter(keys, args.ledger), args.out, names)
    except OpenRouterError as exc:
        print(f"probe stopped: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if all(p.get("ok") for p in summary["probes"].values()) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
