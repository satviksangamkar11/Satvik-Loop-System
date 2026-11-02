# Package E: shared account limits

Branch `wp-shared-limits` from `main` (8027d82). Files: `scripts/source/shared_limits.py` (new), `pieces/test_shared_limits.py` (new), this file. No model call, no network, `openrouter_call.py` not touched.

## What it does

One ledger for every loop on the machine, in `SATVIK_HOME/ledger/limits.json`, written under the loop home lock (`lock`, via `loop_home`). The model slots use `loop_home.gate.slot`.

- Free requests today. The day turns over at 05:30 IST (00:00 UTC, OpenRouter's reset). `day_key()` does the shift.
- Every request sent in the last 60 seconds, free and paid. This is the account-wide pace.
- Review spend today in micro-USD, with reservations held until `settle_review`.

## Interface

```
reserve_free(job_id, gen)                 # refused: DailyCapReached (FREE_DAILY_CAP 1000), MinuteFull (PER_MINUTE 15)
reserve_review(job_id, gen, usd)          # refused: ReviewCapReached (REVIEW_CAP_MICRO = $0.50, spend + holds)
settle_review(job_id, gen, reserved, spent)
pace() -> send time                       # waits until fewer than 15 sends in 60 s, then records this send
model_slot(model, gen, timeout_s)         # context manager; TimeoutError when no slot frees in time
slot_name(model) -> str                   # "dots", "xing", "north", "glm", "opus", "sandbox", or a roster id containing one; else ValueError
usage() -> {day, free_today, sent_last_60s, review_spent_usd, review_reserved_usd}
```

Slots: Dots 1, Xing 1, North 1, GLM 2, Opus 1, sandbox 4.

Refusals subclass `LimitRefused`. A stale generation raises `loop_home.Fenced` and counts nothing.

## How a caller uses it

- Free request: `reserve_free` only. It counts its own send, so do not call `pace()` for it.
- Paid request: `reserve_review` first (cheap refusal), then `pace()`, then send, then `settle_review`.
- Every request: wrap the send in `model_slot` (the slot count is per model).

## Tests

`pieces/test_shared_limits.py` (15), real child processes sharing one `SATVIK_HOME`:

- day turnover at 05:30 IST, both sides of the boundary;
- the ledger needs `SATVIK_HOME`, and lives in `ledger/limits.json`;
- 4 processes x 10 free attempts with a cap of 5: exactly 5 sent, one ledger total;
- 4 processes x 10 free attempts at the real cap: exactly 15 sent in the minute;
- 4 processes x 3 review holds of $0.20: exactly 2 held; then settle returns the unused part and the next holds fit or are refused;
- a stale generation (gen 1 after gen 2 took the lease) is fenced for free and review, and the refused call counts nothing;
- per model (6 cases, the roster ids and short names): holders up to the slot count get in, the next one times out;
- an unknown model has no slot;
- 4 processes x 4 `pace()` calls = 16 sends: any 16 consecutive sends span at least 60 s. This test takes about 60 s of real time.

Result: 15 passed. Mutation check: with `PER_MINUTE = 16` and the review cap at $0.60, the minute test and the review test both fail. The file was restored after.

## Decisions beyond the spec

- The minute window counts all sends. `reserve_free` checks and records its send. `pace()` waits and records its send. `reserve_review` holds spend only and does not count a send, so a paid call is counted by `pace()`, never by `reserve_review`.
- Day counts reset on the first write of a new IST day. A reservation settled on a later day is booked on that later day (`settle_review` clamps the hold at zero).
- A failed attempt keeps its count (as in `openrouter_call.FreeLedger`).
- Constants are local copies (`FREE_DAILY_CAP`, `PER_MINUTE`), not imported from `openrouter_call`, so this module imports nothing from the adapter. The hook PR should keep the two in step, or import them.

## Known gaps

- `model_slot(model, gen, timeout_s)` cannot fence a stale generation: it has no job id. It records `gen` on the gate holder only. Add a `job_id` argument if the hook needs fencing there.
- A stale `settle_review` is refused, so its hold stays reserved until the day rolls over. This is conservative: it can only reduce spend.
- `pace()` waits in a loop with a 10 ms floor and no timeout. The window always clears within 60 s, so it cannot wait longer than that.
- Not wired into `openrouter_call.py`. That is the separate PR after package D merges.
- Windows only: the POSIX `fcntl` path is not run here.
