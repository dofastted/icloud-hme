# Auth boundary

Confirmed from the current tree. This is the planning input for the admin login gate.

## Unauthenticated management surface

- `web_ui.py:839-841` renders `templates/index.html` for `/` and `/index.html`.
- `templates/index.html:45-52` loads the management scripts immediately.
- `static/js/01-core.js:14-15` calls `fetch(path)` with no explicit credentials. Same-origin fetch already sends cookies, so a session cookie works without changing `api()`.
- `static/js/01-core.js:69` loads `/api/state` and `/api/accounts` on startup.
- Management routes beginning at `web_ui.py:1149` (`/api/mailboxes` and the rest of `/api/*`) have no auth decorator. `/api/v1/*` uses `_require_api_key` (`web_ui.py:105-114`).

## Two existing doors that must stay

- API keys: `api_keys.py` stores only a SHA-256 digest. `POST /api/keys` is open only while no active key exists (`web_ui.py:843-848`). The key page sends `X-API-Key` from localStorage (`static/js/06-api-keys.js:18-19`).
- Public redeem: `GET|POST /api/shared/latest`, `GET|POST /api/shared/redeem`, `GET /api/shared/<key>/latest`, plus `/shared`. Tests live in `tests/test_shared_public.py`.
- Admin share management is different: `GET /api/shared` and `POST /api/shared/<id>/revoke` (`static/js/04-shared.js:19`, `04-shared.js:29`). A prefix exemption for `/api/shared` would leave revoke public.

## Persistence constraint

`.gitignore:25` already ignores `api_keys.json`. A new admin credential file and session secret belong in the same ignored data directory (`HME_DATA_DIR`, default repo root). Do not seed by writing that file at import time; tests import `web_ui` and would dirty the worktree.

## Chosen gate

User selected: lock the management page and `/api/*`; keep `/api/v1` on API keys; keep public redeem public.
