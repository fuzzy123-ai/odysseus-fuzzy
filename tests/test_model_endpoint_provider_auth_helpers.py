from types import SimpleNamespace

from routes.model_endpoint_helpers import _delete_orphaned_provider_auth


class _Predicate:
    def __init__(self, check):
        self._check = check

    def __call__(self, row):
        return self._check(row)


class _Column:
    def __init__(self, table, name):
        self.table = table
        self.name = name

    def __eq__(self, value):
        return _Predicate(lambda row: getattr(row, self.name, None) == value)

    def __ne__(self, value):
        return _Predicate(lambda row: getattr(row, self.name, None) != value)


class _ProviderAuthTestEndpoint:
    id = _Column("endpoint", "id")
    provider_auth_id = _Column("endpoint", "provider_auth_id")


class _ProviderAuthTestSession:
    id = _Column("auth", "id")


class _ProviderAuthQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *predicates):
        self._rows = [
            row for row in self._rows
            if all(predicate(row) for predicate in predicates)
        ]
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _ProviderAuthDb:
    def __init__(self, endpoints=None, auth_rows=None):
        self.endpoints = list(endpoints or [])
        self.auth_rows = list(auth_rows or [])
        self.deleted = []

    def query(self, model):
        if isinstance(model, _Column) and model.table == "endpoint":
            return _ProviderAuthQuery(self.endpoints)
        if model is _ProviderAuthTestSession:
            return _ProviderAuthQuery(self.auth_rows)
        raise AssertionError(f"unexpected query model: {model!r}")

    def delete(self, row):
        self.deleted.append(row)


def test_delete_orphaned_provider_auth_skips_referenced_row():
    auth = SimpleNamespace(id="auth-1")
    db = _ProviderAuthDb(
        endpoints=[SimpleNamespace(id="ep-2", provider_auth_id="auth-1")],
        auth_rows=[auth],
    )

    result = _delete_orphaned_provider_auth(
        db,
        "auth-1",
        exclude_ep_id="ep-1",
        model_endpoint_model=_ProviderAuthTestEndpoint,
        provider_auth_model=_ProviderAuthTestSession,
    )

    assert result is False
    assert db.deleted == []


def test_delete_orphaned_provider_auth_deletes_unreferenced_row():
    auth = SimpleNamespace(id="auth-1")
    db = _ProviderAuthDb(
        endpoints=[SimpleNamespace(id="ep-1", provider_auth_id="auth-1")],
        auth_rows=[auth],
    )

    result = _delete_orphaned_provider_auth(
        db,
        "auth-1",
        exclude_ep_id="ep-1",
        model_endpoint_model=_ProviderAuthTestEndpoint,
        provider_auth_model=_ProviderAuthTestSession,
    )

    assert result is True
    assert db.deleted == [auth]


def test_delete_orphaned_provider_auth_handles_missing_auth_row():
    db = _ProviderAuthDb()

    result = _delete_orphaned_provider_auth(
        db,
        "missing",
        model_endpoint_model=_ProviderAuthTestEndpoint,
        provider_auth_model=_ProviderAuthTestSession,
    )

    assert result is False
    assert db.deleted == []
