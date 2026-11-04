"""Shared account limits (package E): one ledger for every loop on this machine, in SATVIK_HOME/ledger/limits.json, under the loop home lock.

It holds these things:
- free requests sent today, against the day's free cap: the account's live daily allowance as the last begin_job read it (record_free_cap),
  else FREE_DAILY_CAP; the day turns over at 05:30 IST (00:00 UTC, OpenRouter's reset);
- every request sent in the last 60 seconds, free and paid, the account-wide pace (pace, called right before each POST, inside the model slot);
- the review spend for today in micro-USD, and each open paid reservation ("hold") under its own call id: {job, gen, usd, pid, start, at}.
  A hold records its owner (pid and process start time). A hold is released as spent (conservatively: its whole reserved amount) when its
  owner is dead and the hold is older than HOLD_STALE_S, or when it is older than HOLD_MAX_AGE_S whatever its owner. Holds are never cleared
  at the day change: an open hold counts against the day it is still open on. The spend booked per job is kept too, so a job's approved
  ceiling (reserve_review ceiling_usd) holds across restarts.

Callers: a free request calls reserve_free_day (its daily count), then pace() right before the POST. A paid request calls reserve_review
(it returns the call id), then pace(), then sends, then settle_review(call_id=...). reserve_free is the older one-step form (count and pace
together); it stays for solo use and tests. Every reserve and settle takes the job id and generation and raises loop_home.Fenced when the
generation is stale; a refused call counts nothing. model_slot takes no job id, so it cannot fence: it only records the generation on the
gate holder. Stdlib only, no network, no model call.
ponytail: one JSON file under the one loop lock; a per-file lock if throughput ever matters."""
import os
import secrets
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import loop_home as lh

IST = timezone(timedelta(hours=5, minutes=30))
FREE_DAILY_CAP = 1000            # the fallback free cap per day, used while no live allowance is recorded (free_cap_of reads it at each call)
PER_MINUTE = 15                  # the whole account, free and paid; OpenRouter allows 20
WINDOW_S = 60.0
REVIEW_CAP_MICRO = 500_000       # $0.50 of review spend per day
SLOTS = {"dots": 1, "xing": 1, "north": 1, "nemotron": 1, "glm": 2, "opus": 1, "sandbox": 4}
OWN_SLOT = 1                     # a model named by none of SLOTS gets a gate of its own, this wide (2026-11-03: any roster model may be used)
HOLD_STALE_S = lh.STALE_S        # a hold whose owner is dead and is this old is released as spent (the lease takeover rule's 5 minutes)
HOLD_MAX_AGE_S = 3600.0          # a hold this old is released as spent whatever its owner: no paid call runs for an hour


class LimitRefused(RuntimeError):
    """The call was refused before anything was sent or counted."""


class DailyCapReached(LimitRefused):
    pass


class MinuteFull(LimitRefused):
    pass


class ReviewCapReached(LimitRefused):
    pass


class JobCeilingReached(ReviewCapReached):
    """The job's approved paid ceiling (its booked spend plus its open holds) would pass with this reservation."""


def day_key(now) -> str:
    """The ledger day: IST shifted back 5 h 30 min, so it changes at 05:30 IST."""
    return (datetime.fromtimestamp(now, IST) - timedelta(hours=5, minutes=30)).date().isoformat()


def _micro(usd) -> int:
    value = round(float(usd) * 1_000_000)
    if value < 0:
        raise ValueError(f"negative amount {usd!r}")
    return value


def _review_cap_micro() -> int:
    """The daily review cap in millionths of a USD: config/limits.json (limits_config, read at each call), REVIEW_CAP_MICRO when unreadable."""
    try:
        import limits_config
        return _micro(limits_config.review_usd_daily_cap())
    except Exception:
        return REVIEW_CAP_MICRO


def _path(root) -> Path:
    return Path(root) / "ledger" / "limits.json"


def _whole_or_none(value):
    """A positive whole number, else None (a bool is not one)."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _owner_alive(hold) -> bool:
    """True while the hold's owner is the same live process that took it (its pid, and its start time when one was recorded)."""
    return lh.owner_alive(hold.get("pid", 0), hold.get("start"))


def _book(st, job_id, micro):
    """Books `micro` as spent today and on the job."""
    st["review_spent"] += micro
    job = st["jobs"].setdefault(job_id, {"spent": 0})
    job["spent"] = int(job.get("spent", 0)) + micro


def _reconcile(st, now):
    """Releases the holds that can no longer be settled, conservatively: each is booked as spent for its whole reserved amount, since its call
    may have been sent. A hold of a live owner is kept until HOLD_MAX_AGE_S. Mutates st; the caller saves it."""
    for call_id, hold in list(st["holds"].items()):
        age = now - float(hold.get("at", 0.0))
        if age > HOLD_MAX_AGE_S or (age > HOLD_STALE_S and not _owner_alive(hold)):
            del st["holds"][call_id]
            _book(st, hold["job"], int(hold["usd"]))


def _state(root, now) -> dict:
    """The ledger as it stands at `now`: a new day clears the day counts, and the 60 s window keeps only the recent sends. The free cap is the
    last live read (record_free_cap), kept across days until the next read; None means no live read applies (the fallback). Open holds are
    kept across the day change and reconciled (_reconcile)."""
    raw = lh._load(_path(root), {})
    # The accounting epoch never runs backwards (I57): a clock that jumps back keeps the latest instant seen, so the day counts reset once
    # at a day change and never again when the clock returns to the old day. A jump forward is kept too (spend is counted, never freed).
    now = max(float(now), float(raw.get("epoch", 0.0) or 0.0))
    day = day_key(now)
    fresh = raw.get("day") != day
    st = {
        "epoch": now,
        "day": day,
        "free_used": 0 if fresh else int(raw.get("free_used", 0)),
        "free_cap": _whole_or_none(raw.get("free_cap")),
        "review_spent": 0 if fresh else int(raw.get("review_spent", 0)),
        "holds": {str(k): dict(v) for k, v in dict(raw.get("holds", {})).items()},
        "jobs": {str(k): dict(v) for k, v in dict(raw.get("jobs", {})).items()},
        "minute": [float(t) for t in raw.get("minute", []) if now - float(t) < WINDOW_S],
        "notes": list(raw.get("notes", [])),
    }
    _reconcile(st, now)
    st["review_reserved"] = sum(int(h["usd"]) for h in st["holds"].values())
    return st


def _save_state(root, state):
    lh._save(_path(root), state)


def free_cap_of(st) -> tuple:
    """The day's free cap and its source: the live allowance recorded on the ledger ("live"), else FREE_DAILY_CAP ("fallback"). The fallback
    is read here, at each call, so a changed FREE_DAILY_CAP takes effect at once."""
    if st["free_cap"] is None:
        return FREE_DAILY_CAP, "fallback"
    return st["free_cap"], "live"


def record_free_cap(live, home_dir=None) -> dict:
    """Records the day's free cap on the ledger. `live` is the account's daily allowance from GET /api/v1/key (a positive whole number), or None
    when that read failed or the field is missing: the fallback FREE_DAILY_CAP then applies. Returns {"cap", "source"} for what now applies.
    Not fenced: the cap is the account's allowance, not a job's count (as model_slot is not fenced)."""
    root = lh.home() if home_dir is None else Path(home_dir)
    with lh._locked(root):
        st = _state(root, time.time())
        st["free_cap"] = _whole_or_none(live)
        _save_state(root, st)
        cap, source = free_cap_of(st)
    return {"cap": cap, "source": source}


def reserve_free(job_id, gen, note=None, home_dir=None):
    """One free request for `job_id`: refused when today's count is at the day's free cap (free_cap_of) or PER_MINUTE requests were sent in the
    last 60 s. A request that is then sent and fails still counts. `note` (optional) is kept in the ledger's "notes" list with the job id.
    The one-step form (count and send time together); the client uses reserve_free_day and pace instead."""
    root = lh.home() if home_dir is None else Path(home_dir)
    with lh._locked(root):
        lh._fence(root, job_id, gen)
        now = time.time()
        st = _state(root, now)
        cap, source = free_cap_of(st)
        if st["free_used"] >= cap:
            raise DailyCapReached(f"{st['free_used']} free requests today, cap {cap} ({source}); the count resets at 05:30 IST")
        if len(st["minute"]) >= PER_MINUTE:
            raise MinuteFull(f"{PER_MINUTE} requests were sent in the last {WINDOW_S:.0f} s")
        st["free_used"] += 1
        st["minute"].append(now)
        if note:
            st["notes"].append({"job": job_id, "note": str(note)})
        _save_state(root, st)


def reserve_free_day(job_id, gen, home_dir=None):
    """One free request counted against today's cap (free_cap_of). It records no send time: pace() does that, right before the POST, so a
    request that waits for its model slot does not age its pace record while it waits. Refused (DailyCapReached) at the cap."""
    root = lh.home() if home_dir is None else Path(home_dir)
    with lh._locked(root):
        lh._fence(root, job_id, gen)
        st = _state(root, time.time())
        cap, source = free_cap_of(st)
        if st["free_used"] >= cap:
            raise DailyCapReached(f"{st['free_used']} free requests today, cap {cap} ({source}); the count resets at 05:30 IST")
        st["free_used"] += 1
        _save_state(root, st)


def reserve_review(job_id, gen, usd, home_dir=None, ceiling_usd=None) -> str:
    """Hold `usd` of today's review spend for `job_id` under a new call id, which is returned. Refused when spend plus holds would pass
    REVIEW_CAP_MICRO (`$0.50`), or, with `ceiling_usd`, when the job's booked spend plus its holds would pass that ceiling (JobCeilingReached).
    The hold records this process as its owner."""
    micro = _micro(usd)
    ceiling = None if ceiling_usd is None else _micro(ceiling_usd)
    root = lh.home() if home_dir is None else Path(home_dir)
    with lh._locked(root):
        lh._fence(root, job_id, gen)
        now = time.time()
        st = _state(root, now)
        cap = _review_cap_micro()
        if st["review_spent"] + st["review_reserved"] + micro > cap:
            raise ReviewCapReached(f"review spend today would pass ${cap / 1e6:.2f}; nothing was sent")
        if ceiling is not None:
            held = sum(int(h["usd"]) for h in st["holds"].values() if h["job"] == job_id)
            booked = int(st["jobs"].get(job_id, {}).get("spent", 0))
            if booked + held + micro > ceiling:
                raise JobCeilingReached(f"{job_id}: its paid spend would pass the approved ceiling ${ceiling / 1e6:.6f}; nothing was sent")
        call_id = secrets.token_hex(8)
        st["holds"][call_id] = {"job": job_id, "gen": gen, "usd": micro, "pid": os.getpid(),
                                "start": lh.process_start(os.getpid()), "at": now}
        _save_state(root, st)
    return call_id


def settle_review(job_id, gen, reserved, spent, home_dir=None, call_id=None):
    """Release one hold and book the `spent` actually paid. With `call_id` (what reserve_review returned) the hold is that call's. Without it,
    the job's hold of the same amount is taken (ValueError when there is none). A hold already released by reconciliation is not booked twice:
    its reserved amount was booked as spent then, so a late settle changes nothing."""
    reserved_micro, spent_micro = _micro(reserved), _micro(spent)
    root = lh.home() if home_dir is None else Path(home_dir)
    with lh._locked(root):
        lh._fence(root, job_id, gen)
        st = _state(root, time.time())
        if call_id is None:
            call_id = next((c for c, h in st["holds"].items() if h["job"] == job_id and int(h["usd"]) == reserved_micro), None)
            if call_id is None:
                raise ValueError(f"{job_id} holds no review reservation of ${reserved}")
        hold = st["holds"].get(call_id)
        if hold is None:                              # released by reconciliation already: booked then
            _save_state(root, st)
            return
        if hold["job"] != job_id:
            raise ValueError(f"reservation {call_id} belongs to {hold['job']}, not {job_id}")
        del st["holds"][call_id]
        _book(st, job_id, spent_micro)
        _save_state(root, st)


def pace(timeout_s=None, home_dir=None, clock=None, sleep=None) -> float:
    """Wait until fewer than PER_MINUTE requests were sent in the last 60 s, then record this send. Returns the send time.
    The client calls it immediately before each POST, after its model slot is held (free and paid alike). reserve_free records its own send
    and reserve_free_day does not, so each request is paced once, here.
    TimeoutError, without recording a send, when the wait would pass `timeout_s` (the unit deadline's time left). `clock` and `sleep` default
    to time.time and time.sleep; the tests inject a clock shared by several processes."""
    clock = clock or time.time
    sleep = sleep or time.sleep
    root = lh.home() if home_dir is None else Path(home_dir)
    end = None if timeout_s is None else clock() + timeout_s
    while True:
        with lh._locked(root):
            now = clock()
            st = _state(root, now)
            if len(st["minute"]) < PER_MINUTE:
                st["minute"].append(now)
                _save_state(root, st)
                return now
            wait = min(st["minute"]) + WINDOW_S - now
        if end is not None and clock() + max(wait, 0.01) > end:
            raise TimeoutError(f"the account pace is full for another {wait:.1f} s")
        sleep(max(wait, 0.01))


def slot_name(model) -> str:
    """The gate name for a model: a short name ("dots") or a roster id that contains one of them; any other model id is a gate of its own
    (OWN_SLOT wide), so a model added to config/roster.json needs no change here. The roster stays the allow-list: the adapter refuses a
    model it does not name before a slot is asked for. An empty or non-text model has no slot."""
    if not isinstance(model, str) or not model.strip():
        raise ValueError(f"no slot for model {model!r}")
    text = model.strip().lower()
    for name in SLOTS:
        if text == name or name in text:
            return name
    return text


def slot_capacity(name) -> int:
    """How many calls the gate `name` (from slot_name) lets run at once."""
    return SLOTS.get(name, OWN_SLOT)


@contextmanager
def model_slot(model, gen, timeout_s, home_dir=None):
    """Hold one slot of the model's gate for the block. TimeoutError when none frees within timeout_s."""
    name = slot_name(model)
    gate = lh.gate if home_dir is None else lh.Gate(home_dir)
    with gate.slot(name, gen, timeout_s, capacity=slot_capacity(name)):
        yield


def usage(home_dir=None) -> dict:
    """What the ledger holds now (read-only): free requests today and the day's free cap with its source, sends in the last 60 s, review spend
    and holds in USD, and the number of open holds."""
    root = lh.home() if home_dir is None else Path(home_dir)
    with lh._locked(root):
        st = _state(root, time.time())
    cap, source = free_cap_of(st)
    return {"day": st["day"], "free_today": st["free_used"], "free_cap": cap, "free_cap_source": source,
            "sent_last_60s": len(st["minute"]), "review_spent_usd": st["review_spent"] / 1e6,
            "review_reserved_usd": st["review_reserved"] / 1e6, "open_holds": len(st["holds"])}
