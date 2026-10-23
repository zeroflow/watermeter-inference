"""Single-admin credential store, password hashing and signed session tokens.

Leaf module: standard library only, so the ``set-password`` CLI can import it without pulling in the web app.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

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
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AuthStore:
    """auth.json reader and writer. ``path=None`` resolves the location on every call."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path

    def path(self) -> Path:
        """File this store reads and writes."""
        return self._path if self._path is not None else auth_file_path()

    def _load(self) -> AuthRecord | None:
        try:
            raw = self.path().read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        data = json.loads(raw)
        return AuthRecord(
            password_hash=data["password_hash"],
            session_secret=data["session_secret"],
            instance_id=data["instance_id"],
            epoch=data["epoch"],
            created_at=data["created_at"],
        )

    def state(self) -> AuthState:
        """UNCONFIGURED when no file exists, CONFIGURED when it holds a record."""
        return AuthState.CONFIGURED if self._load() is not None else AuthState.UNCONFIGURED

    def record(self) -> AuthRecord | None:
        """The stored record, or None unless the state is CONFIGURED."""
        return self._load()

    def _write_temp(self, data: dict) -> str:
        path = self.path()
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")  # created 0600
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            os.unlink(tmp)
            raise
        return tmp

    def create(self, password_hash: str) -> bool:
        """Create auth.json with a fresh secret and instance ID. False if the file already exists (AUTH-02)."""
        path = self.path()
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
            os.link(tmp, path)  # atomic and no-clobber: raises FileExistsError when the file is already there
        except FileExistsError:
            return False
        finally:
            os.unlink(tmp)
        return True

    def replace_password(self, password_hash: str) -> AuthRecord:
        """Swap the password hash, keep secret and instance ID, and bump the epoch to end every other session."""
        current = self._load()
        if current is None:
            raise AuthStoreError("no admin password is set")
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
