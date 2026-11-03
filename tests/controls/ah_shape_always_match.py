"""Negative control for api_shape (loop job ah-shape): always-match. The acceptance tests must fail on it."""


def shape_diff(expected, actual, path='$'):
    return []
