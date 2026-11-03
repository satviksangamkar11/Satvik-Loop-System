"""Negative control for fl_top (four-loop job fl-top): own-count. The acceptance tests must fail on it."""


def top_words(text, k):
    counts = {}
    for w in text.split():
        counts[w] = counts.get(w, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])[:k]
