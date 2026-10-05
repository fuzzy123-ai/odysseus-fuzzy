"""
Authentication module — multi-user password hashing, session tokens, config persistence.
Config stored in data/auth.json. Uses bcrypt directly.
"""

import enum
import copy
import json
import math
import os
import re
import secrets
import threading
import time
import weakref
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, Any, Iterator, List

import bcrypt
import pyotp

logger = logging.getLogger(__name__)


from core.atomic_io import atomic_write_json as _atomic_write_json  # noqa: E402
from core.middleware import INTERNAL_TOOL_USER  # noqa: E402

DEFAULT_PRIVILEGES = {
    "can_use_agent": True,
    "can_use_browser": True,
    "can_use_bash": False,
    "can_use_documents": True,
    "can_use_research": True,
    "can_generate_images": True,
    "can_manage_memory": True,
    "max_messages_per_day": 0,
    "allowed_models": [],
    "allowed_models_restricted": False,
    # Explicit "block every model" sentinel. An empty `allowed_models` list is
    # ambiguous — it's also what gets sent when the admin clicks "[All]" — so
    # we need a dedicated flag to express "this user may use no models at all"
    # distinctly from "this user has no restriction".
    "block_all_models": False,
}

# Admins get everything
ADMIN_PRIVILEGES = {k: (True if isinstance(v, bool) else (0 if isinstance(v, int) else [])) for k, v in DEFAULT_PRIVILEGES.items()}
ADMIN_PRIVILEGES["allowed_models_restricted"] = False
# Admins must never be blocked from using models — the generic dict
# comprehension above flips every boolean default to True, which would be
# backwards for this sentinel.
ADMIN_PRIVILEGES["block_all_models"] = False

from src.constants import AUTH_FILE, PASSWORD_MIN_LENGTH
DEFAULT_AUTH_PATH = AUTH_FILE
TOKEN_TTL = 60 * 60 * 24 * 7  # 7 days

# Usernames the auth + middleware layer reserve as internal "synthetic owner"
# sentinels; they must never belong to a real account. The most dangerous is
# "internal-tool": `core.middleware.require_admin` treats any request whose
# `current_user == "internal-tool"` as the in-process tool loopback and grants
# admin, and because the cookie auth path sets `current_user` to the raw
# username, an account literally named "internal-tool" would be silently
# treated as an admin by every `require_admin`-gated route. "api" collides with
# the bearer-token owner-attribution sentinel. "demo"/"system" round out the
# synthetic-owner set the rest of the codebase already special-cases (see
# `_SYNTHETIC_OWNERS` in routes/assistant_routes.py and the matching guards in
# src/task_scheduler.py / routes/research_routes.py) — a real account with one
# of those names would be denied an assistant and inconsistently owner-scoped.
# Refuse to create or rename into any of them so the sentinels can't be
# impersonated. (Keep this in sync with that synthetic-owner set.)
RESERVED_USERNAMES = frozenset({INTERNAL_TOOL_USER, "api", "demo", "system"})
SUBJECT_ID_PATTERN = re.compile(r"^owner_[a-z0-9]{32}$")
# core.atomic_io uses a process-ID based temporary suffix.  Multiple manager
# instances in this process can otherwise target the same auth/sessions path
# concurrently and collide at rename.  The authority lock is instance-local,
# so file serialization has to live at module scope.
_AUTH_FILE_WRITE_LOCK = threading.RLock()
# Auth config publication and identity migration deliberately use the same
# process-wide lock.  ``atomic_write_json`` uses a PID-derived temporary name,
# so a separate migration lock would still allow a normal config writer to
# collide with a startup migration in this process.
_AUTH_IDENTITY_MIGRATION_LOCK = _AUTH_FILE_WRITE_LOCK


class AuthSubjectIdentityError(RuntimeError):
    """The durable account-to-owner identity mapping is unsafe to use."""


class AuthSessionPersistenceError(RuntimeError):
    """A session invalidation is denied in memory but was not durable."""


@dataclass(frozen=True)
class LoginIdentityFence:
    subject_id: str
    security_revision: int
    # These values are deliberately not constructor arguments.  A fence is a
    # manager-issued, single-use capability, not a value callers can recreate
    # from a subject ID and revision they happen to know.
    _issuer: object | None = field(default=None, init=False, repr=False, compare=False)
    _nonce: str | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class _IssuedLoginFence:
    """Private, immutable issuance facts for one in-memory fence object."""

    fence_ref: weakref.ReferenceType[LoginIdentityFence]
    subject_id: str
    security_revision: int
    issuer: object
    nonce: str


def _new_subject_id() -> str:
    return "owner_" + secrets.token_hex(16)


def normalize_known_username(users: Dict[str, Any], username: str | None) -> Optional[str]:
    """Return a normalized username only when it exists in the auth user map."""
    key = str(username or "").strip().lower()
    if not key or key not in users:
        return None
    return key


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


class SetAdminResult(enum.Enum):
    """Outcome of AuthManager.set_admin, so callers can map each case to a
    precise response instead of guessing from a bare bool."""
    OK = "ok"
    USER_NOT_FOUND = "user_not_found"
    NOT_AUTHORIZED = "not_authorized"   # requester is not an admin
    LAST_ADMIN = "last_admin"           # would remove the last remaining admin


class AuthManager:
    """Manages multi-user password + session-token auth system."""

    def __init__(self, auth_path: str = DEFAULT_AUTH_PATH):
        self.auth_path = auth_path
        self._sessions_path = os.path.join(os.path.dirname(auth_path), "sessions.json")
        self._config: Dict[str, Any] = {}
        self._sessions: Dict[str, Dict[str, Any]] = {}  # token -> {username, expiry}
        # Guards mutations of self._sessions and the on-disk sessions.json.
        # Validate/create/revoke run concurrently from the FastAPI threadpool.
        self._sessions_lock = threading.RLock()
        # Serializes every identity/session mutation with a guarded durable
        # operation.  Keep this outermost: a guard deliberately retains only
        # this lock while a caller performs database work.
        self._session_authority_lock = threading.RLock()
        self._fence_issuer = object()
        # Weak ownership prevents abandoned failed-login fences from becoming
        # an unbounded process-memory registry.  Identity remains exact: a
        # copied object has another id and is never registered.
        # The public frozen dataclass can still be mutated with
        # ``object.__setattr__``.  Bind its identity to a separate immutable
        # issuance projection, keyed by a weak reference, so a modified leaf
        # cannot be repurposed for another account before token creation.
        self._issued_login_fences: Dict[int, _IssuedLoginFence] = {}
        # Monotonic in-process authority revision for ephemeral privileged
        # action grants.  No grant survives an admin-role or active-TOTP
        # security change, and this counter itself contains no credential data.
        self._security_action_revision = 0
        self._security_action_revision_lock = threading.Lock()
        # Guards all mutations of self._config and the on-disk auth.json so
        # concurrent create/delete/rename/privilege operations don't interleave
        # and corrupt the user database.
        self._config_lock = threading.Lock()
        # Guards the first-run setup check-and-write so concurrent requests
        # cannot both observe is_configured==False and both create admin accounts.
        self._setup_lock = threading.Lock()
        self._load_startup_config()
        self._load_sessions()

    @staticmethod
    def _invalid_registry() -> AuthSubjectIdentityError:
        """Return the one safe startup failure visible to callers."""
        return AuthSubjectIdentityError("invalid account identity registry")

    def _read_auth_config_locked(self) -> Dict[str, Any]:
        """Read one authoritative auth.json snapshot while FILE is retained."""
        try:
            if not os.path.exists(self.auth_path):
                return {}
            with open(self.auth_path, "r", encoding="utf-8") as f:
                value = json.load(f)
        except Exception:
            raise self._invalid_registry() from None
        if type(value) is not dict:
            raise self._invalid_registry()
        return value

    def _normalized_startup_candidate(self, durable: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize every legacy auth shape in memory before one publication."""
        if "password_hash" in durable and "users" not in durable:
            username = str(durable.get("username", "admin") or "admin").strip().lower()
            if not username or username in RESERVED_USERNAMES:
                username = "admin"
            password_hash = durable.get("password_hash")
            if not isinstance(password_hash, str) or not password_hash:
                raise self._invalid_registry()
            return {"users": {username: {
                "password_hash": password_hash,
                "created": time.time(),
                "is_admin": True,
                "subject_id": _new_subject_id(),
            }}}

        candidate = copy.deepcopy(durable)
        users = candidate.get("users")
        if users is None:
            return candidate
        if type(users) is not dict:
            raise self._invalid_registry()

        normalized: Dict[str, Any] = {}
        seen: set[str] = set()
        for raw_username, raw_row in users.items():
            if not isinstance(raw_username, str) or type(raw_row) is not dict:
                raise self._invalid_registry()
            username = raw_username.strip().lower()
            if not username or username in RESERVED_USERNAMES:
                continue
            if username in normalized:
                raise self._invalid_registry()
            row = copy.deepcopy(raw_row)
            subject_id = row.get("subject_id")
            if subject_id is None:
                subject_id = _new_subject_id()
                while subject_id in seen:
                    subject_id = _new_subject_id()
                row["subject_id"] = subject_id
            if not isinstance(subject_id, str) or SUBJECT_ID_PATTERN.fullmatch(subject_id) is None or subject_id in seen:
                raise self._invalid_registry()
            seen.add(subject_id)
            if row.get("role") == "admin" and "is_admin" not in row:
                row["is_admin"] = True
            normalized[username] = row
        candidate["users"] = normalized
        return candidate

    def _load_startup_config(self) -> None:
        """Publish a fully-normalized startup config at most once.

        This constructor-only transaction has one lock order (CONFIG then
        process-wide FILE).  ``self._config`` remains empty until an optional
        replace has completed, so another constructor never observes a
        half-migrated SID or reserved-account registry.
        """
        with self._config_lock:
            with _AUTH_FILE_WRITE_LOCK:
                durable = self._read_auth_config_locked()
                candidate = self._normalized_startup_candidate(durable)
                if candidate != durable:
                    try:
                        _atomic_write_json(self.auth_path, candidate, indent=2)
                    except Exception:
                        raise self._invalid_registry() from None
                self._config = candidate

    def _load(self):
        """Compatibility reload used by narrow tests and explicit maintenance."""
        self._load_startup_config()

    def _load_sessions(self):
        """Load persisted session tokens, releasing FILE before any save path."""
        try:
            with _AUTH_FILE_WRITE_LOCK:
                if not os.path.exists(self._sessions_path):
                    return
                with open(self._sessions_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            if type(data) is not dict:
                raise ValueError("invalid session registry")
            now = time.time()
            def _loadable(value: Any) -> bool:
                expiry = value.get("expiry") if type(value) is dict else None
                return type(expiry) in (int, float) and math.isfinite(expiry) and expiry > now
            loaded = {k: copy.deepcopy(v) for k, v in data.items() if _loadable(v)}
            with self._sessions_lock:
                self._sessions = loaded
            if len(data) != len(loaded):
                self._save_sessions()
            logger.info("Loaded %d session(s) from disk", len(loaded))
        except Exception:
            logger.error("Failed to load session registry")
            with self._sessions_lock:
                self._sessions = {}

    def _save_sessions(self):
        """Persist one authority-serialized snapshot of the session map."""
        try:
            with self._session_authority_lock:
                with self._sessions_lock:
                    snapshot = copy.deepcopy(self._sessions)
                with _AUTH_FILE_WRITE_LOCK:
                    _atomic_write_json(self._sessions_path, snapshot)
            return True
        except Exception:
            # Never expose writer/provider detail through logs or callers.
            logger.error("Failed to persist session snapshot")
            return False

    # Retain narrowly-used legacy entry points without reintroducing staged
    # startup migration.  Each one performs the same complete transaction.
    def _migrate_single_user(self):
        with self._session_authority_lock:
            self._load_startup_config()

    def _migrate_subject_identities(self) -> None:
        with self._session_authority_lock:
            self._load_startup_config()

    def _drop_reserved_loaded_users(self):
        with self._session_authority_lock:
            self._load_startup_config()

    def _migrate_legacy_admin_role(self):
        with self._session_authority_lock:
            self._load_startup_config()

    def _save(self):
        with _AUTH_FILE_WRITE_LOCK:
            _atomic_write_json(self.auth_path, self._config, indent=2)

    @property
    def users(self) -> Dict[str, Any]:
        return self._config.get("users", {})

    @property
    def signup_enabled(self) -> bool:
        return self._config.get("signup_enabled", False)

    @signup_enabled.setter
    def signup_enabled(self, value: bool):
        with self._config_lock:
            self._config["signup_enabled"] = value
            self._save()

    @property
    def is_configured(self) -> bool:
        return len(self.users) > 0

    def subject_id_for_username(self, username: str | None) -> Optional[str]:
        """Resolve a login name to its immutable, non-public owner ID."""
        with self._config_lock:
            users = self._config.get("users")
            if type(users) is not dict:
                raise AuthSubjectIdentityError("invalid account identity registry")
            normalized = normalize_known_username(users, username)
            if normalized is None:
                return None
            identities = []
            for account in users.values():
                if type(account) is not dict:
                    raise AuthSubjectIdentityError("invalid account identity registry")
                identity = account.get("subject_id")
                if not isinstance(identity, str) or not SUBJECT_ID_PATTERN.fullmatch(identity):
                    raise AuthSubjectIdentityError("invalid account identity registry")
                identities.append(identity)
            if len(identities) != len(set(identities)):
                raise AuthSubjectIdentityError("invalid account identity registry")
            return users[normalized]["subject_id"]

    def active_admin_subject_identity(
        self,
        *,
        username: str | None = None,
        subject_id: str | None = None,
    ) -> tuple[str, str] | None:
        """Atomically resolve one current admin alias and immutable subject.

        Exactly one selector is required. This read-only snapshot is used at
        MCP token issuance and dispatch; it does not change account lifecycle
        or identity migration behavior.
        """
        if (username is None) == (subject_id is None):
            return None
        with self._config_lock:
            users = self._config.get("users")
            if type(users) is not dict:
                raise AuthSubjectIdentityError("invalid account identity registry")
            identities: list[str] = []
            for account in users.values():
                if type(account) is not dict:
                    raise AuthSubjectIdentityError("invalid account identity registry")
                identity = account.get("subject_id")
                if not isinstance(identity, str) or not SUBJECT_ID_PATTERN.fullmatch(identity):
                    raise AuthSubjectIdentityError("invalid account identity registry")
                identities.append(identity)
            if len(identities) != len(set(identities)):
                raise AuthSubjectIdentityError("invalid account identity registry")
            if username is not None:
                normalized = normalize_known_username(users, username)
                if normalized is None:
                    return None
                account = users[normalized]
                if account.get("is_admin") is not True:
                    return None
                return normalized, account["subject_id"]
            if not isinstance(subject_id, str) or not SUBJECT_ID_PATTERN.fullmatch(subject_id):
                return None
            matches = [
                (name, account)
                for name, account in users.items()
                if account.get("subject_id") == subject_id and account.get("is_admin") is True
            ]
            if len(matches) != 1:
                return None
            return matches[0][0], subject_id

    @contextmanager
    def guard_session_authority(self) -> Iterator[None]:
        """Serialize a durable operation with account/session mutations.

        This process-local guard conveys no identity or authorization.  A
        caller that needs authority must establish it separately while the
        guard is held.
        """
        with self._session_authority_lock:
            yield

    @contextmanager
    def guard_current_admin_subject(self, expected_subject_id: str) -> Iterator[str]:
        """Hold process-local account authority for one SID-bound DB action.

        The config lock is held only while resolving the unique current admin
        alias.  The outer authority lock remains held across the caller's SQL
        work so token revocation, account deletion, rename, and demotion cannot
        cross the authorization/commit boundary in this process.
        """
        with self._session_authority_lock:
            identity = self.active_admin_subject_identity(subject_id=expected_subject_id)
            if identity is None or identity[1] != expected_subject_id:
                raise AuthSubjectIdentityError("current admin subject is not authorized")
            yield identity[0]

    def subject_id_for_owner_scope(self, owner_scope: str) -> str | None:
        """Resolve one current account identity from its owner-scoped USI value."""
        try:
            from src.unified_source_index_owner_scope import OwnerScope
            if type(owner_scope) is not str:
                raise AuthSubjectIdentityError("invalid account identity registry")
            with self._config_lock:
                users = self._config.get("users")
                if type(users) is not dict:
                    raise AuthSubjectIdentityError("invalid account identity registry")
                identities: list[str] = []
                for account in users.values():
                    if type(account) is not dict:
                        raise AuthSubjectIdentityError("invalid account identity registry")
                    subject = account.get("subject_id")
                    if not isinstance(subject, str) or not SUBJECT_ID_PATTERN.fullmatch(subject):
                        raise AuthSubjectIdentityError("invalid account identity registry")
                    identities.append(subject)
                if len(identities) != len(set(identities)):
                    raise AuthSubjectIdentityError("invalid account identity registry")
                matches = [subject for subject in identities
                           if OwnerScope.for_subject_id(subject).value == owner_scope]
                return matches[0] if len(matches) == 1 else None
        except AuthSubjectIdentityError:
            raise
        except BaseException:
            raise AuthSubjectIdentityError("invalid account identity registry") from None

    def has_active_subject_id(self, subject_id: str) -> bool:
        """Return true only when one current account owns this immutable ID."""
        if not isinstance(subject_id, str) or not SUBJECT_ID_PATTERN.fullmatch(subject_id):
            raise AuthSubjectIdentityError("invalid account identity registry")
        with self._config_lock:
            users = self._config.get("users")
            if type(users) is not dict:
                raise AuthSubjectIdentityError("invalid account identity registry")
            identities = []
            for account in users.values():
                if type(account) is not dict:
                    raise AuthSubjectIdentityError("invalid account identity registry")
                identity = account.get("subject_id")
                if not isinstance(identity, str) or not SUBJECT_ID_PATTERN.fullmatch(identity):
                    raise AuthSubjectIdentityError("invalid account identity registry")
                identities.append(identity)
            if len(identities) != len(set(identities)):
                raise AuthSubjectIdentityError("invalid account identity registry")
            return identities.count(subject_id) == 1

    def policy(self) -> dict:
        """Return public auth policy constants for the frontend."""
        return {
            "password_min_length": PASSWORD_MIN_LENGTH,
            "reserved_usernames": sorted(RESERVED_USERNAMES),
            "signup_enabled": self.signup_enabled,
            "session_days": TOKEN_TTL // 86400,
        }

    # ------------------------------------------------------------------
    # Account management
    # ------------------------------------------------------------------

    def setup(self, username: str, password: str) -> bool:
        """First-run admin setup. Only works if no users exist."""
        with self._setup_lock:
            if self.is_configured:
                return False
            return self.create_user(username, password, is_admin=True)

    def create_user(self, username: str, password: str, is_admin: bool = False) -> bool:
        """Create a new user account."""
        username = username.strip().lower()
        if not username:
            return False
        if username in RESERVED_USERNAMES:
            logger.warning("Refused to create reserved username '%s'", username)
            return False
        with self._session_authority_lock:
            with self._config_lock:
                if username in self.users:
                    return False
                if "users" not in self._config:
                    self._config["users"] = {}
                self._config["users"][username] = {
                    "password_hash": _hash_password(password),
                    "created": time.time(),
                    "is_admin": is_admin,
                    "subject_id": _new_subject_id(),
                    "privileges": dict(ADMIN_PRIVILEGES if is_admin else DEFAULT_PRIVILEGES),
                }
                self._save()
        logger.info(f"Created user '{username}' (admin={is_admin})")
        return True

    def delete_user(self, username: str, requesting_user: str) -> bool:
        """Delete a user. Only admins can delete, and can't delete themselves.

        SECURITY: also revoke every active session token belonging to this
        user so any open browser tab they have gets kicked back to /login
        on the next request. Without this the user kept full access until
        their cookie expired naturally (default ~30 days).
        """
        username = username.strip().lower()
        with self._session_authority_lock:
            with self._config_lock:
                if username not in self.users:
                    return False
                if username == requesting_user:
                    return False
                if not self.users.get(requesting_user, {}).get("is_admin"):
                    return False
            # Revoke API bearer tokens before removing the auth row. The bearer
            # path authenticates from ApiToken rows and does not require the
            # owner to still exist, so a successful delete must not leave active
            # rows behind. If the token store is unavailable, fail closed and
            # keep the user/session state intact so the admin can retry.
            try:
                from core.database import get_db_session, ApiToken
                with get_db_session() as db:
                    removed_tokens = db.query(ApiToken).filter(ApiToken.owner == username).delete()
                if removed_tokens:
                    logger.info(
                        f"Revoked {removed_tokens} API token(s) owned by deleted user '{username}'"
                    )
            except Exception:
                logger.warning(f"Failed to revoke API tokens for deleted user '{username}'")
                return False
            with self._config_lock:
                # The outer authority excludes account mutation while the DB
                # transaction runs, so this remains the account we authorized.
                if username not in self._config.get("users", {}):
                    return False
                del self._config["users"][username]
                self._save()
            # Purge all sessions belonging to this user.  The outer authority
            # still serializes this with strict resolution and guarded writes.
            revoked = 0
            with self._sessions_lock:
                to_drop = [tok for tok, sess in self._sessions.items()
                           if (sess or {}).get("username") == username]
                for tok in to_drop:
                    self._sessions.pop(tok, None)
                    revoked += 1
            if revoked:
                self._save_sessions()
        logger.info(f"Deleted user '{username}' (by {requesting_user}); revoked {revoked} active session(s)")
        return True

    def rename_user(self, old_username: str, new_username: str, requesting_user: str) -> bool:
        """Rename a user in auth config and active sessions. Admin only."""
        old_username = old_username.strip().lower()
        new_username = new_username.strip().lower()
        requesting_user = (requesting_user or "").strip().lower()
        if not old_username or not new_username:
            return False
        if new_username in RESERVED_USERNAMES:
            logger.warning("Refused to rename '%s' into reserved username '%s'", old_username, new_username)
            return False
        with self._session_authority_lock:
            with self._config_lock:
                if old_username not in self.users:
                    return False
                if new_username in self.users:
                    return False
                if not self.users.get(requesting_user, {}).get("is_admin"):
                    return False
                self._bump_security_action_revision()
                self._config.setdefault("users", {})[new_username] = self._config["users"].pop(old_username)
                self._save()

            renamed_sessions = 0
            with self._sessions_lock:
                for sess in self._sessions.values():
                    sess_user = str((sess or {}).get("username") or "").strip().lower()
                    if sess_user == old_username:
                        sess["username"] = new_username
                        renamed_sessions += 1
            if renamed_sessions:
                self._save_sessions()
        logger.info(
            "Renamed user '%s' -> '%s' (by %s); updated %d active session(s)",
            old_username, new_username, requesting_user, renamed_sessions,
        )
        return True

    def is_admin(self, username: str) -> bool:
        return self.users.get(username, {}).get("is_admin", False)

    def security_action_revision(self) -> int:
        with self._security_action_revision_lock:
            return self._security_action_revision

    def _bump_security_action_revision(self) -> None:
        with self._security_action_revision_lock:
            self._security_action_revision += 1

    def list_users(self) -> List[Dict[str, Any]]:
        return [
            {"username": u, "is_admin": d.get("is_admin", False), "privileges": self.get_privileges(u)}
            for u, d in self.users.items()
        ]

    def get_privileges(self, username: str) -> Dict[str, Any]:
        """Get privileges for a user. Admins get all privileges."""
        user = self.users.get(username, {})
        if user.get("is_admin"):
            return dict(ADMIN_PRIVILEGES)
        # Merge stored privileges with defaults (in case new privileges were added)
        stored = user.get("privileges", {})
        return {**DEFAULT_PRIVILEGES, **stored}

    def set_privileges(self, username: str, privileges: Dict[str, Any]) -> bool:
        """Update privileges for a user. Can't modify admin privileges."""
        username = username.strip().lower()
        with self._config_lock:
            if username not in self.users:
                return False
            if self.users[username].get("is_admin"):
                return False  # admins always have full access
            # Only allow known privilege keys
            current = self.get_privileges(username)
            for k, v in privileges.items():
                if k in DEFAULT_PRIVILEGES:
                    current[k] = v
            self._config["users"][username]["privileges"] = current
            self._save()
        logger.info(f"Updated privileges for '{username}': {current}")
        return True

    def set_admin(self, username: str, is_admin: bool,
                  requesting_user: str) -> SetAdminResult:
        """Promote/demote an existing user to/from admin. Admin only.

        Refuses to remove the last remaining admin so the instance can never
        be locked out of admin access; self-demotion is allowed as long as
        another admin remains. Admin status is re-checked live on every
        request, so unlike delete/rename no session or token revocation is
        needed — a demoted admin simply fails the next is_admin() gate.

        Promotion stashes the user's current privilege map and demotion
        restores it, so a temporary admin stint can't silently broaden a
        user's non-admin access; users without a stash (created as admin,
        or promoted before stashing existed) demote to DEFAULT_PRIVILEGES.

        Counting admins and flipping the flag happen in one critical section
        so two concurrent demotions can't race the admin count to zero.
        """
        username = (username or "").strip().lower()
        requesting_user = (requesting_user or "").strip().lower()
        is_admin = bool(is_admin)
        with self._session_authority_lock:
            with self._config_lock:
                target = self._config.get("users", {}).get(username)
                if target is None:
                    return SetAdminResult.USER_NOT_FOUND
                if not self.users.get(requesting_user, {}).get("is_admin"):
                    return SetAdminResult.NOT_AUTHORIZED
                currently_admin = bool(target.get("is_admin"))
                if currently_admin == is_admin:
                    return SetAdminResult.OK  # no-op; leave privileges untouched
                if currently_admin and not is_admin:
                    admin_count = sum(1 for d in self.users.values() if d.get("is_admin"))
                    if admin_count <= 1:
                        return SetAdminResult.LAST_ADMIN
                # Invalidate grants before exposing either side of the role change.
                self._bump_security_action_revision()
            # Write order matters for lock-free readers: get_privileges()
            # reads without _config_lock and trusts is_admin, so the admin
            # flag must be flipped while the stored map is safe to expose —
            # before writing admin privileges on promote, after restoring
            # the pre-admin map on demote.
                if is_admin:
                    target["is_admin"] = True
                # Stash the pre-admin map so a later demotion can restore it.
                # While is_admin is set the stored map is inert: get_privileges
                # short-circuits to ADMIN_PRIVILEGES and set_privileges refuses
                # admins, so only set_admin ever touches the stash.
                    target["privileges_before_admin"] = dict(
                        target.get("privileges") or DEFAULT_PRIVILEGES
                    )
                    target["privileges"] = dict(ADMIN_PRIVILEGES)
                else:
                # Restore the stashed pre-admin map. Fall back to defaults for
                # users created as admins (their stored map is ADMIN_PRIVILEGES,
                # which must not leak past demotion — e.g. can_use_bash) and
                # for admins promoted before the stash existed.
                    target["privileges"] = dict(
                        target.pop("privileges_before_admin", None)
                        or DEFAULT_PRIVILEGES
                    )
                    target["is_admin"] = False
                self._save()
        logger.info("Set is_admin=%s for '%s' (by '%s')", is_admin, username, requesting_user)
        return SetAdminResult.OK

    def change_password(self, username: str, current_password: str, new_password: str) -> bool:
        username = username.strip().lower()
        if not self.verify_password(username, current_password):
            return False
        with self._session_authority_lock:
            with self._config_lock:
                if username not in self._config.get("users", {}):
                    return False
                self._bump_security_action_revision()
                self._config["users"][username]["password_hash"] = _hash_password(new_password)
                self._save()
        return True

    # ------------------------------------------------------------------
    # TOTP two-factor authentication
    # ------------------------------------------------------------------

    def totp_enabled(self, username: str) -> bool:
        """Check if 2FA is enabled for a user."""
        with self._config_lock:
            user = self._config.get("users", {}).get(username.strip().lower(), {})
            return bool(user.get("totp_enabled")) if type(user) is dict else False

    def totp_generate_secret(self, username: str) -> Optional[str]:
        """Generate a new TOTP secret for a user. Returns the secret (not yet enabled)."""
        username = username.strip().lower()
        if username not in self.users:
            return None
        secret = pyotp.random_base32()
        with self._config_lock:
            self._config["users"][username]["totp_secret_pending"] = secret
            self._save()
        return secret

    def totp_get_provisioning_uri(self, username: str, secret: str) -> str:
        """Get the otpauth:// URI for QR code generation."""
        totp = pyotp.TOTP(secret)
        return totp.provisioning_uri(name=username, issuer_name="Odysseus")

    def totp_confirm_enable(self, username: str, code: str) -> bool:
        """Verify a TOTP code against the pending secret, then enable 2FA."""
        username = username.strip().lower()
        with self._config_lock:
            user = self._config.get("users", {}).get(username, {})
            secret = user.get("totp_secret_pending") if type(user) is dict else None
        if not secret:
            return False
        totp = pyotp.TOTP(secret)
        if not totp.verify(code, valid_window=1):
            return False
        # Enable 2FA
        with self._session_authority_lock:
            with self._config_lock:
                if username not in self._config.get("users", {}):
                    return False
                self._bump_security_action_revision()
                self._config["users"][username]["totp_secret"] = secret
                self._config["users"][username]["totp_enabled"] = True
                self._config["users"][username].pop("totp_secret_pending", None)
                # Generate backup codes
                backup = [secrets.token_hex(4) for _ in range(8)]
                self._config["users"][username]["totp_backup_codes"] = backup
                self._save()
        logger.info(f"2FA enabled for '{username}'")
        return True

    def totp_verify(self, username: str, code: str) -> bool:
        """Verify a TOTP code for login."""
        username = username.strip().lower()
        with self._config_lock:
            stored = self._config.get("users", {}).get(username, {})
            user = copy.deepcopy(stored) if type(stored) is dict else {}
        if not user.get("totp_enabled"):
            return True  # 2FA not enabled, always pass
        secret = user.get("totp_secret")
        if not secret:
            # 2FA is enabled but no secret is stored (corrupt/partially-written
            # auth.json). Fail closed — returning True here bypassed the second
            # factor entirely.
            return False
        # Check backup codes first
        backup = user.get("totp_backup_codes", [])
        if code in backup:
            with self._session_authority_lock:
                with self._config_lock:
                    current = self._config.get("users", {}).get(username)
                    if type(current) is not dict or code not in current.get("totp_backup_codes", []):
                        return False
                    current_backup = list(current.get("totp_backup_codes", []))
                    current_backup.remove(code)
                    self._config["users"][username]["totp_backup_codes"] = current_backup
                    self._save()
            logger.info(f"Backup code used for '{username}' ({len(backup)} remaining)")
            return True
        totp = pyotp.TOTP(secret)
        return totp.verify(code, valid_window=1)

    def totp_verify_live(self, username: str, code: str) -> bool:
        """Verify only a current authenticator-app code for privileged step-up.

        Login intentionally continues to accept single-use backup codes.  They
        are recovery credentials, not a substitute for the live second factor
        required by security-incident operator actions.
        """
        username = username.strip().lower()
        with self._config_lock:
            stored = self._config.get("users", {}).get(username, {})
            user = copy.deepcopy(stored) if type(stored) is dict else {}
        if not user.get("totp_enabled"):
            return False
        secret = user.get("totp_secret")
        if not isinstance(secret, str) or not secret:
            return False
        if not isinstance(code, str) or not code.strip():
            return False
        try:
            return bool(pyotp.TOTP(secret).verify(code.strip(), valid_window=0))
        except (TypeError, ValueError):
            return False

    def totp_disable(self, username: str, password: str) -> bool:
        """Disable 2FA for a user. Requires password confirmation."""
        username = username.strip().lower()
        if not self.verify_password(username, password):
            return False
        with self._session_authority_lock:
            with self._config_lock:
                if username not in self._config.get("users", {}):
                    return False
                self._bump_security_action_revision()
                self._config["users"][username].pop("totp_secret", None)
                self._config["users"][username].pop("totp_secret_pending", None)
                self._config["users"][username].pop("totp_backup_codes", None)
                self._config["users"][username]["totp_enabled"] = False
                self._save()
        logger.info(f"2FA disabled for '{username}'")
        return True

    # ------------------------------------------------------------------
    # Login / logout / session tokens
    # ------------------------------------------------------------------

    def verify_password(self, username: str, password: str) -> bool:
        username = username.strip().lower()
        with self._config_lock:
            record = self._config.get("users", {}).get(username)
            password_hash = record.get("password_hash") if type(record) is dict else None
        if not isinstance(password_hash, str) or not password_hash:
            return False
        try:
            return _verify_password(password, password_hash)
        except Exception:
            return False

    def capture_login_identity(self, username: str) -> Optional[LoginIdentityFence]:
        username = username.strip().lower()
        with self._session_authority_lock:
            with self._config_lock:
                record = self._config.get("users", {}).get(username)
                subject_id = record.get("subject_id") if type(record) is dict else None
                if not isinstance(subject_id, str) or not SUBJECT_ID_PATTERN.fullmatch(subject_id):
                    return None
                with self._security_action_revision_lock:
                    fence = LoginIdentityFence(subject_id, self._security_action_revision)
                    nonce = secrets.token_hex(16)
                    object.__setattr__(fence, "_issuer", self._fence_issuer)
                    object.__setattr__(fence, "_nonce", nonce)
                    fence_id = id(fence)
                    manager_ref = weakref.ref(self)

                    def discard_abandoned_fence(
                        fence_ref: weakref.ReferenceType[LoginIdentityFence],
                        *,
                        manager_ref: weakref.ReferenceType[AuthManager] = manager_ref,
                        fence_id: int = fence_id,
                    ) -> None:
                        manager = manager_ref()
                        if manager is None:
                            return
                        with manager._session_authority_lock:
                            issued = manager._issued_login_fences.get(fence_id)
                            if issued is not None and issued.fence_ref is fence_ref:
                                manager._issued_login_fences.pop(fence_id, None)

                    fence_ref = weakref.ref(fence, discard_abandoned_fence)
                    self._issued_login_fences[fence_id] = _IssuedLoginFence(
                        fence_ref=fence_ref,
                        subject_id=subject_id,
                        security_revision=self._security_action_revision,
                        issuer=self._fence_issuer,
                        nonce=nonce,
                    )
                    return fence

    def create_session(self, username: str, password: str) -> Optional[str]:
        """Verify credentials and return a session token, or None."""
        username = username.strip().lower()
        identity_fence = self.capture_login_identity(username)
        if identity_fence is None:
            return None
        if not self.verify_password(username, password):
            return None
        return self.create_session_trusted(username, identity_fence=identity_fence)

    def create_session_trusted(
        self,
        username: str,
        *,
        identity_fence: Optional[LoginIdentityFence] = None,
    ) -> Optional[str]:
        """Issue a session token for an already-verified user.
        Call only after verify_password (and TOTP if enabled) have passed."""
        username = username.strip().lower()
        token = secrets.token_hex(32)
        if type(identity_fence) is not LoginIdentityFence:
            return None
        with self._session_authority_lock:
            # Consume before checking mutable account state.  A copied,
            # replayed, cross-manager, or manually-built object cannot enter
            # this registry, and an issued fence cannot be retried.
            issued = self._issued_login_fences.pop(id(identity_fence), None)
            if issued is None or issued.fence_ref() is not identity_fence:
                return None
            if (
                type(identity_fence.subject_id) is not str
                or type(identity_fence.security_revision) is not int
                or type(identity_fence._nonce) is not str
                or identity_fence.subject_id != issued.subject_id
                or identity_fence.security_revision != issued.security_revision
                or identity_fence._issuer is not issued.issuer
                or identity_fence._nonce != issued.nonce
            ):
                return None
            with self._config_lock:
                record = self._config.get("users", {}).get(username)
                if type(record) is not dict:
                    logger.warning("Refused to issue session for missing user '%s'", username)
                    return None
                subject_id = record.get("subject_id")
                if not isinstance(subject_id, str) or not SUBJECT_ID_PATTERN.fullmatch(subject_id):
                    return None
                if identity_fence.subject_id != subject_id:
                    return None
                with self._security_action_revision_lock:
                    if identity_fence.security_revision != self._security_action_revision:
                        return None
                with self._sessions_lock:
                    self._sessions[token] = {
                        "username": username,
                        "subject_id": subject_id,
                        "expiry": time.time() + TOKEN_TTL,
                    }
            if not self._save_sessions():
                with self._sessions_lock:
                    self._sessions.pop(token, None)
                return None
        return token

    def _current_session_identity(self, token: Optional[str], *, strict: bool) -> Optional[tuple[str, Optional[str]]]:
        """Resolve a cookie from its persisted SID under one authority snapshot.

        A legacy SID-less row remains usable only through the legacy helpers;
        strict callers never repair or upgrade it from its mutable alias.
        """
        if not isinstance(token, str) or not token:
            return None
        invalid = False
        resolved: Optional[tuple[str, Optional[str]]] = None
        with self._session_authority_lock:
            # Keep the normal runtime ordering AUTHORITY -> CONFIG -> SESSIONS.
            # The snapshots are copied while their short locks are held, then
            # validated without a CONFIG/SESSIONS nesting inversion.
            with self._config_lock:
                raw_users = self._config.get("users", {})
                users = copy.deepcopy(raw_users) if type(raw_users) is dict else None
            with self._sessions_lock:
                session = self._sessions.get(token)
                snapshot = copy.deepcopy(session) if type(session) is dict else None
            if snapshot is None:
                return None
            expiry = snapshot.get("expiry")
            if (
                type(expiry) not in (int, float)
                or not math.isfinite(expiry)
                or time.time() >= expiry
            ):
                invalid = True
            session_sid = snapshot.get("subject_id")
            if session_sid is None and strict:
                # Strict browser authentication must leave a valid legacy row
                # untouched.  Only compatibility helpers may resolve it.
                return None
            if session_sid is not None and (not isinstance(session_sid, str) or not SUBJECT_ID_PATTERN.fullmatch(session_sid)):
                # A present malformed SID is corruption, never a legacy alias.
                invalid = True
            if users is None:
                invalid = True
            elif not invalid and isinstance(session_sid, str):
                # SID-bound rows consult the whole current registry and
                # require exactly one valid owner.  SID-less legacy rows
                # intentionally do not depend on other accounts' IDs.
                sid_to_name: Dict[str, str] = {}
                for name, record in users.items():
                    registry_sid = record.get("subject_id") if type(record) is dict else None
                    if registry_sid is None:
                        continue
                    if not isinstance(registry_sid, str) or not SUBJECT_ID_PATTERN.fullmatch(registry_sid) or registry_sid in sid_to_name:
                        invalid = True
                        break
                    sid_to_name[registry_sid] = name
                else:
                    owner = sid_to_name.get(session_sid)
                    resolved = (owner, session_sid) if owner else None
                    invalid = resolved is None
            elif not invalid:
                owner = str(snapshot.get("username") or "").strip().lower()
                record = users.get(owner)
                resolved = (owner, None) if isinstance(record, dict) else None
                invalid = resolved is None
            if invalid:
                with self._sessions_lock:
                    self._sessions.pop(token, None)
        if invalid:
            self._save_sessions()
        return resolved

    def validate_token(self, token: Optional[str]) -> bool:
        return self._current_session_identity(token, strict=False) is not None

    def get_username_for_token(self, token: Optional[str]) -> Optional[str]:
        """Return the username associated with a valid token."""
        identity = self._current_session_identity(token, strict=False)
        return identity[0] if identity is not None else None

    def strict_session_identity(self, token: Optional[str]) -> Optional[tuple[str, str]]:
        identity = self._current_session_identity(token, strict=True)
        if identity is None or identity[1] is None:
            return None
        return identity[0], identity[1]

    @contextmanager
    def guard_current_session(self, token: str, expected_subject_id: str) -> Iterator[str]:
        """Hold only session authority while a caller commits a SID-fenced CAS."""
        with self._session_authority_lock:
            identity = self._current_session_identity(token, strict=True)
            if identity is None or identity[1] != expected_subject_id:
                raise AuthSubjectIdentityError("current session is not authorized")
            yield identity[0]

    def revoke_token(self, token: str):
        with self._session_authority_lock:
            with self._sessions_lock:
                removed = self._sessions.pop(token, None)
            if removed is None:
                return None
            if self._save_sessions():
                return None
            # Fail closed in this process.  The caller must not report a
            # durable logout/revocation after the old file could not be
            # replaced, and the removed token must never be re-enabled.
            raise AuthSessionPersistenceError("session_revocation_not_durable")

    def revoke_user_sessions(self, username: str, except_token: Optional[str] = None) -> int:
        """Revoke active browser sessions for a user, optionally preserving one."""
        username = username.strip().lower()
        revoked = 0
        with self._session_authority_lock:
            with self._config_lock:
                users = self._config.get("users")
                record = users.get(username) if type(users) is dict else None
                current_sid = record.get("subject_id") if type(record) is dict else None
                if not isinstance(current_sid, str) or not SUBJECT_ID_PATTERN.fullmatch(current_sid):
                    current_sid = None
            with self._sessions_lock:
                to_drop = [
                    token for token, session in self._sessions.items()
                    if token != except_token and type(session) is dict and (
                        (
                            isinstance(session.get("subject_id"), str)
                            and current_sid is not None
                            and session.get("subject_id") == current_sid
                        )
                        or (
                            session.get("subject_id") is None
                            and str(session.get("username") or "").strip().lower() == username
                        )
                    )
                ]
                for token in to_drop:
                    self._sessions.pop(token, None)
                    revoked += 1
            if revoked:
                if not self._save_sessions():
                    raise AuthSessionPersistenceError("session_revocation_not_durable")
        return revoked

    def status(self, token: Optional[str]) -> Dict[str, Any]:
        username = self.get_username_for_token(token)
        authenticated = username is not None
        result = {
            "configured": self.is_configured,
            "authenticated": authenticated,
            "username": username,
            "is_admin": self.is_admin(username) if username else False,
        }
        if authenticated:
            result["privileges"] = self.get_privileges(username)
        return result
