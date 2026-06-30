from routes.email_runtime_cache import EmailRuntimeCache


class _Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


class _Conn:
    def __init__(self, fail_noop=False):
        self.fail_noop = fail_noop
        self.noop_calls = 0
        self.logout_calls = 0

    def noop(self):
        self.noop_calls += 1
        if self.fail_noop:
            raise RuntimeError("noop failed")

    def logout(self):
        self.logout_calls += 1


def test_cache_keys_keep_default_account_reads_owner_scoped():
    assert EmailRuntimeCache.list_cache_key(None, "INBOX", "all", 25, 0, "") == (
        "",
        "INBOX",
        "all",
        25,
        0,
        "",
    )
    assert EmailRuntimeCache.read_cache_key(None, "INBOX", "42", owner="alice") != EmailRuntimeCache.read_cache_key(
        None,
        "INBOX",
        "42",
        owner="bob",
    )


def test_list_and_read_cache_expire_by_ttl():
    clock = _Clock()
    cache = EmailRuntimeCache(lambda account_id, owner="": _Conn(), list_ttl=5.0, read_ttl=10.0, clock=clock)
    list_key = cache.list_cache_key("acct", "INBOX", "all", 10, 0)
    read_key = cache.read_cache_key("acct", "INBOX", "1", owner="alice")

    cache.list_cache_put(list_key, {"emails": [1]})
    cache.read_cache_put(read_key, {"body": "hello"})
    assert cache.list_cache_get(list_key) == {"emails": [1]}
    assert cache.read_cache_get(read_key) == {"body": "hello"}

    clock.value += 6.0
    assert cache.list_cache_get(list_key) is None
    assert cache.read_cache_get(read_key) == {"body": "hello"}

    clock.value += 5.0
    assert cache.read_cache_get(read_key) is None


def test_invalidate_list_cache_filters_by_account_and_folder():
    cache = EmailRuntimeCache(lambda account_id, owner="": _Conn())
    keep = cache.list_cache_key("acct-a", "Sent", "all", 10, 0)
    drop = cache.list_cache_key("acct-a", "INBOX", "all", 10, 0)
    other = cache.list_cache_key("acct-b", "INBOX", "all", 10, 0)
    cache.list_cache_put(keep, 1)
    cache.list_cache_put(drop, 2)
    cache.list_cache_put(other, 3)

    cache.invalidate_list_cache(account_id="acct-a", folder="INBOX")

    assert cache.list_cache_get(keep) == 1
    assert cache.list_cache_get(drop) is None
    assert cache.list_cache_get(other) == 3


def test_pooled_connect_reuses_same_owner_connection_and_removes_while_checked_out():
    clock = _Clock()
    created = []

    def _connect(account_id, owner=""):
        conn = _Conn()
        created.append((account_id, owner, conn))
        return conn

    cache = EmailRuntimeCache(_connect, clock=clock)
    first, reused = cache.pooled_connect(None, owner="alice")
    assert reused is False
    cache.pooled_release(None, first, owner="alice")

    second, reused = cache.pooled_connect(None, owner="alice")
    assert reused is True
    assert second is first
    assert first.noop_calls == 1
    assert cache.imap_pool == {}

    third, reused = cache.pooled_connect(None, owner="bob")
    assert reused is False
    assert third is not first
    assert created[1][1] == "bob"


def test_pooled_connect_discards_stale_or_broken_connections():
    clock = _Clock()
    fresh = _Conn()
    broken = _Conn(fail_noop=True)
    created = [fresh]

    cache = EmailRuntimeCache(lambda account_id, owner="": created.pop(0), imap_idle_max=1.0, clock=clock)
    cache.pooled_release("acct", broken, owner="alice")
    conn, reused = cache.pooled_connect("acct", owner="alice")
    assert reused is False
    assert conn is fresh
    assert broken.logout_calls == 1

    stale = _Conn()
    cache.pooled_release("acct", stale, owner="alice")
    replacement = _Conn()
    created.append(replacement)
    clock.value += 2.0
    conn, reused = cache.pooled_connect("acct", owner="alice")
    assert reused is False
    assert conn is replacement
    assert stale.logout_calls == 1
