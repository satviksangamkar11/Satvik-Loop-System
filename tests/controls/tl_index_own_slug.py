"""Negative control for tl_index (two-loop job tl-index): its own anchors (lower case, spaces to dashes), no numbering, no tl_slug import. The acceptance tests must fail on it."""


def index_entries(titles):
    out = []
    for title in titles:
        anchor = "-".join(title.lower().split()) or "section"
        out.append((anchor, title))
    return out
