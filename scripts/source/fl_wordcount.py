import re


def word_counts(text):
    """Return a dict mapping each word to its count in *text*.

    A word is a maximal run of the characters a-z and 0-9 after the text is
    lower-cased; every other character separates words.
    """
    words = re.findall(r'[a-z0-9]+', text.lower())
    counts = {}
    for word in words:
        counts[word] = counts.get(word, 0) + 1
    return counts
