"""Shared account limits (package E): one ledger for every loop on this machine, in SATVIK_HOME/ledger/limits.json, under the loop home lock.

It holds three things:
- free requests sent today; the day turns over at 05:30 IST (00:00 UTC, OpenRouter's reset);
- every request sent in the last 60 seconds, free and paid, the account-wide pace;
- the review spend for today in micro-USD, with reservations held until settle_review.

Callers: a free request calls reserve_free (it counts its own send). A paid request calls reserve_review, then pace(), then sends, then
settle_review. Every reserve and settle takes the job id and generation and raises loop_home.Fenced when the generation is stale; a refused
call counts nothing. model_slot takes no job id, so it cannot fence: it only records the generation on the gate holder.
Stdlib only, no network, no model call.
ponytail: one JSON file under the one loop lock; a per-file lock if throughput ever matters. A reservation settled on a later day counts
against that later day's spend."""
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import loop_home as lh

IST = timezone(timedelta(hours=5, minutes=30))
FREE_DAILY_CAP = 1000            # per account once $10 is bought (openrouter_call.FREE_DAILY_CAP)
PER_MINUTE = 15                  # the whole account, free and paid; OpenRouter allows 20
WINDOW_S = 60.0
REVIEW_CAP_MICRO = 500_000       # $0.50 of review spend per day
SLOTS = {"dots": 1, "xing": 1, "north": 1, "nemotron": 1, "glm": 2, "opus": 1, "sandbox": 4}


class LimitRefused(RuntimeError):
    """The call was refused before anything was sent or counted."""


class DailyCapReached(LimitRefused):
    pass


class MinuteFull(LimitRefused):
    pass


class ReviewCapReached(LimitRefused):
    pass


def day_key(now) -> str:
    """The ledger day: IST shifted back 5 h 30 min, so it changes at 05:30 IST."""
    return (datetime.fromtimestamp(now, IST) - timedelta(hours=5, minutes=30)).date().isoformat()


def _micro(usd) -> int:
    value = round(float(usd) * 1_000_000)
    if value < 0:
        raise ValueError(f"negative amount {usd!r}")
    return value


def _path(root) -> Path:
    return Path(root) / "ledger" / "limits.json"


def _state(root, now) -> dict:
    """The ledger as it stands at `now`: a new day clears the day counts, and the 60 s window keeps only the recent sends."""
    raw = lh._load(_path(root), {})
    day = day_key(now)
    fresh = raw.get("day") != day
    return {
        "day": day,
        "free_used": 0 if fresh else int(raw.get("free_used", 0)),
        "review_reserved": 0 if fresh else int(raw.get("review_reserved", 0)),
        "review_spent": 0 if fresh else int(raw.get("review_spent", 0)),
        "minute": [float(t) for t in raw.get("minute", []) if now - float(t) < WINDOW_S],
        "notes": list(raw.get("notes", [])),
    }


def _save_state(root, state):
    lh._save(_path(root), state)


def reserve_free(job_id, gen, note=None, home_dir=None):
    """One free request for `job_id`: refused when today's count is at FREE_DAILY_CAP or PER_MINUTE requests were sent in the last 60 s.
    A request that is then sent and fails still counts. `note` (optional) is kept in the ledger's "notes" list with the job id."""
    root = lh.home() if home_dir is None else Path(home_dir)
    with lh._locked(root):
        lh._fence(root, job_id, gen)
        now = time.time()
        st = _state(root, now)
        if st["free_used"] >= FREE_DAILY_CAP:
            raise DailyCapReached(f"{st['free_used']} free requests today, cap {FREE_DAILY_CAP}; the count resets at 05:30 IST")
        if len(st["minute"]) >= PER_MINUTE:
            raise MinuteFull(f"{PER_MINUTE} requests were sent in the last {WINDOW_S:.0f} s")
        st["free_used"] += 1
        st["minute"].append(now)
        if note:
            st["notes"].append({"job": job_id, "note": str(note)})
        _save_state(root, st)


def reserve_review(job_id, gen, usd, home_dir=None):
    """Hold `usd` of today's review spend for `job_id`. Refused when spend plus holds would pass REVIEW_CAP_MICRO (`$0.50`)."""
    micro = _micro(usd)
    root = lh.home() if home_dir is None else Path(home_dir)
    with lh._locked(root):
        lh._fence(root, job_id, gen)
        st = _state(root, time.time())
        if st["review_spent"] + st["review_reserved"] + micro > REVIEW_CAP_MICRO:
            raise ReviewCapReached(f"review spend today would pass ${REVIEW_CAP_MICRO / 1e6:.2f}; nothing was sent")
        st["review_reserved"] += micro
        _save_state(root, st)


def settle_review(job_id, gen, reserved, spent, home_dir=None):
    """Release the `reserved` hold and book the `spent` actually paid."""
    reserved_micro, spent_micro = _micro(reserved), _micro(spent)
    root = lh.home() if home_dir is None else Path(home_dir)
    with lh._locked(root):
        lh._fence(root, job_id, gen)
        st = _state(root, time.time())
        st["review_reserved"] = max(0, st["review_reserved"] - reserved_micro)
        st["review_spent"] += spent_micro
        _save_state(root, st)


def pace(timeout_s=None, home_dir=None) -> float:
    """Wait until fewer than PER_MINUTE requests were sent in the last 60 s, then record this send. Returns the send time.
    Call it before every paid send (after reserve_review). reserve_free records its own send, so do not call pace for a free request.
    TimeoutError, without recording a send, when the wait would pass `timeout_s` (the unit deadline's time left)."""
    root = lh.home() if home_dir is None else Path(home_dir)
    end = None if timeout_s is None else time.time() + timeout_s
    while True:
        with lh._locked(root):
            now = time.time()
            st = _state(root, now)
            if len(st["minute"]) < PER_MINUTE:
                st["minute"].append(now)
                _save_state(root, st)
                return now
            wait = min(st["minute"]) + WINDOW_S - now
        if end is not None and time.time() + max(wait, 0.01) > end:
            raise TimeoutError(f"the account pace is full for another {wait:.1f} s")
        time.sleep(max(wait, 0.01))


def slot_name(model) -> str:
    """The gate name for a model: a short name ("dots") or a roster id that contains one of them. Anything else has no slot."""
    text = str(model).lower()
    for name in SLOTS:
        if text == name or name in text:
            return name
    raise ValueError(f"no slot for model {model!r}; known: {', '.join(SLOTS)}")


@contextmanager
def model_slot(model, gen, timeout_s, home_dir=None):
    """Hold one slot of the model's gate for the block. TimeoutError when none frees within timeout_s."""
    name = slot_name(model)
    gate = lh.gate if home_dir is None else lh.Gate(home_dir)
    with gate.slot(name, gen, timeout_s, capacity=SLOTS[name]):
        yield


def usage(home_dir=None) -> dict:
    """What the ledger holds now (read-only): free requests today, sends in the last 60 s, review spend and holds in USD."""
    root = lh.home() if home_dir is None else Path(home_dir)
    with lh._locked(root):
        st = _state(root, time.time())
    return {"day": st["day"], "free_today": st["free_used"], "sent_last_60s": len(st["minute"]),
            "review_spent_usd": st["review_spent"] / 1e6, "review_reserved_usd": st["review_reserved"] / 1e6}
