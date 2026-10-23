---
phase: 01-auth-core-and-test-script-plumbing
reviewed: 2026-10-23T00:00:00Z
depth: standard
files_reviewed: 39
files_reviewed_list:
  - watermeter/auth.py
  - watermeter/auth_middleware.py
  - watermeter/ratelimit.py
  - watermeter/routes/auth.py
  - watermeter/routes/health.py
  - watermeter/routes/service.py
  - watermeter/app.py
  - watermeter/__main__.py
  - watermeter/static/auth.js
  - watermeter/static/account.js
  - watermeter/static/style.css
  - watermeter/templates/account.html
  - watermeter/templates/login.html
  - watermeter/templates/setup.html
  - watermeter/templates/_nav.html
  - watermeter/templates/_theme.html
  - scripts/auth_smoke.sh
  - debug.sh
  - debug_clean.sh
  - docker-compose.yml
  - .env.example
  - .github/workflows/docker.yml
  - tests/conftest.py
  - tests/integration/conftest.py
  - tests/integration/test_smoke.py
  - tests/unit/conftest.py
  - tests/unit/js/auth_shim_check.js
  - tests/unit/test_auth_helpers.py
  - tests/unit/test_auth_js.py
  - tests/unit/test_auth_middleware.py
  - tests/unit/test_auth_nav.py
  - tests/unit/test_auth_routes.py
  - tests/unit/test_auth_startup.py
  - tests/unit/test_auth_store.py
  - tests/unit/test_auth_wiring.py
  - tests/unit/test_cli_set_password.py
  - tests/unit/test_ratelimit.py
  - tests/unit/test_route_inventory.py
findings:
  critical: 0
  warning: 4
  info: 6
  total: 10
status: issues_found
---

# Phase 01: Code Review Report

**Reviewed:** 2026-10-23
**Depth:** standard
**Files Reviewed:** 39 (the unit tests were skimmed for reliability, not line-reviewed)
**Status:** issues_found

## Summary

The auth core is carefully built. The scrypt parameters are bounded, session tokens are compared in constant time, `safe_next` closes the open-redirect paths I tried, and the middleware is default-deny and fails closed. The throttle reserves its slot atomically before the awaited hash. I found no authentication bypass and no injection. The 358 auth-related unit tests pass and `ruff check` is clean.

The defects are in failure handling around the credential file and in the setup flow:
- A misconfigured password seed silently leaves the public setup page open.
- `AuthStore.create` depends on hard links and has no fallback.
- `POST /setup` has no handler for `OSError` from the store.
- Form parsing can raise an unhandled `ValueError`.
- The throttle is trivially bypassed for IPv6 clients.

Six smaller items are listed under Info.

## Warnings

### WR-01: An invalid or unreadable password seed leaves the unauthenticated setup page open

**File:** `watermeter/auth.py:540-579`, `watermeter/app.py:51-76`
**Issue:** An operator who sets `WATERMETER_ADMIN_PASSWORD` or `WATERMETER_ADMIN_PASSWORD_FILE` is saying "do not let anyone claim this instance on the setup page". `bootstrap_from_env` fails open on every error path:
- The seed is shorter than 8 or longer than 1024 characters (line 571).
- The `_FILE` path is unreadable or a Docker secret is not yet mounted (line 546).
- `store.create` returns False or raises.

In each case the store stays UNCONFIGURED and `/setup` is served to anyone who can reach the port. The only signal is a log line. An upgrading install that expected to be locked down is exposed until someone reads the log.

**Fix:** Treat "a seed was supplied but could not be applied" as a distinct state that keeps `/setup` closed, for example an in-process flag that `_is_allowed` and `setup_page` honour. This should be a 503 with the logged reason. A minimal version:
```python
# auth.py
_seed_failed = False

def bootstrap_from_env(...):
    global _seed_failed
    ...
    if problem is not None:
        _seed_failed = True
        ...
```
The middleware then reports UNAVAILABLE while `_seed_failed` is set and the store is still UNCONFIGURED. Alternatively, document loudly that a bad seed means open setup, and have the setup page render the seed error to the visitor.

### WR-02: `AuthStore.create` requires hard links, and the setup route turns any `OSError` into a bare 500

**File:** `watermeter/auth.py:329-335`, `watermeter/routes/auth.py:159-163`, `watermeter/app.py:77`
**Issue:** There are three linked problems.
- No-clobber creation uses `os.link(tmp, path)`. Only `FileExistsError` is handled. Filesystems that reject `link(2)` (SMB/CIFS without Unix extensions, some FUSE and overlay mounts, FAT/exFAT) raise `EPERM`, `ENOTSUP` or `EMLINK`. Bind-mounting `./data` from a NAS share is common for this home-lab project. On such a mount the password can never be created. The CLI fallback hits the same code and only prints an error.
- `setup_submit` awaits `store.create` without a `try/except`. Permission errors on `/data` (`EACCES`, `EROFS`, a PUID mismatch) also surface as an unexplained "Internal Server Error" after the user typed a password.
- `_auth_startup` logs "the web UI stays locked" when seeding raises. The state is still UNCONFIGURED, so the setup page is open. The log message is wrong, which compounds WR-01.

**Fix:** Fall back to `O_CREAT | O_EXCL` for link-incapable filesystems, and catch `OSError` in the route:
```python
try:
    os.link(tmp, path)
except FileExistsError:
    return False
except OSError as exc:
    if exc.errno not in (errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EMLINK, errno.EXDEV):
        raise
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(record.to_dict(), handle)
        handle.flush()
        os.fsync(handle.fileno())
```
The `O_EXCL` write is not atomic for readers. A concurrent reader may briefly see a truncated file as UNAVAILABLE, which is acceptable under `_WRITE_LOCK` but worth a comment.

In `setup_submit`, wrap `store.create` in `try/except OSError`, log the error, and render `setup.html` with a message such as "Cannot write /data/auth.json: <reason>" and status 500. Correct the "stays locked" wording in `app.py:77` to match the actual state.

### WR-03: A form with more than 20 fields raises an unhandled `ValueError` (HTTP 500)

**File:** `watermeter/routes/auth.py:64`
**Issue:** `parse_qs(..., max_num_fields=20)` raises `ValueError("Max number of fields exceeded")`. I reproduced this with 25 fields. `_read_form` does not catch it, so an anonymous `POST /login` or `POST /setup` with many fields produces a 500 and a traceback in the log instead of the 413 used for oversized bodies. The behaviour is deliberate, but the handling is missing.

**Fix:**
```python
try:
    parsed = parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True, max_num_fields=20)
except ValueError:
    return None  # callers answer 413
```

### WR-04: The failure throttle is keyed on the full IPv6 address, so any IPv6 client can bypass it

**File:** `watermeter/routes/auth.py:32-34`, `watermeter/ratelimit.py:98-108`
**Issue:** `_client_ip` returns `request.client.host` unchanged. A client with an IPv6 prefix (a normal /64 on a LAN or from an ISP) has 2^64 source addresses. It can use a fresh key for every guess, so the 5 per minute and 20 per hour limits never apply. The 1024-key LRU is also trivially flushed. The limiter is the only brute-force defence for the scrypt-protected password.

**Fix:** Normalize the key before use. Collapse IPv6 to its /64 and keep IPv4 as is:
```python
import ipaddress

def _client_ip(request):
    host = request.client.host if request.client else "unknown"
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return host
    if addr.version == 6:
        return str(ipaddress.ip_network(f"{addr}/64", strict=False).network_address)
    return host
```
Use the normalized value for the limiter keys only. Keep the real address in the log lines.

## Info

### IN-01: The `_client_ip` docstring is inaccurate, and clients behind a proxy share one throttle key

**File:** `watermeter/routes/auth.py:32-34`
**Issue:** The comment says "X-Forwarded-For is never trusted", but uvicorn's default `--proxy-headers` rewrites `scope["client"]` from `X-Forwarded-For` when the peer is in `FORWARDED_ALLOW_IPS`.
- With the default `127.0.0.1`, behind a proxy on another host every user shares the proxy's address. One failing client then locks out the owner for up to an hour (the 20 per hour window).
- With `FORWARDED_ALLOW_IPS=*`, any client can spoof its key and bypass the limiter.

The README mentions `FORWARDED_ALLOW_IPS` only for the Secure cookie.

**Fix:** Correct the comment, and add a README sentence that `FORWARDED_ALLOW_IPS` must name only the proxy. It also decides which address the login throttle sees.

### IN-02: Logout does not revoke the session token

**File:** `watermeter/routes/auth.py:247-253`, `watermeter/auth.py:372-411`
**Issue:** Tokens are stateless HMACs valid for 30 days. Logout only clears the browser cookie. A copied cookie keeps working until the password is changed (epoch bump). The Account page presents "Log out" as ending the session.

**Fix:** Document that changing the password is the revocation mechanism. If per-session revocation is wanted later, keep a small server-side denylist of issued-at values or add a per-session id to the token.

### IN-03: A double-submitted login form can show a 429 for a login that succeeded

**File:** `watermeter/routes/auth.py:215-224`, `watermeter/ratelimit.py:23`
**Issue:** `reserve` books the attempt up front and the `(1, 1)` window blocks any second request for one second. A double-click or a password-manager auto-submit sends two POSTs. The second gets "Too many attempts, try again in 1 s". The browser can discard the first response, which carried the session cookie, and show the error. The user must click again.

**Fix:** Disable the submit button on first submit in the login and setup templates, or accept it as a documented trade-off.

### IN-04: `debug.sh` loads `.env` with `source`, which mangles passwords containing `$`, spaces or `#`

**File:** `debug.sh:19-22`, `.env.example` (admin password block)
**Issue:** `.env` is also read by docker compose, which has different quoting rules. `WATERMETER_ADMIN_PASSWORD=pa$$word` or a value with spaces is expanded or truncated by bash. The seeded password then differs from what the operator expects, and they cannot log in.

**Fix:** Add to the `.env.example` comment that the password should be plain `[A-Za-z0-9._-]` or quoted. Alternatively, have `debug.sh` read the single variable with `grep`/`cut`.

### IN-05: The scripts exit without a message on some failures

**File:** `scripts/auth_smoke.sh:34-43`, `debug_clean.sh:~65`
**Issue:** There are two cases.
- Under `set -e`, `code=$(curl ...)` in `check()` aborts the script silently on a transport error (connection reset, TLS error). The expected "FAIL name" line is never printed and CI shows only a bare non-zero exit.
- `debug_clean.sh` calls `openssl rand` under `set -e`. A host without `openssl` aborts with no explanation.

**Fix:**
```bash
code=$(curl -s -o /dev/null -w '%{http_code}' "$@") || code="curl-error"
```
In `debug_clean.sh`, fall back to `head -c16 /dev/urandom | od -An -tx1 | tr -d ' \n'` or check `command -v openssl` first.

### IN-06: `change_password` reads the record before the awaited hash and does not re-check it

**File:** `watermeter/routes/auth.py:291-313`
**Issue:** `record` is read, then `verify_password` runs in a thread (about 200 ms), then `replace_password` overwrites whatever is on disk. If the owner resets the password with the CLI in that window, an in-flight request that verified against the old hash overwrites the new password. The window is tiny and needs the old current password, so it is low risk. The same newline-stripping logic also exists twice in `auth.py` (`_read_seed_from_env` and `_strip_one_newline`).

**Fix:** Have `replace_password` accept the `epoch` that was verified and raise `AuthStoreError` if `current.epoch` has moved. Reuse `_strip_one_newline` in `_read_seed_from_env`.

---

_Reviewed: 2026-10-23_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
