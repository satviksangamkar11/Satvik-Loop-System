"""Negative control for fl_dedupe (four-loop job fl-dedupe): sorted-set. The acceptance tests must fail on it."""


def dedupe(items):
    return sorted(set(items))
