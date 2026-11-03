"""Negative control for fl_wordcount (four-loop job fl-wordcount): split-once. The acceptance tests must fail on it."""


def word_counts(text):
    return {w: 1 for w in text.split()}
