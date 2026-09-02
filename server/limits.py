"""Rate limits, held in this process.

Per-instance and in-memory, which is the honest description: two Cloud Run
instances each allow their own share. That is fine for what these are for —
slowing a password-guesser down and stopping one tab from firing model calls
in a loop. The limit that actually protects the bill is the monthly token cap
in Postgres, which every instance reads from the same place.

Nothing here blocks: a caller over the line is refused, not queued.
"""
import threading
import time

BUCKETS = {}
LOCK = threading.Lock()
SWEEP_EVERY = 600
_last_sweep = [time.time()]


def _sweep(now):
    if now - _last_sweep[0] < SWEEP_EVERY:
        return
    _last_sweep[0] = now
    for key in [k for k, v in BUCKETS.items() if v and v[-1] < now - 3600]:
        BUCKETS.pop(key, None)


def hit(key, limit, window):
    """Record one attempt against `key`. Returns (allowed, retry_after).

    A sliding window of timestamps rather than a counter, because the failure
    mode of a fixed window is a burst of 2x the limit across its boundary —
    which is the moment a guesser gets lucky."""
    now = time.time()
    with LOCK:
        _sweep(now)
        seen = [t for t in BUCKETS.get(key, ()) if t > now - window]
        if len(seen) >= max(1, limit):
            BUCKETS[key] = seen
            return False, int(max(1, window - (now - seen[0])))
        seen.append(now)
        BUCKETS[key] = seen
        return True, 0


def forget(key):
    """Called after a success, so one bad password does not count against a
    person for the next fifteen minutes."""
    with LOCK:
        BUCKETS.pop(key, None)


def reset():
    with LOCK:
        BUCKETS.clear()
        _last_sweep[0] = time.time()


class Cache(object):
    """Tiny TTL cache. Used for the monthly-usage lookup, which would
    otherwise be a database round-trip on every model call."""

    def __init__(self, ttl=30):
        self.ttl = ttl
        self.data = {}
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            hit_ = self.data.get(key)
        if not hit_:
            return None
        when, value = hit_
        if time.time() - when > self.ttl:
            self.drop(key)
            return None
        return value

    def put(self, key, value):
        with self.lock:
            if len(self.data) > 5000:
                self.data.clear()
            self.data[key] = (time.time(), value)
        return value

    def drop(self, key):
        with self.lock:
            self.data.pop(key, None)

    def clear(self):
        with self.lock:
            self.data.clear()


# The caches the routers share. They live here rather than in api.py because
# admin.py has to be able to drop a stale entry too — an administrator who
# turns signups off should see them off, not sixty seconds later — and nothing
# in this package below api may import api.
PROFILE = Cache(ttl=20)         # role and status: re-read often enough to bite
USAGE = Cache(ttl=30)           # the monthly token total, which is a sum over rows
SETTINGS = Cache(ttl=60)        # app_settings, read on every page load
TOUCHED = Cache(ttl=900)        # who we have already written last_seen_at for
