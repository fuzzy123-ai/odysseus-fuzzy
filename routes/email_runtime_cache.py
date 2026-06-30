"""Runtime cache and IMAP pool helpers for email routes."""

import threading
import time


class EmailRuntimeCache:
    """Owns short-lived email route caches and the per-owner IMAP pool."""

    def __init__(
        self,
        imap_connect,
        *,
        list_ttl: float = 8.0,
        read_ttl: float = 30 * 60.0,
        imap_idle_max: float = 60.0,
        clock=time.monotonic,
    ):
        self._imap_connect = imap_connect
        self._clock = clock
        self.list_ttl = list_ttl
        self.read_ttl = read_ttl
        self.imap_idle_max = imap_idle_max
        self.list_cache = {}
        self.read_cache = {}
        self.imap_pool = {}
        self.warming_reads = set()
        self.pool_lock = threading.Lock()

    def pooled_connect(self, account_id, owner=""):
        """Reuse a pooled IMAP connection for the same account and owner."""
        pool_key = (account_id, owner)
        now = self._clock()
        with self.pool_lock:
            entry = self.imap_pool.get(pool_key)
            if entry:
                conn, last_used = entry
                if (now - last_used) < self.imap_idle_max:
                    try:
                        conn.noop()
                        del self.imap_pool[pool_key]
                        return conn, True
                    except Exception:
                        try:
                            conn.logout()
                        except Exception:
                            pass
                        del self.imap_pool[pool_key]
                else:
                    try:
                        conn.logout()
                    except Exception:
                        pass
                    del self.imap_pool[pool_key]
        return self._imap_connect(account_id, owner=owner), False

    def pooled_release(self, account_id, conn, ok=True, owner=""):
        if not ok:
            try:
                conn.logout()
            except Exception:
                pass
            return
        with self.pool_lock:
            self.imap_pool[(account_id, owner)] = (conn, self._clock())

    @staticmethod
    def list_cache_key(account_id, folder, filter_, limit, offset, from_addr=""):
        return (account_id or "", folder, filter_, int(limit), int(offset), from_addr or "")

    @staticmethod
    def read_cache_key(account_id, folder, uid, owner=""):
        # Owner is part of the key so default-account reads never cross users.
        return (account_id or "", folder, str(uid), owner)

    def list_cache_get(self, key):
        value = self.list_cache.get(key)
        if not value:
            return None
        if value[0] < self._clock():
            self.list_cache.pop(key, None)
            return None
        return value[1]

    def list_cache_put(self, key, value):
        self.list_cache[key] = (self._clock() + self.list_ttl, value)
        if len(self.list_cache) > 64:
            for old_key in list(self.list_cache.keys())[:-32]:
                self.list_cache.pop(old_key, None)

    def invalidate_list_cache(self, account_id=None, folder=None):
        """Drop stale list cache entries after a flag or folder mutation."""
        if account_id is None and folder is None:
            self.list_cache.clear()
            return
        for key in list(self.list_cache.keys()):
            key_account = key[0] if len(key) > 0 else ""
            key_folder = key[1] if len(key) > 1 else ""
            if (account_id is None or key_account == (account_id or "")) and (
                folder is None or key_folder == folder
            ):
                self.list_cache.pop(key, None)

    def read_cache_get(self, key):
        value = self.read_cache.get(key)
        if not value:
            return None
        if value[0] < self._clock():
            self.read_cache.pop(key, None)
            return None
        return value[1]

    def read_cache_put(self, key, value):
        self.read_cache[key] = (self._clock() + self.read_ttl, value)
        if len(self.read_cache) > 256:
            for old_key in list(self.read_cache.keys())[:-128]:
                self.read_cache.pop(old_key, None)

    def router_pool_exports(self):
        return {
            "connect": self.pooled_connect,
            "release": self.pooled_release,
            "list_cache_get": self.list_cache_get,
            "list_cache_put": self.list_cache_put,
            "list_cache_key": self.list_cache_key,
            "read_cache_get": self.read_cache_get,
            "read_cache_put": self.read_cache_put,
            "read_cache_key": self.read_cache_key,
        }
