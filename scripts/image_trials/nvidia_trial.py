"""Try reference-based image models through the free NVIDIA API catalog from this PC (no GPU, no Kaggle). Personal prototyping only (owner decision D-012).

  python scripts/image_trials/nvidia_trial.py check-key
  python scripts/image_trials/nvidia_trial.py probe  --model flux2-klein-4b
  python scripts/image_trials/nvidia_trial.py run    --model flux2-klein-4b --scenes 1,2,3 --out D:/kaggle_upload/trial_klein

KEYS (never printed, never written to a receipt, never committed): the environment first, then the owner's key folder
  NVIDIA_API_KEY            one key, or
  NVIDIA_API_KEYS           several keys separated by commas, then
  every nvidia_api_key*.txt in the folder named by NVIDIA_KEYS_DIR (default D:/aai work/secrets/nvidia, outside any repository; one key per file, empty files skipped).
SPEED: all keys form one pool. The owner's limit is 10 requests per key per rolling 60 s: each request goes to the ready key with the fewest recent requests, scenes and variants run in
parallel (one worker per key, at most 8), a key that answers 429 cools down for 60 s while the others keep working, a key answering 401/402/403 (invalid or out of credit) is retired
for the run. The owner owns every account in the pool (decision D-012, personal prototyping).

What it does: sends the owner's reference images (assets/pilot_refs/set_v1, first = the person in image 1 ...) with a scene prompt built from the frozen style v1 prefix/suffix
(canon/style.yaml) plus a plain description of who stands where, saves each result as a PNG and writes receipt.json (model, endpoint, reference hashes, prompt, seed, output
hash, time). A PASS means an image came back: whether it looks right and keeps each person's identity is judged by the owner.

The request field names of the hosted endpoints could not be read from NVIDIA's script-rendered pages, so BODY_BUILDERS are best guesses; `probe` sends one real request and prints the
server's own answer (status and the first 600 characters of the body, with any key-like text removed) so the builder can be corrected.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time
import http.client
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from granthstudio.style import compose, load_style  # noqa: E402

REFS = ROOT / "assets" / "pilot_refs" / "set_v1"
HOST = "https://ai.api.nvidia.com/v1/genai"
KEY_PATTERN = re.compile(r"nvapi-[A-Za-z0-9_\-]{10,}")
KEY_SYNTAX = re.compile(r"nvapi-[A-Za-z0-9_\-]{20,}")
ALLOWED_PREFIXES = ("https://ai.api.nvidia.com/", "https://integrate.api.nvidia.com/")


# ------------------------------------------------------------------ keys

def keys_from_env(environ: dict | None = None) -> list[str]:
    """Keys in order; a value that is not exactly one well-formed key (stray spaces, newlines, extra text) is dropped, never sent and never echoed."""
    environ = os.environ if environ is None else environ
    found = [k.strip() for k in environ.get("NVIDIA_API_KEYS", "").split(",") if k.strip()]
    single = environ.get("NVIDIA_API_KEY", "").strip()
    candidates = ([single] if single else []) + [k for k in found if k != single]
    return [k for k in candidates if KEY_SYNTAX.fullmatch(k)]


DEFAULT_KEYS_DIR = Path("D:/aai work/secrets/nvidia")


def keys_from_folder(folder: Path | None = None, environ: dict | None = None) -> list[str]:
    """One key per nvidia_api_key*.txt file (sorted by name); an empty or malformed file is skipped without echoing its content."""
    environ = os.environ if environ is None else environ
    folder = Path(folder or environ.get("NVIDIA_KEYS_DIR") or DEFAULT_KEYS_DIR)
    if not folder.is_dir():
        return []
    found = []
    for path in sorted(folder.glob("nvidia_api_key*.txt"), key=lambda p: (len(p.stem), p.stem)):
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if KEY_SYNTAX.fullmatch(text):
            found.append(text)
    return found


def load_keys(environ: dict | None = None, folder: Path | None = None) -> list[str]:
    seen, keys = set(), []
    for key in keys_from_env(environ) + keys_from_folder(folder, environ):
        if key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def scrub(text: str, keys: list[str]) -> str:
    text = KEY_PATTERN.sub("***", text)
    for key in keys:
        text = text.replace(key, "***") if len(key) >= 8 else text
    return text


# ------------------------------------------------------------------ models (field names unverified: see the module docstring)

def data_uri(path: Path) -> str:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


# "Effort high where available": these image models have no effort switch; the equivalent is the sampling steps, guidance and the largest output size each endpoint accepts.
# FLUX.2 klein 4B is step-distilled (4 steps is its design point: more steps do not add quality); FLUX.1 Kontext dev gets 50 steps.
QUALITY = {"flux2-klein-4b": {"steps": 4, "note": "distilled: 4 steps is the maximum useful value", "size": [1152, 768]},
           "flux1-kontext-dev": {"steps": 50, "cfg_scale": 3.5, "note": "50 steps (the model default is 30)"}}


def body_klein(prompt: str, refs: list[Path], seed: int, size: tuple[int, int]) -> dict:
    return {"prompt": prompt, "image": [data_uri(p) for p in refs], "width": size[0], "height": size[1], "seed": seed, "steps": QUALITY["flux2-klein-4b"]["steps"]}


def body_kontext(prompt: str, refs: list[Path], seed: int, size: tuple[int, int]) -> dict:
    return {"prompt": prompt, "image": data_uri(refs[0]), "aspect_ratio": "match_input_image", "seed": seed, "steps": QUALITY["flux1-kontext-dev"]["steps"], "cfg_scale": QUALITY["flux1-kontext-dev"]["cfg_scale"]}


MODELS = {
    "flux2-klein-4b": {"url": f"{HOST}/black-forest-labs/flux.2-klein-4b", "builder": body_klein, "max_refs": 4, "size": (1152, 768), "quality": QUALITY["flux2-klein-4b"], "note": "multi-reference editing; up to 4 images"},
    "flux1-kontext-dev": {"url": f"{HOST}/black-forest-labs/flux.1-kontext-dev", "builder": body_kontext, "max_refs": 1, "size": (1024, 1024), "quality": QUALITY["flux1-kontext-dev"], "note": "single image only: baseline"},
}


def extract_image(response: dict) -> bytes | None:
    """The generated image from any of the response shapes the NVIDIA genai endpoints use."""
    candidates = []
    if isinstance(response.get("artifacts"), list) and response["artifacts"]:
        candidates.append(response["artifacts"][0].get("base64"))
    candidates += [response.get("image"), response.get("b64_json")]
    if isinstance(response.get("images"), list) and response["images"]:
        candidates.append(response["images"][0] if isinstance(response["images"][0], str) else response["images"][0].get("base64"))
    if isinstance(response.get("data"), list) and response["data"]:
        candidates.append(response["data"][0].get("b64_json"))
    for value in candidates:
        if isinstance(value, str) and len(value) > 100:
            try:
                return base64.b64decode(value.split(",", 1)[-1])
            except ValueError:
                return None
    return None


PNG_MAGIC = bytes([0x89]) + b"PNG" + bytes([13, 10, 26, 10])
JPEG_MAGIC = bytes([0xFF, 0xD8, 0xFF])


def looks_like_an_image_without_a_secret(data: bytes, keys: list[str]) -> bool:
    """PNG or JPEG magic bytes, and no key bytes anywhere inside (a response must never be able to write a secret into a saved file)."""
    if not (data[:8] == PNG_MAGIC or data[:3] == JPEG_MAGIC):
        return False
    if any(key.encode() in data for key in keys):
        return False
    return KEY_PATTERN.search(data.decode("latin-1")) is None


# ------------------------------------------------------------------ scenes (what the TEXT of Adhyay 1 says; the old SDXL keyframes had content gaps)

STYLE_CLAUSE = "Match the style, faces, builds and clothing of the reference images exactly."
SHORT_STYLE = ("1950s Indian devotional book illustration, opaque gouache on aged ivory paper, soft sepia contour lines, flat matte colours of raw sienna, "
               "burnt umber, ochre, terracotta and sage green, no text.")
PROMPT_LIMIT = 800                                        # the hosted endpoint rejects longer prompts (HTTP 422, 'at most 800 characters')
SCENES = {
    1: {"refs": ["maharaj.jpg", "location.jpg"],
        "text": ("Image 1 is the young man, image 2 is the village road and house. He sits cross-legged on the dusty road in front of the house, among scattered used leaf plates, "
                 "lifting a few grains of rice from a leaf plate toward his mouth, a small clay pipe in his other hand, calm face. Bright daylight.")},
    2: {"refs": ["maharaj.jpg", "bankatlal.jpg", "damodarpant.jpg", "location.jpg"],
        "text": ("Image 1 is the young man, image 2 the merchant in the turban, image 3 the clerk in the cap, image 4 the village road and house. "
                 "The young man sits cross-legged on the road among scattered leaf plates, eating a grain of rice. The merchant and the clerk stand on the road a few steps away, watching in amazement. "
                 "Bright daylight.")},
    3: {"refs": ["maharaj.jpg", "devidas.jpg", "bankatlal.jpg", "damodarpant.jpg", "location.jpg"],
        "text": ("Image 1 is the young man, image 2 the householder with the sacred thread, image 3 the merchant in the turban, image 4 the clerk in the cap, image 5 the house and road. "
                 "At the doorway the householder sets a large plate of sweets on the ground before the young man, who sits cross-legged and eats calmly. "
                 "The merchant and the clerk stand nearby, watching with respect. Bright daylight.")},
}


def scene_prompt(scene: int, limit: int = PROMPT_LIMIT) -> str:
    """The frozen style text + scene when it fits the endpoint's limit; otherwise a short style line + scene (the references carry the look). Never longer than `limit`."""
    body = f"{SCENES[scene]['text']} {STYLE_CLAUSE}"
    full = compose(load_style(_Project()), body)
    prompt = full if len(full) <= limit else f"{SHORT_STYLE} {body}"
    if len(prompt) > limit:
        raise SystemExit(f"scene {scene}: the prompt is {len(prompt)} characters, the endpoint allows {limit}")
    return prompt


class _Project:                                                        # load_style only needs the canon directory
    canon_dir = ROOT / "canon"


# ------------------------------------------------------------------ HTTP

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would forward the Authorization header to wherever it points: refuse them all (the answer becomes an HTTP 3xx error)."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _send(request: urllib.request.Request, timeout: int) -> tuple[int, str]:
    """(status, body). Never raises with a secret in the message: any transport or header problem becomes (0, 'request failed: <ExceptionType>')."""
    if not request.full_url.startswith(ALLOWED_PREFIXES):
        return 0, "request failed: URL outside the approved NVIDIA hosts"
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as exc:     # HTTPException covers BadStatusLine, which would carry the server's raw status line
        return 0, f"request failed: {type(exc).__name__}"


def http_post(url: str, key: str, body: dict, timeout: int = 180) -> tuple[int, str]:
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Authorization": f"Bearer {key}", "Accept": "application/json", "Content-Type": "application/json"})
    return _send(request, timeout)


REJECTED = (401, 402, 403)          # invalid or out-of-credit key: retired for the run
PER_KEY = 10                        # owner's limit: requests per key ...
WINDOW_S = 60.0                     # ... per rolling window; a 429 also cools the key for one window


class AllKeysUnavailable(RuntimeError):
    pass


class _LocalKeyPool:
    """Thread-safe rate-limited key pool. acquire() blocks (sleeping outside the lock) until some live key has fewer than PER_KEY requests in the last WINDOW_S seconds."""

    def __init__(self, keys: list[str], per_key: int = PER_KEY, window_s: float = WINDOW_S, clock=time.monotonic, sleep=time.sleep):
        import threading
        self.keys = list(keys)
        self.per_key, self.window_s, self.clock, self.sleep = per_key, window_s, clock, sleep
        self._stamps = [[] for _ in self.keys]
        self._dead = [False] * len(self.keys)
        self._cool = [0.0] * len(self.keys)
        self._lock = threading.Lock()

    def acquire(self) -> tuple[int, str]:
        while True:
            with self._lock:
                now = self.clock()
                live = [i for i in range(len(self.keys)) if not self._dead[i]]
                if not live:
                    raise AllKeysUnavailable("every key was rejected (invalid or out of credit)")
                for i in live:
                    self._stamps[i] = [t for t in self._stamps[i] if now - t < self.window_s]
                ready = [i for i in live if self._cool[i] <= now and len(self._stamps[i]) < self.per_key]
                if ready:
                    pick = min(ready, key=lambda i: (len(self._stamps[i]), self._stamps[i][-1] if self._stamps[i] else 0.0, i))
                    self._stamps[pick].append(now)
                    return pick, self.keys[pick]
                waits = [max(self._cool[i] - now, (self._stamps[i][0] + self.window_s - now) if len(self._stamps[i]) >= self.per_key else 0.0) for i in live]
                wait = max(min(waits), 0.05)
            self.sleep(wait)

    def report(self, index: int, status: int) -> None:
        with self._lock:
            if status in REJECTED:
                self._dead[index] = True
            elif not 200 <= status < 300:                      # owner rule (P1-D14): ANY failed request (429, 5xx, 0 = timeout or reset ...) cools the key one window
                self._cool[index] = self.clock() + self.window_s

    @property
    def live(self) -> int:
        return sum(1 for d in self._dead if not d)


def KeyPool(keys, *args, **kwargs):
    """Pool factory. Default: the in-process pool above. With env P1_LEDGER=1 (set by pieces/p1_claims.py run) every loop process shares ONE
    ledger (per-key request counts, cooldowns, dead-key markers) in P1_LEDGER_DIR (default <repo>/_loop), and P1_KEY_OFFSET spreads the start key (P1-D20)."""
    if os.environ.get("P1_LEDGER") != "1":
        return _LocalKeyPool(keys, *args, **kwargs)
    root = Path(__file__).resolve().parents[2]
    if str(root / "pieces") not in sys.path:
        sys.path.insert(0, str(root / "pieces"))
    import p1_claims as pc

    class _LedgerPool(pc.LedgerKeyPool):
        def acquire(self):
            try:
                return super().acquire()
            except pc.AllKeysUnavailable as exc:
                raise AllKeysUnavailable(str(exc)) from exc

    offset = os.environ.get("P1_KEY_OFFSET", "")
    return _LedgerPool(keys, os.environ.get("P1_LEDGER_DIR") or str(root / "_loop"), offset=int(offset) if offset.isdigit() else None)


def call(model: dict, body: dict, pool: KeyPool, post=http_post) -> tuple[int, dict | str, int]:
    """(status, parsed response or text, index of the key used). 401/402/403 retire that key and the request moves on; 429 cools that key and the request goes to another key
    (waiting for one if all are cooling); any other answer ends the request."""
    last: tuple[int, str, int] = (0, "no key", 0)
    for _ in range(3 * len(pool.keys) + 5):
        try:
            index, key = pool.acquire()
        except AllKeysUnavailable as exc:
            return last[0] or 401, str(exc), last[2]
        status, text = post(model["url"], key, body)
        pool.report(index, status)
        last = (status, text, index)
        if status in REJECTED or status == 429:
            continue
        try:
            return status, json.loads(text), index
        except json.JSONDecodeError:
            return status, text, index
    return last[0], last[1], last[2]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cmd_check_key(args, opener=None) -> int:
    raise SystemExit("DISABLED (P1-D35): no network command of this copy may run; the loop uses only the key pool, load_keys and scrub")
    keys = load_keys()
    if not keys:
        print("no usable key: put one nvapi-... key per file nvidia_api_key*.txt in the key folder (D:/aai work/secrets/nvidia), or set NVIDIA_API_KEY / NVIDIA_API_KEYS")
        return 2
    send = opener or (lambda request: _send(request, 30))
    print(f"{len(keys)} well-formed key(s) loaded (values are never shown)")
    accepted = 0
    for index, key in enumerate(keys):
        status, _ = send(urllib.request.Request("https://integrate.api.nvidia.com/v1/models", headers={"Authorization": f"Bearer {key}"}))
        if status in REJECTED:
            print(f"key {index + 1} of {len(keys)}: rejected (HTTP {status})" + ("; trying the next one" if not args.all else ""))
            continue
        print(f"key {index + 1} of {len(keys)}: " + ("accepted (HTTP 200)" if status == 200 else f"HTTP {status}" + (" (rate limit: stopping)" if status == 429 else "")))
        if status != 200:
            return 1                                                                              # 429 or a server error: stop, never move on
        accepted += 1
        if not args.all:
            return 0
    if not accepted:
        print("every key was rejected")
    return 0 if accepted else 1


def build(model_name: str, scene: int, seed: int):
    model = MODELS[model_name]
    refs = [REFS / name for name in SCENES[scene]["refs"]][: model["max_refs"]]
    missing = [p.name for p in refs if not p.is_file()]
    if missing:
        raise SystemExit(f"reference images missing in {REFS}: {missing}")
    prompt = scene_prompt(scene)
    return model, refs, prompt, model["builder"](prompt, refs, seed, model["size"])


def cmd_probe(args, post=http_post, pool: KeyPool | None = None) -> int:
    raise SystemExit("DISABLED (P1-D35): the image and vision commands of this copy must never run in Phase 1 (no vision or image-generation calls); only the key pool, load_keys and scrub are used by the loop")
    keys = load_keys()
    if not keys:
        print("no key: put the key files in D:/aai work/secrets/nvidia (see check-key) or set NVIDIA_API_KEY first")
        return 2
    pool = pool or KeyPool(keys)
    model, refs, prompt, body = build(args.model, 1, args.seed)
    status, response, _ = call(model, body, pool, post)
    shown = json.dumps(response)[:600] if isinstance(response, dict) else str(response)[:600]
    print(f"HTTP {status}\n{scrub(shown, keys)}\nrequest fields sent: {sorted(body)}")
    return 0 if status == 200 else 1


def cmd_run(args, post=http_post, pool: KeyPool | None = None) -> int:
    raise SystemExit("DISABLED (P1-D35): the image and vision commands of this copy must never run in Phase 1 (no vision or image-generation calls); only the key pool, load_keys and scrub are used by the loop")
    from concurrent.futures import ThreadPoolExecutor
    keys = load_keys()
    if not keys and not args.dry_run:
        print("no key: put the key files in D:/aai work/secrets/nvidia (see check-key) or set NVIDIA_API_KEY first")
        return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    model_spec = MODELS[args.model]
    receipt = {"schema_version": 1, "model": args.model, "endpoint": model_spec["url"], "schema_confidence": "unverified request field names",
               "quality_settings": model_spec["quality"], "prompt_note": "frozen style text is longer than the endpoint limit (800 characters): a short style line is sent and the reference images carry the look", "variants_per_scene": args.variants,
               "note": "PROTOTYPE via the NVIDIA API catalog (personal use, D-012); human review NOT_DONE", "scenes": []}
    jobs = [(scene, variant) for scene in [int(x) for x in args.scenes.split(",")] for variant in range(args.variants)]
    pool = pool or KeyPool(keys or ["nvapi-" + "0" * 20])
    started_all = time.monotonic()

    def work(job):
        scene, variant = job
        seed = args.seed + scene * 100 + variant
        model, refs, prompt, body = build(args.model, scene, seed)
        item = {"scene": scene, "variant": variant, "seed": seed, "prompt": prompt, "references": [{"file": p.name, "sha256": sha256(p.read_bytes())} for p in refs],
                "request_fields": sorted(body)}
        if args.dry_run:
            item["status"] = "DRY_RUN"
            return item
        started = time.monotonic()
        status, response, key_index = call(model, body, pool, post)
        item.update(status=status, wall_s=round(time.monotonic() - started, 1), key_number_used=key_index + 1)
        image = extract_image(response) if isinstance(response, dict) else None
        if status == 200 and image and looks_like_an_image_without_a_secret(image, keys):
            target = out / f"scene{scene}_v{variant}_{args.model}.png"
            target.write_bytes(image)
            item.update(output=target.name, output_sha256=sha256(image), verdict="IMAGE_RETURNED")
        else:
            item.update(verdict="FAILED", detail=scrub((json.dumps(response) if isinstance(response, dict) else str(response))[:600], keys))
        return item

    workers = 1 if args.dry_run else max(1, min(len(keys), 8, len(jobs)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        receipt["scenes"] = list(executor.map(work, jobs))
    receipt["wall_s_total"] = round(time.monotonic() - started_all, 1)
    for item in receipt["scenes"]:
        print(f"scene {item['scene']} v{item['variant']}: {item.get('verdict', item['status'])}")
    text = scrub(json.dumps(receipt, indent=2, ensure_ascii=False), keys)
    (out / "receipt.json").write_text(text, encoding="utf-8")
    return 0 if all(i.get("verdict", "IMAGE_RETURNED") == "IMAGE_RETURNED" for i in receipt["scenes"]) else 1


class _QuietParser(argparse.ArgumentParser):
    """argparse echoes unrecognised arguments (a pasted key would be printed): scrub every diagnostic before it is shown."""
    def error(self, message):
        sys.stderr.write(f"{self.prog}: error: {scrub(message, [])}\n")
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    parser = _QuietParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_QuietParser)
    check = sub.add_parser("check-key")
    check.add_argument("--all", action="store_true", help="test every key (HTTP status only) instead of stopping at the first good one")
    for name in ("probe", "run"):
        one = sub.add_parser(name)
        one.add_argument("--model", choices=sorted(MODELS), required=True)
        one.add_argument("--seed", type=int, default=20261024)
        if name == "run":
            one.add_argument("--scenes", default="1,2,3")
            one.add_argument("--variants", type=int, default=1, choices=range(1, 7), help="seeds per scene, generated in parallel across the key pool")
            one.add_argument("--out", required=True)
            one.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    return {"check-key": cmd_check_key, "probe": cmd_probe, "run": cmd_run}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
