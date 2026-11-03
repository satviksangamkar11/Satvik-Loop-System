"""Negative control for tl_slug (two-loop job tl-slug): slugify returns its input unchanged. The acceptance tests must fail on it."""


def slugify(text):
    return text
