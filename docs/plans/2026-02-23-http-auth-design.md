# BL-48: HTTP API Authentication — Design

**Date:** 2026-02-23
**Status:** Approved
**Effort:** L

## Problem

All 40+ write endpoints accept unauthenticated requests from any LAN host. `/api/config` and `/api/mqtt/config` return plaintext MQTT credentials. Combined with `0.0.0.0` binding, any host on the network has full control.

## Decision

Frigate-style JWT authentication with a single admin account. Dashboard (current meter reading) remains publicly accessible; everything else requires admin login.

## Auth Model

- **Public (no auth):** Dashboard homepage, `GET /api/status`, `GET /api/status/html`, `GET /api/health`, `GET /login`, `POST /api/auth/login`, `GET /static/*`
- **Protected (admin JWT):** All other routes — config, ROI, training, models, label, MQTT, synthetic, explore. All POST/PUT/DELETE endpoints except login.
- **Setup mode:** `/setup` and `POST /api/auth/setup` — only available when no password has been set yet.

## Configuration

New `auth` section in `config.yaml`:

```yaml
auth:
  token_lifetime: 86400         # JWT lifetime in seconds (default: 24h)
  refresh_threshold: 1800       # auto-refresh when token has < X seconds left (default: 30min)
  cookie_name: "watermeter_token"
  cookie_secure: false          # set true behind HTTPS reverse proxy
```

Not configurable (by design):
- JWT secret: auto-generated 64-char hex, stored in `data/auth.json`
- Password hash: bcrypt, stored in `data/auth.json`
- Hash algorithm: bcrypt (fixed)

### `data/auth.json` (auto-generated)

```json
{
  "jwt_secret": "<64-char random hex>",
  "admin_password_hash": "<bcrypt hash>",
  "created_at": "2026-02-23T..."
}
```

### Password Initialization

Priority order:
1. `ADMIN_PASSWORD` env var — hashed and written to `auth.json` on startup (overwrites existing)
2. First-run setup UI — if no `auth.json` exists and no env var, accessing a protected page redirects to `/setup`

## Auth Flow

### First Start (no auth.json, no env var)

```
Access protected page → redirect /setup
Set password → auth.json created → redirect /login
```

### Normal Flow

```
Access protected page → redirect /login
Enter "admin" + password → POST /api/auth/login
bcrypt verify → JWT generated → HttpOnly cookie set
Redirect to original page
```

### Token Refresh

On every authenticated request: if token has less than `refresh_threshold` seconds remaining, a new token is issued and set as cookie.

### API Clients

```
POST /api/auth/login (JSON body) → JWT in response body
Subsequent requests: Authorization: Bearer <token>
```

### Logout

```
POST /api/auth/logout → cookie deleted
```

## Architecture

### New Files

| File | Purpose |
|------|---------|
| `watermeter/auth.py` | `AuthManager` class — JWT create/verify, bcrypt hash/check, auth.json management |
| `watermeter/routes/auth.py` | Login, logout, setup routes |
| `watermeter/templates/login.html` | Login form |
| `watermeter/templates/setup.html` | First-run password setup form |

### Modified Files

| File | Change |
|------|--------|
| `app.py` | Init `AuthManager`, include auth router, add token-refresh middleware |
| `routes/*.py` | Add `dependencies=[Depends(require_admin)]` at router level |
| `config_utils.py` | Add `auth` section to `CONFIG_SCHEMA` |
| `requirements.txt` | Add `PyJWT`, `bcrypt` |
| `requirements-docker.txt` | Add `PyJWT`, `bcrypt` |

### Auth Guard (FastAPI Depends)

```python
async def require_admin(request: Request) -> bool:
    # 1. Check cookie (config: cookie_name)
    # 2. Fallback: Authorization: Bearer header
    # 3. JWT decode + verify with secret from auth.json
    # 4. Raise HTTPException(401) / redirect to /login if invalid
    # 5. Auto-refresh if near expiry (config: refresh_threshold)
```

Applied at router level — no per-endpoint boilerplate:

```python
router = APIRouter(prefix="/api/config", dependencies=[Depends(require_admin)])
```

### Public Routes

Public routes use routers without the `require_admin` dependency. The dashboard page route and status API remain on unprotected routers.

## Login UI

Simple form matching existing dashboard style:
- Username field (pre-filled "admin", since single-user)
- Password field
- Login button
- Error message on wrong password
- No CSRF token needed (JWT-based, no session state)

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `PyJWT` | >=2.8 | JWT encode/decode |
| `bcrypt` | >=4.0 | Password hashing (OWASP-recommended) |

## Security Considerations

- JWT secret auto-generated (64 chars, `secrets.token_hex(32)`) — never in config.yaml
- Password stored as bcrypt hash — never in plaintext
- Cookie: `HttpOnly`, `SameSite=Lax`, `Secure` configurable
- Auth always active — no toggle to disable
- `data/auth.json` should not be served as static file (not under `/static`)
- Rate limiting on login endpoint deferred to BL-53
