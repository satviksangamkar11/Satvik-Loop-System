def clamp(x, lo, hi):
    if lo > hi:
        raise ValueError("lo must be <= hi")
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x
