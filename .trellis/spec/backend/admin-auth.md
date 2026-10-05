# Admin session gate

Cross-layer contract for the management UI. API key clients and public redeem links are separate doors.

## Scenario: management login

### 1. Scope / Trigger

The management page and `/api/*` used by that page were open. A browser session now closes them. Do not fold `/api/v1` or public redeem into this session.

### 2. Signatures

- `POST /api/login`
- `POST /api/logout`
- `GET /` and `GET /index.html`
- `AdminAuthStore.verify(username: str, password: str) -> bool`
- Credential file: `$HME_DATA_DIR/admin.json`
- Session secret file: `$HME_DATA_DIR/admin_secret.key`

### 3. Contracts

`POST /api/login` body:

- `username`: string. Non-strings are treated as empty.
- `password`: string. Non-strings are treated as empty.

Success: `200 {"ok": true}` and a signed session cookie with `admin: true`. Cookie is `HttpOnly`, `SameSite=Lax`, not permanent. `Secure` only when `HME_COOKIE_SECURE` is `1`, `true`, `yes`, or `on`.

Failure: `401 {"ok": false, "error": "账号或密码错误"}`. Same text when the user is unknown or the password is wrong.

`POST /api/logout`: `200 {"ok": true}` and the cookie is cleared. No admin session required.

Unauthenticated `GET /` and `GET /index.html` render `templates/login.html` and do not include `static/js/01-core.js`. After login, the same routes render `templates/index.html`.

Protected `/api/*`: `401 {"ok": false, "error": "未登录"}` before the view runs.

Still public:

- `/api/v1` and `/api/v1/*` — existing API key (`Authorization: Bearer` or `X-API-Key`). Admin cookie does not replace the key.
- `GET|POST /api/shared/latest`
- `GET|POST /api/shared/redeem`
- `GET /api/shared/<key>/latest`
- `/shared` and `/static/*`

Still protected even though the path contains `shared`:

- `GET /api/shared`
- `POST /api/shared/<id>/revoke`

Default seed, only when `admin.json` is absent: username `admin`, password `123456qwe`. The file stores scrypt parameters `n=16384`, `r=8`, `p=1` plus salt and hash. An existing file is never rewritten by login. `admin.json`, `admin.json.tmp`, and `admin_secret.key` stay gitignored.

Logs must not contain the password.

### 4. Validation & Error Matrix

- Missing or wrong credentials -> `401 账号或密码错误`
- Non-string username or password -> same `401`
- Corrupt or unexpected `admin.json` -> login fails, file is left untouched
- No session on a management API -> `401 未登录`
- `/api/v1` without a key -> `401 invalid API key` (not `未登录`)
- `/api/v1` with a valid key and no admin cookie -> `200`
- Public redeem without an admin cookie -> not `401`

### 5. Good/Base/Bad Cases

- Good: `admin` / `123456qwe` on a fresh data dir, then `GET /api/state` is `200`.
- Base: logout, then `/` is the login page and `GET /api/state` is `401`.
- Bad: exempting every path under `/api/shared` makes revoke public.

### 6. Tests Required

- `tests/test_admin_login.py` asserts the anonymous page, `401` management APIs, default login, wrong password, logout, v1 key behavior, and that revoke is not called while redeem stays non-401.
- Existing management API tests must call `tests.support.login_admin` through `POST /api/login`. Do not disable the gate under `app.testing`.
- `tests/conftest.py` points the credential file and secret at `tmp_path`.

### 7. Wrong vs Correct

#### Wrong

```python
if request.path.startswith("/api/shared"):
    return None
```

#### Correct

```python
if path in ("/api/shared/latest", "/api/shared/redeem") and method in ("GET", "POST"):
    return True
if method == "GET" and path.startswith("/api/shared/") and path.endswith("/latest"):
    return True
```
