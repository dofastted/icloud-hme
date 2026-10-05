# 实施顺序

不开始写产品代码，直到这份计划和 PRD 被明确批准。

1. 加 `admin_auth.py`：懒加载、`scrypt`、文件缺失时才种入默认账密、文件存在不覆盖。单测覆盖正确密码、错误密码、已有文件不被重置。
2. 在 `web_ui.py` 接 store、持久化 `secret_key`，加 `POST /api/login`、`POST /api/logout` 和 `before_request`。放行表按 `design.md`，不要用 `/api/shared` 前缀。
3. `/` 与 `/index.html` 未登录只渲染 `templates/login.html`。登录成功后刷新进入现有 `index.html`。顶栏加退出。
4. `.gitignore` 增加 `admin.json`、`admin_secret.key`。
5. `tests/conftest.py` 把凭证文件和 secret 指到 `tmp_path`。加 `login_admin(client)`，走真实 `POST /api/login`。
6. 跑 `python3 -m pytest tests`。新出现的 401，只给打到受保护 `/api/*` 的客户端登录。`tests/test_shared_public.py` 和 `/api/v1` 用例保持不登录。
7. 新增门禁用例，对应 PRD AC1–AC6：未登录 401、默认账密可进、错误账密失败、退出后再 401、v1 仍只认 key、公开兑换仍可用、`/api/shared` 列表和 revoke 未登录 401。

## 验证

```bash
python3 -m pytest tests
```

至少再看一条未登录 `GET /api/state` 是 401，以及一条不带管理员 cookie 的公开兑换仍是 200。

## 风险点

- `web_ui.py` 的 `before_request`：放行写宽会把吊销接口公开，写窄会弄断 v1 或兑换。
- 现有测试大量直接 `test_client()` 调 `/api/*`。禁止用 `app.testing` 或全局开关跳过鉴权。
- import `web_ui` 时不得写仓库根目录的 `admin.json`。

## 回滚点

第 2 步之后如果门禁范围错了，先停，不要继续改测试去迁就错误放行。回滚就是移除 gate 和登录分支；不迁移既有账号数据。
