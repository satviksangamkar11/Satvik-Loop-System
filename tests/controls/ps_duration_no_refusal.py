"""Negative control for ps_duration (prod-smoke job ps-duration): correct padding, but no refusals (negatives and non-whole numbers are accepted). The acceptance tests must fail on it."""


def format_duration(seconds):
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}h {minutes:02d}m {secs:02d}s"
