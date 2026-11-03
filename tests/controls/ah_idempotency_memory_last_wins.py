"""Negative control for api_idempotency (loop job ah-idempotency): memory-last-wins. The acceptance tests must fail on it."""


class IdempotencyStore:
    def __init__(self, folder, ttl_s=86400, clock=None):
        self.d = {}
    def get(self, key):
        return self.d.get(key)
    def put(self, key, response):
        self.d[key] = response
        return response
