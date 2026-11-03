"""Negative control for fl_chunk (four-loop job fl-chunk): one-chunk. The acceptance tests must fail on it."""


def chunks(items, size):
    return [list(items)]
