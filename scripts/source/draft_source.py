"""DRAFTS for owner review (decision D-018), never canon: English meanings of one Adhyay's ovis and its characters/places list, from free NVIDIA chat models.
One model drafts, a model of another family checks each meaning; names in the characters list are string-checked against the ovis they cite. Nothing here is approved.

  python scripts/source/draft_source.py --adhyay 1 [--drafter deepseek-ai/deepseek-v4.1-flash] [--checker z-ai/glm-5.3] [--only meanings|characters]

In:  canon/source_hj/adhyay_NN.jsonl (ovis). Out: canon/drafts/aNN_meanings_DRAFT.jsonl, aNN_characters_places_DRAFT.json, aNN_REVIEW.md, aNN_receipt.json.
Keys: nvidia_trial.load_keys (never printed); requests only to NVIDIA hosts, no redirects, every reply scrubbed of keys."""
from __future__ import annotations

import argparse
import http.client
import json
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "image_trials"))
import nvidia_trial as nt  # noqa: E402

URL = "https://integrate.api.nvidia.com/v1/chat/completions"
BATCH = 5
CONTEXT = 3

MEANING_SYS = """You translate Marathi ovis of Shri Gajanan Vijay (by Dasganu) into plain English for a film team.
For each ovi in TRANSLATE give a faithful, close meaning in 1-3 short sentences.
Rules:
- Add nothing: no facts, interpretation, praise or explanation that the ovi does not contain.
- Keep every name as written, transliterated (e.g. Bankatlal, Damodarpant, Devidas). Keep numbers and dates exact.
- If a word or line is unclear, give your best close reading, set "unclear": true and say why in "note".
- "speaker": who is speaking in this ovi if the ovi or the CONTEXT makes it clear, else null. "Dasganu" when the poet speaks.
- CONTEXT ovis are for understanding only; do not translate them.
Output JSON only, no other text: [{"ovi": n, "meaning_en": "...", "speaker": null, "unclear": false, "note": ""}]"""

CHECK_SYS = """You check English meanings of Marathi ovis of Shri Gajanan Vijay.
Judge each meaning against the Marathi ovi itself; CONTEXT ovis are only for understanding.
For each ovi: "OK" if the meaning is faithful and adds nothing; "ISSUE" if anything is missing, added, reversed, mistranslated, or a name, number or the SPEAKER is wrong.
For ISSUE, "note" says what is wrong and gives a corrected meaning. Do not flag style.
Output JSON only, no other text: [{"ovi": n, "verdict": "OK", "note": ""}]"""

CHARCHECK_SYS = """You check a draft list of people and places made from the ovis of one Adhyay of Shri Gajanan Vijay. Use ONLY the ovis given.
Report a problem when: an entry's role, description or name is not stated in its cited ovis; a person or place in the narrated events is missing; two entries are the same person; a person only named in an invocation is listed as a character.
Output JSON only, no other text: {"problems": [{"id": "char_id or place_id or MISSING", "problem": "one sentence", "ovis": [n]}]}  (empty list if none)"""

CHAR_SYS = """You list the people and places of one Adhyay of Shri Gajanan Vijay, using ONLY the ovis given. Do not use outside knowledge of the Granth.
characters: everyone who appears or acts in the narrated events (not people only named in invocations or lists of saints). For each:
  "char_id": snake_case ASCII starting "char_" (e.g. "char_bankatlal"),
  "names_in_text": the exact Marathi spellings used in the ovis (copy them character for character),
  "name_en": transliterated name, "role": one line, only what the ovis state,
  "description": only what the ovis state about age, body, clothing, objects carried; null if nothing is stated,
  "evidence_ovis": the ovi numbers where they appear or are described.
places: every place named or shown in the narrated events: "place_id" ("loc_..."), "names_in_text", "name_en", "description" (only what is stated, else null), "evidence_ovis".
named_only: people or deities only named in invocations or lists: {"name_en": "...", "ovis": [n]}.
questions_for_owner: anything unclear (for example who speaks, or whether two names are one person).
Output JSON only, no other text: {"characters": [], "places": [], "named_only": [], "questions_for_owner": []}"""


def load_ovis(adhyay: int) -> list[dict]:
    path = ROOT / "canon" / "source_hj" / f"adhyay_{adhyay:02d}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def extract_json(reply: str, kind: type):
    """The first JSON value of type `kind` (list or dict) in a model reply, after dropping <think> blocks and code fences."""
    text = re.sub(r"<think>.*?</think>", "", reply, flags=re.S)
    text = re.sub(r"```(?:json)?", "", text)
    dec = json.JSONDecoder()
    opener = "[" if kind is list else "{"
    i = text.find(opener)
    while i != -1:
        try:
            value, _ = dec.raw_decode(text, i)
            if isinstance(value, kind):
                return value
        except json.JSONDecodeError:
            pass
        i = text.find(opener, i + 1)
    raise ValueError("no JSON in reply")


def scrub_obj(value, keys: list[str]):
    """Scrub keys from every string AFTER JSON decoding (an escaped key like \\u006evapi- only becomes visible once parsed)."""
    if isinstance(value, str):
        return nt.scrub(value, keys)
    if isinstance(value, list):
        return [scrub_obj(v, keys) for v in value]
    if isinstance(value, dict):
        return {nt.scrub(str(k), keys): scrub_obj(v, keys) for k, v in value.items()}
    return value


def save(path: Path, text: str, keys: list[str]) -> None:
    path.write_text(nt.scrub(text, keys), encoding="utf-8")                # every file is scrubbed once more as written


def norm(s: str) -> str:
    return " ".join(s.split())


def check_names(entries: list, ovis: list[dict]) -> list[str]:
    """Problems: malformed entry, evidence ovi missing/out of range/not an integer, or a names_in_text spelling that appears in none of its cited ovis."""
    text = {o["ovi"]: norm(o["text"]) for o in ovis}
    problems = []
    for e in entries:
        if not isinstance(e, dict):
            problems.append(f"malformed entry: {str(e)[:80]}")
            continue
        ident = e.get("char_id") or e.get("place_id") or "?"
        cited = e.get("evidence_ovis")
        cited = cited if isinstance(cited, list) else []
        valid = [n for n in cited if isinstance(n, int) and not isinstance(n, bool) and n in text]
        bad = [n for n in cited if n not in valid]
        if bad or not cited:
            problems.append(f"{ident}: evidence ovis missing or out of range {bad}")
        cited = valid
        names = e.get("names_in_text")
        if not isinstance(names, list) or not names or not all(isinstance(n, str) and n.strip() for n in names):
            problems.append(f"{ident}: names_in_text missing or not non-empty strings")
            continue
        for name in names:
            if not any(norm(name) in text[n] for n in cited):
                problems.append(f"{ident}: name '{name}' not found in its cited ovis {cited}")
    return problems


STREAM_TOTAL_S = 2700                                              # hard cap for one streamed call; a socket idle for 120 s also ends it


USAGE_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens")
MAX_SSE_BYTES = 8_000_000


def _stream_once(key: str, body: dict) -> tuple[int, str, dict]:
    """One streamed request: (status, content, extra). Only the fixed NVIDIA URL, no redirects (nt._OPENER). Success needs HTTP 200, a finish_reason of "stop" and non-blank content: a stream that ends
    early, is cut by the token limit, or runs past the deadline is a failure. A transport problem becomes (0, '', {'error': <ExceptionType>})."""
    req = urllib.request.Request(URL, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "text/event-stream"})
    parts, usage, finish, buf, total, deadline = [], None, None, b"", 0, time.monotonic() + STREAM_TOTAL_S
    try:
        with nt._OPENER.open(req, timeout=120) as resp:                  # the timeout is per socket read (idle timeout); the deadline below also covers a trickle of bytes
            while True:
                piece = resp.read1(8192)                                  # one network read at a time, so the deadline is checked after every chunk even if bytes trickle in
                if time.monotonic() > deadline:
                    return 0, "", {"finish": "deadline", "error": "deadline"}   # not transient: a retry would take another 1200 s
                if not piece:
                    break
                total += len(piece)
                buf += piece
                if total > MAX_SSE_BYTES or len(buf) > 1_000_000:
                    return 0, "", {"finish": "too_large", "error": "stream too large"}
                while b"\n" in buf:
                    raw_line, buf = buf.split(b"\n", 1)
                    line = raw_line.decode("utf-8", "replace").strip()
                    if not line.startswith("data:") or line == "data: [DONE]":
                        continue
                    try:
                        chunk = json.loads(line[5:])
                    except ValueError:
                        continue
                    if not isinstance(chunk, dict):
                        continue
                    if isinstance(chunk.get("usage"), dict):
                        usage = chunk["usage"]
                    choices = chunk.get("choices")
                    for ch in choices if isinstance(choices, list) else []:
                        if isinstance(ch, dict):
                            delta = ch.get("delta") if isinstance(ch.get("delta"), dict) else {}
                            if isinstance(delta.get("content"), str):
                                parts.append(delta["content"])
                            if isinstance(ch.get("finish_reason"), str):
                                finish = ch["finish_reason"]
            status = resp.status
    except urllib.error.HTTPError as exc:
        return exc.code, "", {"error": f"HTTP {exc.code}"}
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as exc:
        return 0, "", {"error": type(exc).__name__}
    content = "".join(parts)
    extra = {"finish": finish if finish in ("stop", "length", "content_filter", "tool_calls") else ("none" if finish is None else "other"),
             "usage": {k: usage[k] for k in USAGE_FIELDS if isinstance(usage, dict) and isinstance(usage.get(k), int)} or None}
    if status == 200 and (finish != "stop" or not content.strip()):
        return 0, "", {**extra, "error": f"incomplete stream (finish={extra['finish']})"}
    return status, content, extra


def chat_stream(pool: nt.KeyPool, keys: list[str], model: str, system: str, user: str, max_tokens: int = 40000) -> tuple[str, dict]:
    """Like chat() but streamed, so the gateway's ~300 s cut for non-streamed calls does not apply. Key rules: 401/402/403 retire a key; 429, 5xx and transport failures (status 0 with no finish value) cool it for 60 s and the call moves on to another key; an incomplete 200 stream or any other 4xx returns at once."""
    body = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "temperature": 0.2, "max_tokens": max_tokens, "stream": True}
    t0, status, content, extra = time.time(), 0, "", {"error": "no key"}
    for _ in range(3 * len(pool.keys) + 5):
        try:
            index, key = pool.acquire()
        except nt.AllKeysUnavailable:
            break
        status, content, extra = _stream_once(key, body)
        incomplete = status == 0 and "finish" in extra        # a 200 reply that did not finish (token limit, filter, empty): the KEY worked, a retry would fail the same way
        pool.report(index, 200 if incomplete else status)
        # owner rule (P1-D14/D16): a failed REQUEST moves on to another key (report() cools the failed key); replies that fail identically on every key (incomplete stream, other 4xx) return at once
        transient = status in nt.REJECTED or status == 429 or 500 <= status < 600 or (status == 0 and not incomplete)
        if transient:
            continue
        break
    info = {"model": model, "status": status, "wall_s": round(time.time() - t0, 1), "streamed": True}
    if status != 200 or not content:
        info["error"] = nt.scrub(str(extra.get("error", "empty reply")), keys)
        info["finish"] = extra.get("finish")
        return "", info
    info.update(finish=extra.get("finish"), usage=extra.get("usage"))
    return nt.scrub(content, keys), info


def chat(pool: nt.KeyPool, keys: list[str], model: str, system: str, user: str) -> tuple[str, dict]:
    body = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "temperature": 0.2, "max_tokens": 16000}
    t0 = time.time()
    status, resp, _ = nt.call({"url": URL}, body, pool, post=lambda u, k, b: nt.http_post(u, k, b, timeout=320))
    info = {"model": model, "status": status, "wall_s": round(time.time() - t0, 1)}
    try:
        choice = resp["choices"][0]
        content = choice["message"].get("content") or ""
        if status != 200 or not isinstance(content, str):
            raise TypeError
    except (TypeError, KeyError, IndexError, AttributeError):
        info["error"] = nt.scrub(str(resp)[:200], keys)
        return "", info
    info["finish"] = str(choice.get("finish_reason"))
    usage = resp.get("usage")
    info["usage"] = {k: v for k, v in usage.items() if isinstance(v, int)} if isinstance(usage, dict) else None
    return nt.scrub(content, keys), info


def ovi_lines(ovis: list[dict]) -> str:
    return "\n".join(f"{o['ovi']}: {o['text']}" for o in ovis)


def clean_row(r: dict) -> dict | None:
    """Only the allowed fields, type-checked; None if the meaning is missing. Model-supplied fields such as "approved" are dropped."""
    meaning = r.get("meaning_en")
    if not isinstance(meaning, str) or not meaning.strip():
        return None
    speaker = r.get("speaker")
    return {"ovi": r["ovi"], "meaning_en": meaning.strip(), "speaker": speaker if isinstance(speaker, str) and speaker.strip() else None,
            "unclear": r.get("unclear") is True, "note": r.get("note") if isinstance(r.get("note"), str) else ""}


CHAR_FIELDS = ("char_id", "names_in_text", "name_en", "role", "description", "evidence_ovis")
PLACE_FIELDS = ("place_id", "names_in_text", "name_en", "description", "evidence_ovis")


def clean_chars(parsed: dict) -> dict:
    """Allowlisted fields only (a model-supplied "approved"/"status" is dropped); a collection that is not a list of objects raises ValueError."""
    out = {}
    for key, fields in (("characters", CHAR_FIELDS), ("places", PLACE_FIELDS)):
        items = parsed.get(key)
        if not isinstance(items, list) or not all(isinstance(e, dict) for e in items):
            raise ValueError(f"'{key}' is not a list of objects")
        out[key] = [{f: e.get(f) for f in fields} for e in items]
    if not out["characters"]:
        raise ValueError("no characters")
    for key in ("named_only", "questions_for_owner"):
        items = parsed.get(key, [])
        if not isinstance(items, list):
            raise ValueError(f"'{key}' is not a list")
        out[key] = items
    return out


def clean_problems(found):
    """A list of {"id","problem","ovis"} objects (possibly empty = the check found nothing); anything malformed is "NO_CHECK", never an empty list."""
    if not isinstance(found, list) or not all(isinstance(p, dict) and isinstance(p.get("problem"), str) and p["problem"].strip() for p in found):
        return "NO_CHECK"
    return [{"id": str(p.get("id", "")), "problem": p["problem"].strip(), "ovis": [n for n in p.get("ovis") or [] if isinstance(n, int) and not isinstance(n, bool)] if isinstance(p.get("ovis"), list) else []} for p in found]


def load_done(path: Path, a, ovis: list[dict]) -> dict:
    """Valid checked rows from earlier runs, from ANY drafter/checker pair (the pair is kept in each row and shown in the review file); failed and unchecked rows are redone. Malformed lines are skipped."""
    done, valid = {}, {o["ovi"] for o in ovis}
    if not path.is_file():
        return done
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
            row = clean_row(r)
            if (row and r.get("ovi") in valid and r.get("status") == "DRAFT" and isinstance(r.get("drafter"), str) and isinstance(r.get("checker"), str) and r["drafter"] != r["checker"]
                    and r.get("check") in ("OK", "ISSUE") and isinstance(r.get("check_note", ""), str)):
                done[r["ovi"]] = {**row, "drafter": r["drafter"], "checker": r["checker"], "check": r["check"], "check_note": r.get("check_note", ""), "status": "DRAFT"}
        except (ValueError, TypeError, AttributeError):
            continue
    return done


def run_batch(pool, keys, a, ovis, start) -> tuple[list[dict], list[dict]]:
    batch = ovis[start:start + BATCH]
    want = [o["ovi"] for o in batch]
    log = []
    try:
        user = f"CONTEXT:\n{ovi_lines(ovis[max(0, start - CONTEXT):start]) or '(none)'}\n\nTRANSLATE:\n{ovi_lines(batch)}"
        rows = None
        for _ in range(2):                                         # one retry on a bad or incomplete reply
            reply, info = chat(pool, keys, a.drafter, MEANING_SYS, user)
            log.append({"step": "draft", "ovis": [want[0], want[-1]], **info})
            try:
                got = {r["ovi"]: clean_row(r) for r in extract_json(reply, list) if isinstance(r, dict) and r.get("ovi") in want}
            except ValueError:
                continue
            if sorted(got) == want and all(got.values()):
                rows = [scrub_obj(got[n], keys) for n in want]
                break
        if rows is None:
            raise ValueError("no complete draft after 2 tries")
        check_user = (f"CONTEXT:\n{ovi_lines(ovis[max(0, start - CONTEXT):start]) or '(none)'}\n\nCHECK:\n"
                      + "\n\n".join(f"{o['ovi']}: {o['text']}\nMEANING: {r['meaning_en']}\nSPEAKER: {r['speaker']}" for o, r in zip(batch, rows)))
        reply, info = chat(pool, keys, a.checker, CHECK_SYS, check_user)
        log.append({"step": "check", "ovis": [want[0], want[-1]], **info})
        try:
            checks = {c.get("ovi"): c for c in extract_json(reply, list) if isinstance(c, dict)}
        except ValueError:
            checks = {}
        for r in rows:
            c = checks.get(r["ovi"], {})
            verdict = c.get("verdict") if c.get("verdict") in ("OK", "ISSUE") else "NO_CHECK"
            note = c.get("note") if isinstance(c.get("note"), str) else ""
            r.update(drafter=a.drafter, checker=a.checker, check=verdict, check_note=nt.scrub(note, keys), status="DRAFT")
        return rows, log
    except Exception as exc:                                       # a failed batch is recorded, never fatal: the other batches and the receipt are kept
        log.append({"step": "batch_failed", "ovis": [want[0], want[-1]], "error": type(exc).__name__ + ": " + nt.scrub(str(exc), keys)[:120]})
        return [{"ovi": n, "meaning_en": None, "status": "DRAFT_FAILED", "check": "NOT_CHECKED"} for n in want], log


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--adhyay", type=int, required=True)
    ap.add_argument("--drafter", default="deepseek-ai/deepseek-v4.1-flash")
    ap.add_argument("--checker", default="z-ai/glm-5.3")
    ap.add_argument("--only", choices=["meanings", "characters"])
    ap.add_argument("--char-range", help="A-B: send only these ovis to the characters step (shorter input, less thinking); evidence ovis are still checked against the whole Adhyay")
    ap.add_argument("--char-writer", help="model that drafts the characters list (default: --drafter); the whole Adhyay goes in one call, so pick a fast model")
    ap.add_argument("--char-checker", help="model that checks the characters list (default: --checker); must differ from the char writer")
    a = ap.parse_args()
    keys = nt.load_keys()
    if not keys:
        raise SystemExit("no usable NVIDIA key")
    pool = nt.KeyPool(keys)
    if a.drafter == a.checker:
        raise SystemExit("the meanings drafter and checker must be different models")
    char_writer, char_checker = a.char_writer or a.drafter, a.char_checker or a.checker
    if char_writer == char_checker:
        raise SystemExit("the characters writer and checker must be different models")
    ovis = load_ovis(a.adhyay)
    char_ovis = ovis
    if a.char_range:
        lo, hi = (int(x) for x in a.char_range.split("-"))
        char_ovis = [o for o in ovis if lo <= o["ovi"] <= hi]
        if not char_ovis:
            raise SystemExit("--char-range selects no ovis")
    out = ROOT / "canon" / "drafts"
    out.mkdir(parents=True, exist_ok=True)
    stem = out / f"a{a.adhyay:02d}"
    receipt = {"adhyay": a.adhyay, "status": "DRAFT, not approved", "drafter": a.drafter, "checker": a.checker, "calls": []}
    meanings, chars = [], None

    if a.only != "characters":
        partial = stem.with_name(stem.name + "_meanings_PARTIAL.jsonl")      # rows of every finished batch; a restart resumes from it instead of redoing them
        done = load_done(partial, a, ovis)
        starts = [s for s in range(0, len(ovis), BATCH) if not all(o["ovi"] in done for o in ovis[s:s + BATCH])]
        meanings += [done[o["ovi"]] for s in range(0, len(ovis), BATCH) if s not in starts for o in ovis[s:s + BATCH]]
        print(f"resuming: {len(meanings)} meanings already done, {len(starts)} batches to run", flush=True)
        with ThreadPoolExecutor(max_workers=len(keys)) as ex:
            for fut in as_completed([ex.submit(run_batch, pool, keys, a, ovis, s) for s in starts]):
                rows, log = fut.result()
                meanings += rows
                receipt["calls"] += log
                with partial.open("a", encoding="utf-8") as f:
                    f.write(nt.scrub("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), keys))
        meanings.sort(key=lambda r: r["ovi"])
        save(stem.with_name(stem.name + "_meanings_DRAFT.jsonl"), "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in meanings), keys)

    if a.only == "characters":                                          # keep the meanings table in the review file
        meanings = sorted(load_done(stem.with_name(stem.name + "_meanings_PARTIAL.jsonl"), a, ovis).values(), key=lambda r: r["ovi"])

    if a.only != "meanings":
        reply, info = chat_stream(pool, keys, char_writer, CHAR_SYS, ovi_lines(char_ovis))
        receipt["calls"].append({"step": "characters", **info})
        try:
            chars = clean_chars(scrub_obj(extract_json(reply, dict), keys))
            chars["name_check"] = check_names(chars["characters"] + chars["places"], ovis)
            reply2, info2 = chat_stream(pool, keys, char_checker, CHARCHECK_SYS, ovi_lines(char_ovis) + "\n\nDRAFT LIST:\n" + json.dumps({k: chars[k] for k in ("characters", "places")}, ensure_ascii=False))
            receipt["calls"].append({"step": "characters_check", **info2})
            try:
                chars["model_check"] = clean_problems(scrub_obj(extract_json(reply2, dict), keys).get("problems"))
            except ValueError:
                chars["model_check"] = "NO_CHECK"
        except ValueError as exc:
            chars = {"error": f"reply was not the expected JSON ({exc})", "raw": nt.scrub(reply[:4000], keys)}
        chars["status"] = "DRAFT, not approved"
        chars["written_by"], chars["checked_by"] = char_writer, char_checker
        save(stem.with_name(stem.name + "_characters_places_DRAFT.json"), json.dumps(chars, indent=2, ensure_ascii=False), keys)

    save(stem.with_name(stem.name + "_receipt.json"), json.dumps(receipt, indent=2, ensure_ascii=False), keys)
    save(stem.with_name(stem.name + "_REVIEW.md"), review_text(a, ovis, meanings, chars), keys)
    failed = sum(1 for r in meanings if r.get("status") != "DRAFT")
    issues = sum(1 for r in meanings if r.get("check") == "ISSUE")
    print(f"meanings: {len(meanings)} rows, {failed} failed, {issues} flagged by the checker; characters: "
          + ("none" if chars is None else f"{len(chars.get('characters', []))} people, {len(chars.get('places', []))} places, {len(chars.get('name_check', []))} name-check problems"))
    return 1 if failed or (chars and "error" in chars) else 0


def review_text(a, ovis, meanings, chars) -> str:
    by = {r["ovi"]: r for r in meanings}
    cell = lambda s: str(s or "").replace("|", "/").replace("\n", " ")
    lines = [f"# Adhyay {a.adhyay}: DRAFT meanings and characters (owner review; nothing here is approved)", "",
             "Each row names the model that drafted it and the model that checked it. Marathi from hindujagruti.org (to be checked against the printed PDF).", ""]
    if meanings:
        lines += ["## Meanings", "", "| Ovi | Marathi | Draft meaning | Speaker | Check |", "|---|---|---|---|---|"]
        for o in ovis:
            r = by.get(o["ovi"], {})
            if r.get("status") != "DRAFT":
                status = f"**{r.get('status', 'MISSING')}**"
            elif r.get("check") == "ISSUE":
                status = f"**ISSUE:** {r.get('check_note')}"
            else:
                status = r.get("check", "NO_CHECK")
            if r.get("note"):
                status += f" (drafter note: {r['note']})"
            if r.get("drafter"):
                status += f" [drafted by {r['drafter'].split('/')[-1]}, checked by {r['checker'].split('/')[-1]}]"
            lines.append(f"| {o['ovi']} | {cell(o['text'])} | {cell(r.get('meaning_en'))}{' (unclear)' if r.get('unclear') else ''} | {cell(r.get('speaker'))} | {cell(status)} |")
    if chars:
        lines += ["", "## Characters and places", "", "```json", json.dumps(chars, indent=2, ensure_ascii=False), "```"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
