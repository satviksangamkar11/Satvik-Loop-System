"""Negative control for fl_clamp (four-loop job fl-clamp): identity. The acceptance tests must fail on it."""


def clamp(x, lo, hi):
    return x
