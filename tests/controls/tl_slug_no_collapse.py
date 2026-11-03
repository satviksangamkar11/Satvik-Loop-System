"""Negative control for tl_slug (two-loop job tl-slug): every character outside a to z and 0 to 9 becomes one dash, with no collapsing and no trimming. The acceptance tests must fail on it."""
import re


def slugify(text):
    return re.sub(r"[^a-z0-9]", "-", text.lower())
