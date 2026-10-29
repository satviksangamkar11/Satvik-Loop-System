from __future__ import annotations

import sys
import importlib.util
import os
import json
from pathlib import Path
from argparse import ArgumentParser
from typing import Callable, Any, List, Dict, Optional


RECEIPTS: List[Dict[str, Any]] = []


def profile_extra(model: str, thinking: str, effort: str) -> dict:
    if not model.startswith("nvidia/nemotron"):
        return {}
    extra = {"chat_template_kwargs": {"enable_thinking": thinking == "on"}}
    if effort in ("low", "medium", "high"):
        extra["reasoning_effort"] = effort
    return extra


def apply_profile(body: dict, thinking: str, effort: str) -> dict:
    model = body.get("model", "")
    extra = profile_extra(model, thinking, effort)
    if not extra:
        return body.copy()
    protected = {"model", "messages", "stream", "max_tokens", "temperature"}
    new_body = body.copy()
    for k, v in extra.items():
        if k not in protected:
            new_body[k] = v
    return new_body


def with_idle_timeout(ds: Any, seconds: Optional[int] = None) -> Any:
    explicit = seconds is not None
    if seconds is None:
        val = os.environ.get("CODER_IDLE_S")
        try:
            seconds = int(val) if val is not None else 600
        except ValueError:
            seconds = 600

    if not hasattr(ds, "nt") or not hasattr(ds.nt, "_OPENER"):
        return ds

    original_opener = ds.nt._OPENER
    if getattr(original_opener, "_coder_idle_proxied", False):
        current_explicit = getattr(original_opener, "_coder_idle_explicit", False)
        if explicit and current_explicit:
            return ds
        original_opener._timeout = seconds
        original_opener._coder_idle_explicit = explicit
        return ds

    class ProxyOpener:
        def __init__(self, opener: Any, timeout: int, explicit: bool):
            self._opener = opener
            self._timeout = timeout
            self._coder_idle_proxied = True
            self._coder_idle_explicit = explicit

        def open(self, req: Any, *args: Any, **kwargs: Any) -> Any:
            kwargs["timeout"] = self._timeout
            return self._opener.open(req, *args[:1], **kwargs)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._opener, name)

    ds.nt._OPENER = ProxyOpener(original_opener, seconds, explicit)
    return ds


def _sanitize_receipt(receipt: Dict[str, Any], keys: List[str]) -> Dict[str, Any]:
    """Replace occurrences of keys (len >= 8) in all string values of receipt with [key]."""
    if not isinstance(keys, list):
        return receipt
    valid_keys = [k for k in keys if isinstance(k, str) and len(k) >= 8]
    if not valid_keys:
        return receipt

    def sanitize_value(value: Any) -> Any:
        if isinstance(value, str):
            for key in valid_keys:
                value = value.replace(key, "[key]")
            return value
        elif isinstance(value, dict):
            return {k: sanitize_value(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [sanitize_value(v) for v in value]
        else:
            return value

    return sanitize_value(receipt)


def wrap_ds(original_ds: Callable[[], Any], thinking: str, effort: str) -> Callable[[], Any]:
    def ds() -> Any:
        mod = original_ds()

        # Keep existing behaviour: apply profile to _stream_once
        orig_stream_once = mod._stream_once
        mod._stream_once = lambda key, body: orig_stream_once(key, apply_profile(body, thinking, effort))

        # Apply idle timeout (safe: no-op if nt/_OPENER missing)
        mod = with_idle_timeout(mod)

        # Wrap chat_stream for retries and receipts ONLY if it exists
        if hasattr(mod, "chat_stream"):
            orig_chat_stream = mod.chat_stream

            def wrapped_chat_stream(
                pool: Any,
                keys: Any,
                model: str,
                system: str,
                user: str,
                max_tokens: int = 40000,
            ) -> tuple[str, dict]:
                global RECEIPTS
                attempts = 0
                current_max_tokens = max_tokens
                last_reply = ""
                last_info: dict = {}

                while True:
                    attempts += 1
                    reply, info = orig_chat_stream(pool, keys, model, system, user, current_max_tokens)

                    receipt = {
                        "model": model,
                        "attempt": attempts,
                        "max_tokens": current_max_tokens,
                        "status": info.get("status"),
                        "wall_s": info.get("wall_s"),
                        "finish": info.get("finish"),
                        "usage": info.get("usage"),
                        "error": info.get("error"),
                        "thinking": thinking,
                        "effort": effort,
                    }
                    # Sanitize receipt before storing
                    receipt = _sanitize_receipt(receipt, keys)
                    RECEIPTS.append(receipt)

                    if reply == "" and info.get("finish") == "length" and attempts < 3:
                        next_max_tokens = current_max_tokens * 2
                        if next_max_tokens > 128000:
                            last_reply, last_info = reply, info
                            break
                        current_max_tokens = next_max_tokens
                        continue
                    else:
                        last_reply = reply
                        last_info = info
                        break

                last_info["attempts"] = attempts
                return last_reply, last_info

            mod.chat_stream = wrapped_chat_stream

        return mod

    return ds


def split_args(argv: list[str] | None = None) -> tuple[str, str, list[str]]:
    parser = ArgumentParser(add_help=False)
    parser.add_argument("--thinking", choices=("on", "off"), default="on")
    parser.add_argument("--effort", choices=("low", "medium", "high", "none"), default="medium")
    if argv is None:
        argv = sys.argv[1:]
    args, rest = parser.parse_known_args(argv)
    return args.thinking, args.effort, rest


def find_out_arg(args: list[str]) -> str | None:
    i = 0
    while i < len(args):
        if args[i] == "--out":
            if i + 1 < len(args):
                return args[i + 1]
            return None
        elif args[i].startswith("--out="):
            return args[i][6:]
        i += 1
    return None


def write_receipt(out_dir: Path | str, receipts: list[dict]) -> Path | None:
    out_path = Path(out_dir)
    if not out_path.exists() or not out_path.is_dir():
        return None
    receipt_file = out_path / "RECEIPT.json"
    try:
        with open(receipt_file, "w", encoding="utf-8") as f:
            json.dump(receipts, f, indent=1, sort_keys=True)
        return receipt_file
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    global RECEIPTS
    RECEIPTS = []

    thinking, effort, rest = split_args(argv)
    print(f"coder profile: thinking={thinking} effort={effort}")

    root = Path(__file__).resolve().parents[2]
    nc_path = root / "scripts" / "source" / "nemotron_code.py"
    spec = importlib.util.spec_from_file_location("nemotron_code_wrapped", nc_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {nc_path}")
    nc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nc)

    nc._ds = wrap_ds(nc._ds, thinking, effort)

    sys.argv = ["nemotron_code"] + rest
    ret = nc.main()

    out_dir = find_out_arg(rest)
    if out_dir:
        write_receipt(out_dir, RECEIPTS)

    return ret


if __name__ == "__main__":
    sys.exit(main())
