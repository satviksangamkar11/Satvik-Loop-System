"""Negative control for ps_duration (prod-smoke job ps-duration): no zero padding and no checks. The acceptance tests must fail on it."""


def format_duration(seconds):
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}h {minutes}m {secs}s"
