# 管理员登录设计

## Boundary

一个管理员会话，只保护管理页和管理接口。

放行：

- `POST /api/login`、`POST /api/logout`
- `/api/v1/*`（仍走现有 API Key）
- `GET|POST /api/shared/latest`、`GET|POST /api/shared/redeem`、`GET /api/shared/<path>/latest`
- `/shared` 与 `/static/*`

拦截：

- 其它 `/api/*`，包括 `GET /api/shared` 和 `POST /api/shared/<id>/revoke`
- 未登录的 `/`、`/index.html` 不渲染管理页

不能用 `/api/shared` 前缀放行，否则吊销接口会公开。

## Data flow

1. 未登录 `GET /` 返回 `templates/login.html`。页面只提交登录，不加载 `static/js/01-core.js` 等管理脚本。
2. `POST /api/login` 接收 `username`、`password`。校验通过后写签名 cookie，响应 `{"ok": true}`。失败统一 `401 {"ok": false, "error": "账号或密码错误"}`。
3. 浏览器再请求 `/`，服务端看到会话后返回现有 `index.html`。
4. `static/js/01-core.js` 的 `fetch` 保持不变。同源请求会带上 cookie。
5. `before_request` 对受保护的 `/api/*` 在进入视图前返回 `401 {"ok": false, "error": "未登录"}`。
6. 顶栏退出调用 `POST /api/logout`，清 cookie，然后重新加载 `/`。

会话是 Flask 签名 cookie，只放 `admin: true`，不放密码。`HttpOnly`，`SameSite=Lax`。默认不加 `Secure`，因为当前 `app.run` 是本地 HTTP；仅当环境变量 `HME_COOKIE_SECURE=1` 时开启。cookie 不设永久有效，关闭浏览器即失效。

## Credential store

新模块 `admin_auth.py`，形态对齐 `APIKeyStore`：数据目录文件、`RLock`、懒加载。

- 文件：`$HME_DATA_DIR/admin.json`，加入 `.gitignore`。
- 内容只有用户名和 `scrypt` 哈希，没有明文密码。
- 文件不存在时，第一次登录校验才写入默认账号 `admin` / `123456qwe`。文件已存在则绝不重置。
- 会话签名密钥是 `$HME_DATA_DIR/admin_secret.key`，同样 gitignore。进程内复用，重启后旧 cookie 仍有效。测试里把这两个路径指到 `tmp_path`，并给 `app.secret_key` 固定测试值。

不把默认密码写进日志。失败日志只写「管理员登录失败」。

## Compatibility

- `/api/v1/*` 不读管理员 cookie，也不接受用管理员 cookie 代替 API Key。
- API Key 页面继续从 localStorage 送 `X-API-Key`。管理员会话是额外的门，不替换 key 校验。
- 公开兑换的限流、404 和脱敏不改。
- 现有直接调用 `/api/*` 的 pytest 会变成 401。用登录 helper 补会话，不用测试开关关掉鉴权。

## Tradeoff

签名 cookie 没有服务端会话表，不能单点作废某一张 cookie。退出只清当前浏览器的 cookie。这个应用是单进程管理台，不做会话表。

默认口令是用户指定的弱口令，而且会作为种子出现在代码里。落盘只有哈希。本任务不做法登录锁定。

## Rollback

去掉 `before_request`、登录路由和登录页分支即可恢复当前开放行为。`admin.json` 与 `admin_secret.key` 是新文件，删除不影响账号和邮箱数据。
