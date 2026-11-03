def chunks(items, size):
    """Split a list into chunks of the given size."""
    if size < 1:
        raise ValueError("size must be >= 1")
    return [items[i:i+size] for i in range(0, len(items), size)]
