import fl_wordcount

def top_words(text, k):
    if k < 0:
        raise ValueError("k must be non-negative")
    counts = fl_wordcount.word_counts(text)
    # sort by count descending, then word ascending
    sorted_items = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    if k == 0:
        return []
    return sorted_items[:k]
