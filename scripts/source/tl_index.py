from tl_slug import slugify


def index_entries(titles):
    result = []
    counts = {}
    for title in titles:
        base = slugify(title)
        if base == "":
            base = "section"
        if base in counts:
            counts[base] += 1
            anchor = f"{base}-{counts[base]}"
        else:
            counts[base] = 1
            anchor = base
        result.append((anchor, title))
    return result
