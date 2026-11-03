"""Negative control for tl_roman (two-loop job tl-roman): writes every value with I only and accepts any number. The acceptance tests must fail on it."""


def to_roman(n):
    return "I" * n
