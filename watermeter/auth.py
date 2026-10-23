"""Single-admin credential store, password hashing and signed session tokens.

Leaf module: standard library only, so the ``set-password`` CLI can import it without pulling in the web app.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# -- Environment variable names and paths (single source of truth for later plans) --------------------------------
AUTH_FILE_ENV = "WATERMETER_AUTH_FILE"
DEFAULT_AUTH_FILE = "/data/auth.json"
ADMIN_PASSWORD_ENV = "WATERMETER_ADMIN_PASSWORD"
ADMIN_PASSWORD_FILE_ENV = "WATERMETER_ADMIN_PASSWORD_FILE"
SETUP_TOKEN_ENV = "WATERMETER_SETUP_TOKEN_REQUIRED"
ALLOWED_ORIGINS_ENV = "WATERMETER_ALLOWED_ORIGINS"

# -- Session and cookie constants ------------------------------------------------------------------------------------
SESSION_LIFETIME = 2592000  # 30 days, fixed (D-17)
SESSION_REFRESH_AFTER = SESSION_LIFETIME // 2  # re-issue the cookie once more than half the lifetime has passed
COOKIE_PREFIX = "wm_session_"
CSRF_HEADER = "x-requested-with"
CSRF_HEADER_VALUE = "watermeter"
_FUTURE_SKEW = 300  # tolerate a client/server clock difference of 5 minutes when checking the issue time

# -- Password policy and public paths --------------------------------------------------------------------------------
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 1024
HEALTH_PATH = "/health"
LOGIN_PATH = "/login"
SETUP_PATH = "/setup"
LOGOUT_PATH = "/logout"
STATIC_PREFIX = "/static/"
README_AUTH_URL = "https://github.com/zeroflow/watermeter-inference#authentication"
README_RECOVERY_URL = "https://github.com/zeroflow/watermeter-inference#forgot-the-admin-password"

# -- scrypt parameters -----------------------------------------------------------------------------------------------
# N=2^15, r=8, p=3 is an equal-strength set from the OWASP password storage cheat sheet; it costs about 205 ms and
# 45 MiB on the shipped image. The caps bound what a tampered auth.json can make verify_password allocate.
SCRYPT_LN = 15
SCRYPT_R = 8
SCRYPT_P = 3
_MAX_LN = 17
_MAX_R = 16
_MAX_P = 16
_SALT_BYTES = 16
_DKLEN = 32
_MAX_STORED_HASH_LENGTH = 512
# Bounds concurrent hashing threads. A module-level asyncio semaphore would bind to a single event loop.
_KDF_SEMAPHORE = threading.Semaphore(2)

_TOKEN_PREFIX = "wm1"


class AuthState(str, Enum):
    """What the credential file says about this instance."""

    UNCONFIGURED = "unconfigured"
    CONFIGURED = "configured"
    UNAVAILABLE = "unavailable"


class AuthStoreError(Exception):
    """The credential store cannot do what was asked (for example replacing a password that was never set)."""


@dataclass(frozen=True)
class AuthRecord:
    """Parsed content of auth.json."""

    password_hash: str
    session_secret: str
    instance_id: str
    epoch: int
    created_at: str

    @property
    def cookie_name(self) -> str:
        """Session cookie name, unique per instance (D-16)."""
        return COOKIE_PREFIX + self.instance_id

    def to_dict(self) -> dict:
        """On-disk representation."""
        return {
            "version": 1,
            "password_hash": self.password_hash,
            "session_secret": self.session_secret,
            "instance_id": self.instance_id,
            "epoch": self.epoch,
            "created_at": self.created_at,
        }


def auth_file_path() -> Path:
    """Location of auth.json, resolved on every call so tests and operators can switch it."""
    return Path(os.environ.get(AUTH_FILE_ENV, DEFAULT_AUTH_FILE))


# -- Password hashing ------------------------------------------------------------------------------------------------


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text + "=" * (-len(text) % 4), validate=True)


def _derive(password: str, salt: bytes, ln: int, r: int, p: int) -> bytes:
    n = 1 << ln
    # OpenSSL 3.0 rejects N=2^15 under hashlib's default memory limit, so pass an explicit one sized to the parameters.
    maxmem = 2 * 128 * r * (n + p + 2)
    with _KDF_SEMAPHORE:
        return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=_DKLEN, maxmem=maxmem)


def hash_password(password: str) -> str:
    """Return a PHC-style scrypt hash string for ``password``."""
    salt = os.urandom(_SALT_BYTES)
    digest = _derive(password, salt, SCRYPT_LN, SCRYPT_R, SCRYPT_P)
    return f"$scrypt$ln={SCRYPT_LN},r={SCRYPT_R},p={SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """True only for the exact password; False (never an exception) for a malformed or over-sized stored hash."""
    try:
        if not isinstance(stored, str) or len(stored) > _MAX_STORED_HASH_LENGTH:
            return False
        empty, algo, params, salt_b64, hash_b64 = stored.split("$")
        if empty != "" or algo != "scrypt":
            return False
        values = dict(item.split("=") for item in params.split(","))
        ln, r, p = int(values["ln"]), int(values["r"]), int(values["p"])
        if not (1 <= ln <= _MAX_LN and 1 <= r <= _MAX_R and 1 <= p <= _MAX_P):
            return False
        expected = _unb64(hash_b64)
        derived = _derive(password, _unb64(salt_b64), ln, r, p)
        return hmac.compare_digest(derived, expected)
    except Exception:
        return False


# -- Credential store ------------------------------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


_SECRET_RE = re.compile(r"[0-9a-f]{64}")
_INSTANCE_ID_RE = re.compile(r"[0-9a-f]{8}")
# Serializes "is it unconfigured? then create it" and password replacement across threads (AUTH-02).
_WRITE_LOCK = threading.Lock()


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _parse_record(text: str) -> AuthRecord:
    """Validate the file content. Raises ValueError naming the first problem."""
    try:
        data = json.loads(text)
    except Exception as exc:  # JSONDecodeError, RecursionError on absurd nesting
        raise ValueError(f"not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    if not _is_int(data.get("version")) or data["version"] != 1:
        raise ValueError("unsupported version")
    password_hash = data.get("password_hash")
    if not isinstance(password_hash, str) or not password_hash.startswith("$scrypt$"):
        raise ValueError("password_hash missing or not a scrypt hash")
    secret = data.get("session_secret")
    if not isinstance(secret, str) or not _SECRET_RE.fullmatch(secret):
        raise ValueError("session_secret missing or malformed")
    instance_id = data.get("instance_id")
    if not isinstance(instance_id, str) or not _INSTANCE_ID_RE.fullmatch(instance_id):
        raise ValueError("instance_id missing or malformed")
    epoch = data.get("epoch")
    if not _is_int(epoch) or epoch < 1:
        raise ValueError("epoch missing or invalid")
    created_at = data.get("created_at")
    if not isinstance(created_at, str):
        raise ValueError("created_at missing")
    return AuthRecord(
        password_hash=password_hash,
        session_secret=secret,
        instance_id=instance_id,
        epoch=epoch,
        created_at=created_at,
    )


class AuthStore:
    """auth.json reader and writer. ``path=None`` resolves the location on every call.

    The file is re-parsed only when ``(st_mtime_ns, st_size, st_ino)`` changes, so a replacement written by another
    process (the set-password CLI) is picked up by the next call without a restart.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._cache_key: tuple | None = None
        self._cache_value: tuple[AuthState, AuthRecord | None] = (AuthState.UNAVAILABLE, None)
        self._logged: tuple | None = None

    def path(self) -> Path:
        """File this store reads and writes."""
        return self._path if self._path is not None else auth_file_path()

    def _log_unavailable(self, key: tuple, path: Path, reason: str) -> None:
        if self._logged == key:
            return
        self._logged = key
        logger.error(
            f"Auth file {path} is unreadable or invalid ({reason}); refusing every request except /health. "
            "Fix or delete it to recover (see README)."
        )

    def _load(self) -> tuple[AuthState, AuthRecord | None]:
        path = self.path()
        try:
            info = os.stat(path)
        except FileNotFoundError:
            return AuthState.UNCONFIGURED, None
        except OSError as exc:
            # Anything else (permissions on the directory, a file where the directory should be) fails closed.
            with self._lock:
                self._log_unavailable((str(path), "stat", exc.errno), path, str(exc))
            return AuthState.UNAVAILABLE, None
        # mode, uid and gid join the documented (mtime, size, inode) key so that a chmod or chown that makes the file
        # unreadable is noticed instead of being served from the cache.
        key = (str(path), info.st_mtime_ns, info.st_size, info.st_ino, info.st_mode, info.st_uid, info.st_gid)
        with self._lock:
            if key == self._cache_key:
                return self._cache_value
            try:
                text = path.read_bytes().decode("utf-8")
            except FileNotFoundError:
                return AuthState.UNCONFIGURED, None  # deleted between stat and read
            except OSError as exc:
                # Not cached: fixing the permissions must take effect without touching the file's mtime.
                self._log_unavailable(key + ("read", exc.errno), path, str(exc))
                return AuthState.UNAVAILABLE, None
            except UnicodeDecodeError as exc:
                value: tuple[AuthState, AuthRecord | None] = (AuthState.UNAVAILABLE, None)
                self._log_unavailable(key, path, f"not UTF-8: {exc}")
            else:
                try:
                    value = (AuthState.CONFIGURED, _parse_record(text))
                except ValueError as exc:
                    value = (AuthState.UNAVAILABLE, None)
                    self._log_unavailable(key, path, str(exc))
            self._cache_key = key
            self._cache_value = value
            return value

    def _invalidate(self) -> None:
        with self._lock:
            self._cache_key = None

    def state(self) -> AuthState:
        """UNCONFIGURED when no file exists, CONFIGURED for a valid one, UNAVAILABLE for anything else.

        Deliberate deviation from "a file without password_hash is unconfigured": no code path writes such a file,
        and no-clobber creation needs the path to be absent, so treating it as unconfigured would open a setup page
        that cannot complete.
        """
        return self._load()[0]

    def record(self) -> AuthRecord | None:
        """The stored record, or None unless the state is CONFIGURED."""
        return self._load()[1]

    def _write_temp(self, data: dict) -> str:
        """Write ``data`` to a fsynced 0600 temp file next to auth.json and return its path."""
        path = self.path()
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")  # created 0600
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
                handle.flush()
                os.fsync(handle.fileno())
            if os.geteuid() == 0:
                # `docker exec` runs as root while the app runs as an unprivileged user: hand the file to the owner of
                # the data directory so the app can still read the 0600 file.
                parent = os.stat(path.parent)
                os.chown(tmp, parent.st_uid, parent.st_gid)
        except BaseException:
            os.unlink(tmp)
            raise
        return tmp

    def create(self, password_hash: str) -> bool:
        """Create auth.json with a fresh secret and instance ID. False if a file exists in any state (AUTH-02)."""
        path = self.path()
        with _WRITE_LOCK:
            if self.state() is not AuthState.UNCONFIGURED:
                return False
            path.parent.mkdir(parents=True, exist_ok=True)
            record = AuthRecord(
                password_hash=password_hash,
                session_secret=secrets.token_hex(32),
                instance_id=secrets.token_hex(4),
                epoch=1,
                created_at=_utc_now_iso(),
            )
            tmp = self._write_temp(record.to_dict())
            try:
                os.link(tmp, path)  # atomic and no-clobber: raises FileExistsError if another process won
            except FileExistsError:
                return False
            finally:
                os.unlink(tmp)
                self._invalidate()
            return True

    def replace_password(self, password_hash: str) -> AuthRecord:
        """Swap the password hash, keep secret and instance ID, and bump the epoch to end every other session."""
        with _WRITE_LOCK:
            current = self.record()
            if current is None:
                raise AuthStoreError("no valid admin password record to replace")
            updated = AuthRecord(
                password_hash=password_hash,
                session_secret=current.session_secret,
                instance_id=current.instance_id,
                epoch=current.epoch + 1,
                created_at=current.created_at,
            )
            tmp = self._write_temp(updated.to_dict())
            try:
                os.replace(tmp, self.path())
            except BaseException:
                os.unlink(tmp)
                raise
            finally:
                self._invalidate()
            return updated


_store = AuthStore()


def get_store() -> AuthStore:
    """Process-wide store that follows WATERMETER_AUTH_FILE."""
    return _store


# -- Session tokens --------------------------------------------------------------------------------------------------


def _sign(record: AuthRecord, issued: str, epoch: str) -> str:
    body = f"{_TOKEN_PREFIX}.{issued}.{epoch}"
    return hmac.new(bytes.fromhex(record.session_secret), body.encode("ascii"), hashlib.sha256).hexdigest()


def issue_token(record: AuthRecord, now: float | None = None) -> str:
    """Session token ``wm1.<issued>.<epoch>.<hex hmac-sha256>`` for the record's current epoch."""
    issued = str(int(time.time() if now is None else now))
    epoch = str(record.epoch)
    return f"{_TOKEN_PREFIX}.{issued}.{epoch}.{_sign(record, issued, epoch)}"


def _canonical_int(text: str) -> bool:
    """Plain decimal digits without a sign, leading zeros or non-ASCII digits (and short enough to be sane)."""
    return text.isascii() and text.isdigit() and len(text) <= 12 and (text == "0" or not text.startswith("0"))


def verify_token(record: AuthRecord, token: str | None, now: float | None = None) -> int | None:
    """Issue time of a valid token, or None. Never raises."""
    try:
        if not token:
            return None
        parts = token.split(".")
        if len(parts) != 4 or parts[0] != _TOKEN_PREFIX:
            return None
        _, issued_text, epoch_text, signature = parts
        if not (_canonical_int(issued_text) and _canonical_int(epoch_text)):
            return None
        expected = _sign(record, issued_text, epoch_text)
        if not hmac.compare_digest(signature.encode("utf-8"), expected.encode("ascii")):
            return None
        if int(epoch_text) != record.epoch:
            return None
        issued = int(issued_text)
        current = time.time() if now is None else now
        if issued > current + _FUTURE_SKEW or current - issued > SESSION_LIFETIME:
            return None
        return issued
    except Exception:
        return None


def session_issued_at(record: AuthRecord | None, cookies: Mapping[str, str], now: float | None = None) -> int | None:
    """Issue time of the valid session cookie in ``cookies``, or None."""
    if record is None:
        return None
    return verify_token(record, cookies.get(record.cookie_name), now)


# -- Cookie headers (shared by the middleware and the routes so attributes never diverge) ---------------------------


def _cookie_header(name: str, value: str, max_age: int, secure: bool) -> str:
    header = f"{name}={value}; Max-Age={max_age}; Path=/; HttpOnly; SameSite=Lax"
    return header + "; Secure" if secure else header


def build_session_cookie(record: AuthRecord, token: str, secure: bool) -> str:
    """Set-Cookie value for a session. Always persistent (D-18); Secure only when the request used HTTPS (AUTH-13)."""
    return _cookie_header(record.cookie_name, token, SESSION_LIFETIME, secure)


def build_clear_cookie(record: AuthRecord, secure: bool) -> str:
    """Set-Cookie value that removes the session cookie."""
    return _cookie_header(record.cookie_name, "", 0, secure)


# -- Password policy -------------------------------------------------------------------------------------------------


def validate_new_password(password: str, confirm: str) -> str | None:
    """Error message for a new password, or None when it is acceptable.

    Length counts Unicode code points (the decoded str), so eight multi-byte characters pass. Passwords are encoded
    as UTF-8 without normalization, so only the identical code point sequence verifies later.
    """
    if not password:
        return "Password is required"
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Password must be at most {MAX_PASSWORD_LENGTH} characters"
    if password != confirm:
        return "Passwords do not match"
    return None


# -- Redirect targets ------------------------------------------------------------------------------------------------

_MAX_NEXT_LENGTH = 2048
_AUTH_PAGES = frozenset({LOGIN_PATH, SETUP_PATH, LOGOUT_PATH})


def safe_next(value: str | None) -> str:
    """A same-origin relative path that is safe to redirect to, otherwise ``/`` (T-01-09)."""
    if not isinstance(value, str) or not value or len(value) > _MAX_NEXT_LENGTH:
        return "/"
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        return "/"
    try:
        parts = urlsplit(value)
    except ValueError:
        return "/"
    if parts.scheme or parts.netloc:
        return "/"
    if (parts.path.rstrip("/") or "/") in _AUTH_PAGES:
        return "/"
    return value


def next_from_hx_current_url(hx_current_url: str | None, host: str | None) -> str:
    """Page to return to after login for an HTMX request, taken from its HX-Current-URL header.

    The fragment URL being polled must never become the target, so only the page the user is looking at counts, and
    only when it is on the same host.
    """
    if not hx_current_url or not host:
        return "/"
    try:
        parts = urlsplit(hx_current_url)
    except ValueError:
        return "/"
    if not parts.netloc or parts.netloc.lower() != host.lower():
        return "/"
    target = parts.path or "/"
    if parts.query:
        target += "?" + parts.query
    return safe_next(target)


# -- Setup token (AUTH-14, D-09) -------------------------------------------------------------------------------------

_setup_token: str | None = None
_setup_token_lock = threading.Lock()


def setup_token_required() -> bool:
    """True when WATERMETER_SETUP_TOKEN_REQUIRED is 1, true or yes (any case). Read at call time."""
    return os.environ.get(SETUP_TOKEN_ENV, "").strip().lower() in {"1", "true", "yes"}


def get_setup_token() -> str | None:
    """The per-process setup token (memory only, logged when generated), or None when the flag is off."""
    global _setup_token
    if not setup_token_required():
        return None
    with _setup_token_lock:
        if _setup_token is None:
            _setup_token = secrets.token_hex(8)
            logger.warning(f"Setup token required on the setup page: {_setup_token}")
        return _setup_token


def check_setup_token(candidate: str | None) -> bool:
    """True when no token is required, otherwise whether ``candidate`` equals it (constant-time compare)."""
    token = get_setup_token()
    if token is None:
        return True
    if not candidate:
        return False
    return hmac.compare_digest(candidate.strip().encode("utf-8"), token.encode("ascii"))


# -- Environment seed (AUTH-09) --------------------------------------------------------------------------------------


def _read_seed_from_env() -> str | None:
    """Seed password from the _FILE variable (preferred) or the plain variable. None when absent or unreadable."""
    file_path = os.environ.get(ADMIN_PASSWORD_FILE_ENV)
    if file_path:
        try:
            raw = Path(file_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.error(f"Cannot read the password file named by {ADMIN_PASSWORD_FILE_ENV}: {exc}")
            return None
        if raw.endswith("\r\n"):
            return raw[:-2]
        return raw[:-1] if raw.endswith("\n") else raw
    return os.environ.get(ADMIN_PASSWORD_ENV) or None


def bootstrap_from_env(store: AuthStore | None = None) -> bool:
    """Create auth.json from the environment while no password exists. True only when a file was written.

    The value is a seed, never an override: once a password exists the variables are ignored. The seed itself is never
    logged.
    """
    seed = _read_seed_from_env()
    if seed is None:
        return False
    store = get_store() if store is None else store
    state = store.state()
    if state is AuthState.CONFIGURED:
        logger.info("Admin password seed from the environment ignored: a password already exists")
        return False
    if state is AuthState.UNAVAILABLE:
        return False  # the store already logged why
    problem = validate_new_password(seed, seed)
    if problem is not None:
        logger.error(f"Admin password seed from the environment rejected: {problem}")
        return False
    if not store.create(hash_password(seed)):
        logger.info("Admin password seed from the environment not applied: auth.json appeared meanwhile")
        return False
    logger.info("Admin password seeded from the environment")
    return True
