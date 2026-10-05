# 管理员登录面板

## Goal

未登录的人打不开管理界面，也调不了管理接口。使用默认账密登录后，现有管理功能保持可用。外部 API Key 客户端和公开兑换链接保持原样。

## Background

- `web_ui.py:839` 的 `/` 与 `/index.html` 直接渲染 `templates/index.html`，没有登录。
- `static/js/01-core.js:14` 的 `fetch(path)` 不带额外凭证。首屏 `01-core.js:69` 请求 `/api/state` 和 `/api/accounts`。
- `/api/*` 管理接口目前无鉴权。`/api/v1/*` 由 `api_keys.py` 的 API Key 保护，明文 key 只返回一次，落盘是 SHA-256（`api_keys.py:121`）。
- 公开兑换是 `/shared`、`/api/shared/latest`、`/api/shared/redeem`、`/api/shared/<key>/latest`（`web_ui.py:1228`、`web_ui.py:1240`）。管理用的 `GET /api/shared` 和 `POST /api/shared/<id>/revoke`（`static/js/04-shared.js:19`、`04-shared.js:29`）不是公开兑换。
- 应用没有浏览器登录会话。`api_keys.json` 已在 `.gitignore:25`，同类敏感文件不得进 git。

## Requirements

- R1. 未登录访问 `/` 或 `/index.html` 只返回登录面板，不加载管理脚本，不展示账号、邮箱或口令。
- R2. 初始默认账号 `admin`，密码 `123456qwe`。数据文件不存在时用这对账密可以登录。文件已存在时不得覆盖。
- R3. 账密错误时返回统一的失败，不区分用户名是否存在。响应和日志都不含明文密码、cookie 或内部路径。
- R4. 登录成功后进入现有管理界面。顶栏提供退出。退出后必须重新登录。
- R5. 管理前端使用的 `/api/*` 未登录时返回 401，且不执行原操作。`GET /api/shared` 与吊销兑换码算管理接口。
- R6. `/api/v1/*` 仍只认 API Key，不要求管理员会话。公开兑换页和公开兑换接口不要求管理员会话。
- R7. 只有一个管理员。不做多用户、注册、角色、改密页面、找回或 2FA。

## Acceptance Criteria

- [ ] AC1. 未登录打开 `/` 只有登录面板。`GET /api/state`、`GET /api/accounts`、`GET /api/shared` 返回 401，正文没有账号或邮箱数据。
- [ ] AC2. `admin` / `123456qwe` 登录后，现有仪表盘和侧栏可用，管理接口恢复。
- [ ] AC3. 错误账密不能建立会话。
- [ ] AC4. 退出后，`/` 回到登录面板，管理接口再次 401。
- [ ] AC5. 不带管理员会话时，有效 API Key 仍能调用 `/api/v1/*`；无 key 仍是 401。
- [ ] AC6. 不带管理员会话时，`/shared` 与 `/api/shared/latest`、`/api/shared/redeem`、`/api/shared/<key>/latest` 仍按现有规则可用。`POST /api/shared/<id>/revoke` 未登录返回 401。

## Out of Scope

- 多管理员、自助注册、角色、改密、找回、2FA、登录失败锁定。
- 改变 `/api/v1` 的 key 协议。
- 改变公开兑换的入参、限流和脱敏。
- 把 API Key 页面改成不再保存 key；管理员会话只是额外的门，页面仍可按现有方式携带 `X-API-Key`。
