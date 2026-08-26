# iCloud HME API 使用说明

本文面向调用方。默认服务地址示例为 `http://127.0.0.1:5050`，下文用变量表示：

```bash
BASE_URL=http://127.0.0.1:5050
API_KEY=hme_xxx
ALIAS=alias@icloud.com
ACCOUNT_ID=acc_xxx
```

## 鉴权边界

### 外部 API：`/api/v1/*`

`/api/v1/*` 必须使用 API Key。支持两种请求头：

```http
Authorization: Bearer <api_key>
```

或：

```http
X-API-Key: <api_key>
```

可在 Web UI 左侧 `API Key` 页面创建、保存、查看和吊销。首次创建 API Key 也可用命令行：

```bash
curl -X POST "$BASE_URL/api/keys" \
  -H 'Content-Type: application/json' \
  -d '{"name":"default"}'
```

响应中的 `api_key` 明文只返回一次；服务端只保存 SHA-256 摘要。

已有 API Key 后，创建、查看、吊销 key 需要鉴权：

```bash
curl "$BASE_URL/api/keys" \
  -H "Authorization: Bearer $API_KEY"

curl -X POST "$BASE_URL/api/keys/key_xxx/revoke" \
  -H "Authorization: Bearer $API_KEY"
```

### 管理端 API：`/api/*`

`/api/*` 是本地 Web UI 管理接口，默认不要求 API Key。不要直接暴露到公网。公网自动化调用请使用 `/api/v1/*`。

### 公网共享入口：`/shared` 与 `/api/shared/*`

公网共享只使用兑换码，不使用 API Key。共享接口只返回单个 HME 的最新一封邮件，响应字段经过白名单过滤。

## 外部 API 路由速查

| 方法 | 路径 | 用途 |
|------|------|------|
| `GET` | `/api/v1/config` | 获取 API 配置和入口 |
| `GET` | `/api/v1/client-config` | 获取同一份客户端配置 |
| `GET` | `/api/v1/hme/available` | 列出全局可用 HME |
| `GET` | `/api/v1/hme/available/next` | 取一个可用 HME |
| `GET` | `/api/v1/hme/{alias_email}/latest` | 读取指定 HME 最新邮件 |
| `POST` | `/api/v1/accounts` | 导入账号 Cookie |
| `GET` | `/api/v1/accounts` | 列出账号；响应附带邮箱分组摘要 |
| `POST` | `/api/v1/accounts/{id}/session/validate` | 校验账号会话 |
| `POST` | `/api/v1/accounts/{id}/mail-settings` | 配置接收邮箱邮件登录 |
| `GET` | `/api/v1/accounts/{id}/aliases` | 列出账号别名 |
| `POST` | `/api/v1/accounts/{id}/aliases` | 直接创建 HME |
| `POST` | `/api/v1/accounts/{id}/hme/generate` | 生成候选 HME |
| `POST` | `/api/v1/accounts/{id}/hme/reserve` | 预留候选 HME |
| `POST` | `/api/v1/accounts/{id}/aliases/{anonymousId}/deactivate` | 停用 HME |
| `DELETE` | `/api/v1/accounts/{id}/aliases/{anonymousId}` | 删除 HME |
| `GET` | `/api/v1/mailboxes` | 列出或搜索邮箱 |
| `GET` | `/api/v1/mailboxes/search` | 搜索邮箱快捷入口 |
| `GET` | `/api/v1/mailboxes/{alias}` | 查看邮箱详情 |
| `DELETE` | `/api/v1/mailboxes/{alias}` | 删除邮箱：本地缺 anonymousId 时会现场向 Apple 查一次；查不到返回 409，加 `?local_only=1` 才只清本地记录 |
| `GET` | `/api/v1/mailboxes/{alias}/messages` | 读取邮箱邮件列表 |
| `GET` | `/api/v1/mailboxes/{alias}/messages/{message_id}` | 读取邮件正文 |
| `POST` | `/api/v1/shared-mailboxes` | 创建共享兑换码 |
| `GET` | `/api/v1/shared-mailboxes` | 列出共享记录 |
| `POST` | `/api/v1/shared-mailboxes/{id}/revoke` | 吊销共享兑换码 |

## 推荐入口：全局配置

```bash
curl "$BASE_URL/api/v1/config" \
  -H "Authorization: Bearer $API_KEY"
```

同义入口：

```text
GET /api/v1/client-config
```

典型响应字段：

```json
{
  "ok": true,
  "api": {
    "version": "v1",
    "base_url": "http://127.0.0.1:5050/api/v1",
    "auth": {
      "type": "api_key",
      "headers": ["Authorization: Bearer <api_key>", "X-API-Key: <api_key>"],
      "key_prefix": "hme_xxx"
    },
    "entrypoints": {
      "available_hme": "/api/v1/hme/available",
      "next_hme": "/api/v1/hme/available/next",
      "hme_latest": "/api/v1/hme/{alias_email}/latest?force=0"
    }
  },
  "shared": {
    "entry_url": "http://127.0.0.1:5050/shared"
  },
  "capabilities": {
    "global_hme_lookup": true,
    "latest_mail": true,
    "refresh_mail": true,
    "redemption_codes": true
  }
}
```

## 全局 HME 调用

### 列出可用 HME

```bash
curl "$BASE_URL/api/v1/hme/available?limit=25&offset=0" \
  -H "Authorization: Bearer $API_KEY"
```

常用查询参数：

| 参数 | 说明 |
|------|------|
| `q` | 按邮箱、标签、账号名搜索 |
| `account_id` | 只看某个账号下的 HME |
| `limit` | 返回数量，默认 25，最大 100 |
| `offset` | 分页偏移 |
| `refresh=1` | 先从 iCloud 同步别名索引，再返回 |

响应示例：

```json
{
  "ok": true,
  "hme": [
    {
      "hme": "alias@icloud.com",
      "alias_email": "alias@icloud.com",
      "account_id": "acc_xxx",
      "account_name": "Main",
      "label": "Login",
      "created_at": "2026-07-03T12:00:00",
      "is_active": true,
      "can_read_mail": true,
      "mail_status": "available",
      "shared": null
    }
  ],
  "count": 1,
  "total": 1,
  "limit": 25,
  "offset": 0,
  "refreshed": false
}
```

语义：只返回活跃账号下的活跃 HME。邮件读取不可用时，不影响 HME 可用性；读取邮件的接口会返回 `message: null` 或明确错误。

### 取一个可用 HME

```bash
curl "$BASE_URL/api/v1/hme/available/next?include_latest=1" \
  -H "Authorization: Bearer $API_KEY"
```

参数：

| 参数 | 说明 |
|------|------|
| `include_latest=1` | 同时读取最新邮件 |
| `force=1` | 读取邮件时强制刷新 |
| `refresh=1` | 先刷新 HME 索引 |

响应中 `item` 是当前选中的 HME。若 `include_latest=1`，响应中可能包含 `latest_message`；读取不可用时为 `null`。

### 读取指定 HME 最新邮件

```bash
curl "$BASE_URL/api/v1/hme/$ALIAS/latest?force=0" \
  -H "Authorization: Bearer $API_KEY"
```

参数：

| 参数 | 说明 |
|------|------|
| `force=0` | 默认优先使用缓存 |
| `force=1` | 强制刷新邮件 |

响应示例：

```json
{
  "ok": true,
  "hme": "alias@icloud.com",
  "mailbox": {
    "alias_email": "alias@icloud.com",
    "account_id": "acc_xxx",
    "label": "Login"
  },
  "message": {
    "message_id": "123",
    "subject": "验证码",
    "from": "service@example.com",
    "date": "2026-07-03T12:00:00",
    "body_preview": "你的验证码是 123456"
  }
}
```

`message` 可以为 `null`，表示邮箱可用但当前邮件读取不可用或暂无邮件。

## 账号与别名管理 API

### 列出账号

```bash
curl "$BASE_URL/api/v1/accounts" \
  -H "Authorization: Bearer $API_KEY"
```

账号响应不会返回 `cookies`、邮件登录密码等敏感字段。`has_mail_config` 表示是否已配置邮件读取所需登录。

### 导入账号 Cookie

```bash
curl -X POST "$BASE_URL/api/v1/accounts" \
  -H "Authorization: Bearer $API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "name":"Main",
    "cookie_input":"X_APPLE_WEB_KB=...; SESSION_TOKEN=...",
    "host":"icloud.com"
  }'
```

字段：

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | 否 | 账号显示名 |
| `cookie_input` | 是 | Cookie Header String 或 JSON |
| `host` | 否 | 默认 `icloud.com` |

### 校验账号会话

```bash
curl -X POST "$BASE_URL/api/v1/accounts/$ACCOUNT_ID/session/validate" \
  -H "Authorization: Bearer $API_KEY"
```


### 配置接收邮箱邮件登录

HME 邮件会转发到账号的接收邮箱。读取邮件正文前，需要配置接收邮箱的 IMAP 登录。QQ 邮箱通常使用 `imap.qq.com`，163 邮箱通常使用 `imap.163.com`。

```bash
curl -X POST "$BASE_URL/api/v1/accounts/$ACCOUNT_ID/mail-settings" \
  -H "Authorization: Bearer $API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "email":"user@qq.com",
    "host":"imap.qq.com",
    "port":993,
    "password":"mail-auth-code"
  }'
```

该接口会保存配置并立即测试登录。响应不会返回明文密码。未配置或登录失败时，HME 管理仍可用，邮件读取接口会返回 `邮件读取暂不可用`。
### 列出账号别名

```bash
curl "$BASE_URL/api/v1/accounts/$ACCOUNT_ID/aliases" \
  -H "Authorization: Bearer $API_KEY"
```

别名字段对齐 Apple HME，常见字段：`hme`、`label`、`note`、`isActive`、`createTimestamp`、`anonymousId`、`forwardToEmail`、`origin`。

### 生成候选 HME

```bash
curl -X POST "$BASE_URL/api/v1/accounts/$ACCOUNT_ID/hme/generate" \
  -H "Authorization: Bearer $API_KEY"
```

该接口只生成候选值，不预留。

### 预留候选 HME

```bash
curl -X POST "$BASE_URL/api/v1/accounts/$ACCOUNT_ID/hme/reserve" \
  -H "Authorization: Bearer $API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "hme":"alias@icloud.com",
    "label":"Login",
    "note":"created by api"
  }'
```

`hme` 必填。

### 直接创建 HME

```bash
curl -X POST "$BASE_URL/api/v1/accounts/$ACCOUNT_ID/aliases" \
  -H "Authorization: Bearer $API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "count": 3,
    "label":"Batch",
    "note":"created by api"
  }'
```

说明：

- `count` 最大 50。
- 当前调度规则中，单账号达到 750 个 HME 后会跳过自动调度；手动 API 创建仍以 Apple/iCloud 返回结果为准。

### 停用或删除 HME

停用：

```bash
curl -X POST "$BASE_URL/api/v1/accounts/$ACCOUNT_ID/aliases/$ANONYMOUS_ID/deactivate" \
  -H "Authorization: Bearer $API_KEY"
```

删除：

```bash
curl -X DELETE "$BASE_URL/api/v1/accounts/$ACCOUNT_ID/aliases/$ANONYMOUS_ID" \
  -H "Authorization: Bearer $API_KEY"
```

## 邮箱管理 API

### 搜索/列出邮箱

```bash
curl "$BASE_URL/api/v1/mailboxes?q=login&limit=50&offset=0" \
  -H "Authorization: Bearer $API_KEY"
```

同义搜索入口：

```text
GET /api/v1/mailboxes/search?q=login
```

查询参数：

| 参数 | 说明 |
|------|------|
| `q` | 邮箱、标签、账号名搜索 |
| `account_id` 或 `account` | 限定账号 |
| `group_id` 或 `group` | 限定邮箱分组 |
| `status` | 限定状态 |
| `limit` | 默认 50，最大 100 |
| `offset` | 分页偏移 |
| `refresh=1` | 先远端同步 HME 索引 |

### 查看单个邮箱详情

```bash
curl "$BASE_URL/api/v1/mailboxes/$ALIAS" \
  -H "Authorization: Bearer $API_KEY"
```

### 读取邮箱邮件列表

```bash
curl "$BASE_URL/api/v1/mailboxes/$ALIAS/messages?limit=1&force=0" \
  -H "Authorization: Bearer $API_KEY"
```

参数：

| 参数 | 说明 |
|------|------|
| `limit` | 1-10，默认 1 |
| `force=1` | 强制刷新邮件 |

### 读取邮件正文详情

```bash
curl "$BASE_URL/api/v1/mailboxes/$ALIAS/messages/$MESSAGE_ID" \
  -H "Authorization: Bearer $API_KEY"
```

## 共享兑换码 API

### 创建兑换码

```bash
curl -X POST "$BASE_URL/api/v1/shared-mailboxes" \
  -H "Authorization: Bearer $API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"alias_email":"alias@icloud.com"}'
```

响应示例：

```json
{
  "ok": true,
  "shared": {
    "id": "sh_...",
    "alias_email": "alias@icloud.com",
    "account_id": "acc_xxx",
    "prefix": "shk_abcd"
  },
  "redemption_code": "shk_xxx",
  "share_key": "shk_xxx",
  "share_url": "http://127.0.0.1:5050/shared",
  "legacy_share_url": "http://127.0.0.1:5050/shared/shk_xxx"
}
```

注意：

- `redemption_code` / `share_key` 明文只返回一次。
- `share_url` 是统一共享主入口。
- `legacy_share_url` 兼容旧的 `/shared/<code>` 链接。

### 列出共享记录

```bash
curl "$BASE_URL/api/v1/shared-mailboxes" \
  -H "Authorization: Bearer $API_KEY"
```

只返回记录和兑换码前缀，不返回明文兑换码。

### 吊销兑换码

```bash
curl -X POST "$BASE_URL/api/v1/shared-mailboxes/$SHARE_ID/revoke" \
  -H "Authorization: Bearer $API_KEY"
```

吊销后，公网 HTML 和 JSON 统一返回 404。

## 公网共享只读 API

### 打开共享主入口

```text
GET /shared
```

用户在页面输入兑换码后读取最新邮件。

兼容旧入口：

```text
GET /shared/<redemption_code>
```

### 使用兑换码读取最新邮件

```bash
curl -X POST "$BASE_URL/api/shared/latest" \
  -H 'Content-Type: application/json' \
  -d '{"redemption_code":"shk_xxx","force":false}'
```

同义入口：

```text
GET|POST /api/shared/redeem
GET /api/shared/<redemption_code>/latest?force=0
```

响应示例：

```json
{
  "ok": true,
  "mailbox": "alias@icloud.com",
  "label": "Login",
  "message": {
    "message_id": "123",
    "subject": "验证码",
    "from": "service@example.com",
    "date": "2026-07-03T12:00:00",
    "body": "你的验证码是 123456"
  },
  "fetched_at": "2026-07-03T12:00:00",
  "cache_age_sec": 10
}
```

安全边界：

- 只返回最新一封邮件。
- 不返回账号、真实邮箱、cookies、内部异常。
- 无效、吊销、邮箱不存在统一返回 404。
- 内置限流：同一兑换码每分钟 10 次，同一 IP 每分钟 30 次。公网部署建议叠加网关限流。

## 本地管理端接口

以下接口服务 Web UI，默认不鉴权，只能放在本地或受控管理网：

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/state` | 当前运行状态、账号和别名统计 |
| `GET` | `/api/accounts` | 本地账号列表和邮箱分组摘要，敏感字段已过滤 |
| `POST` | `/api/accounts/add` | 导入账号 Cookie |
| `GET` | `/api/groups` | 获取本地邮箱分组列表，默认内置 `可用`、`不可用`、`废弃` |
| `POST` | `/api/groups` | 创建自定义邮箱分组 |
| `PUT` | `/api/groups/{id}` | 更新自定义邮箱分组 |
| `DELETE` | `/api/groups/{id}` | 删除自定义邮箱分组，邮箱回到“可用”分组 |
| `PUT` | `/api/groups/reorder` | 调整自定义分组顺序 |
| `POST` | `/api/mailboxes/batch-update-group` | 批量移动 HME 邮箱到指定分组 |
| `POST` | `/api/accounts/{id}/remove` | 删除账号 |
| `POST` | `/api/accounts/{id}/validate` | 校验账号会话 |
| `POST` | `/api/accounts/{id}/create` | 单账号创建 HME |
| `POST` | `/api/create-batch` | 多账号批量创建 HME |
| `GET` | `/api/mailboxes` | 本地邮箱列表 |
| `GET` | `/api/mailboxes/{alias}` | 本地邮箱详情 |
| `GET` | `/api/mailboxes/{alias}/messages` | 本地读取邮件列表 |
| `POST` | `/api/mailboxes/{alias}/share` | 本地创建兑换码 |
| `GET` | `/api/shared` | 本地共享记录列表 |
| `POST` | `/api/shared/{id}/revoke` | 本地吊销兑换码 |
| `POST` | `/api/scheduler/start` | 启动调度器 |
| `POST` | `/api/scheduler/stop` | 停止调度器 |
| `GET` | `/api/log-stream` | Server-Sent Events 日志流 |

## 常见错误

| HTTP 状态 | 示例响应 | 说明 |
|----------|----------|------|
| `400` | `{"ok":false,"error":"..."}` | 参数缺失或请求不可执行 |
| `401` | `{"ok":false,"error":"invalid API key"}` | API Key 缺失或无效 |
| `404` | `{"ok":false,"error":"not found"}` | 资源不存在；共享吊销也返回 404 |
| `502` | `{"ok":false,"error":"..."}` | 后端或远端服务不可用 |

## 最小调用流程

### 获取一个可用 HME 并读取最新邮件

```bash
curl "$BASE_URL/api/v1/hme/available/next?include_latest=1" \
  -H "Authorization: Bearer $API_KEY"
```

### 搜索指定 HME 并读取邮件

```bash
curl "$BASE_URL/api/v1/mailboxes/search?q=login" \
  -H "Authorization: Bearer $API_KEY"

curl "$BASE_URL/api/v1/mailboxes/$ALIAS/messages?limit=1&force=1" \
  -H "Authorization: Bearer $API_KEY"
```

### 创建共享兑换码并交给用户

```bash
curl -X POST "$BASE_URL/api/v1/shared-mailboxes" \
  -H "Authorization: Bearer $API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"alias_email":"alias@icloud.com"}'
```

把响应里的 `share_url` 和 `redemption_code` 提供给用户。用户通过 `/shared` 输入兑换码，只能看到该 HME 的最新一封邮件。
