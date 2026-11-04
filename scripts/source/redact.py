"""One redaction for every artifact a run persists or archives (I55, Codex audit). Two things are replaced: the exact values of the keys a run
has loaded (from secrets/ or the environment), and the shapes of common credentials (an OpenRouter, Anthropic, Groq, GitHub or AWS key, a Bearer
token, an Authorization header, a private key block). scan() also names the kinds it found, so a caller can refuse a reply, or record that it
held a credential, before the text is written or archived. Nothing here reads a file: callers pass the key values they have loaded."""
import os
import re

REPLACEMENT = "[REDACTED]"
MIN_VALUE = 8                             # a loaded value shorter than this is not scrubbed (it would mangle ordinary text)
KEY_NAME = re.compile(r"(?i)(KEY|TOKEN|SECRET|PASSWORD)")
SHAPES = (
    ("openrouter-key", re.compile(r"sk-or-[A-Za-z0-9_\-]{6,}")),
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{6,}")),
    ("sk-key", re.compile(r"\bsk-[A-Za-z0-9_\-]{6,}")),
    ("groq-key", re.compile(r"\bgsk_[A-Za-z0-9]{10,}")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("bearer", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=\-]{6,}")),
    ("authorization", re.compile(r"(?i)authorization[\"']?\s*[:=]\s*[^\r\n,}\"']+")),
    ("private-key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
)


def values_of(*sources):
    """The exact strings to scrub, from any number of sources: a dict contributes its values (load_keys returns {slot: key}), a list or tuple
    its strings. Strings shorter than MIN_VALUE are left out; the rest are de-duplicated, longest first (so a longer key is scrubbed whole)."""
    out = set()
    for source in sources:
        items = source.values() if isinstance(source, dict) else (source or ())
        for item in items:
            if isinstance(item, str) and len(item.strip()) >= MIN_VALUE:
                out.add(item.strip())
    return sorted(out, key=len, reverse=True)


def env_key_values(environ=None):
    """The values of the environment variables whose names look like a key, token, secret or password."""
    env = os.environ if environ is None else environ
    return [value for name, value in env.items() if KEY_NAME.search(name) and isinstance(value, str)]


def scan(text, values=()):
    """(scrubbed text, kinds): every loaded value and every credential shape is replaced by [REDACTED]; `kinds` names what the text held before
    the replacement ("loaded-key" for a loaded value, else the shape's name), in the order found, without repeats."""
    text, kinds = str(text), []
    for value in values_of(values):
        if value in text:
            text = text.replace(value, REPLACEMENT)
            if "loaded-key" not in kinds:
                kinds.append("loaded-key")
    for name, pattern in SHAPES:
        if pattern.search(text):
            text = pattern.sub(REPLACEMENT, text)
            kinds.append(name)
    return text, kinds


def scrub(text, values=()):
    """The text with every loaded value and credential shape replaced (scan's first result)."""
    return scan(text, values)[0]
