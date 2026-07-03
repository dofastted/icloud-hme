# iCloud HME — 多账号聚合管理平台

基于 iCloud Hide My Email 协议，批量创建 `@icloud.com` 隐私邮箱的商用聚合平台。

- 👥 **多账号管理** — 同时管理多组 iCloud 账号，每个独立存储、独立会话
- 🔗 **账号-别名映射** — 自动提取真实 Apple ID，每个隐私邮箱标注归属账号
- ⏱ **定时调度** — 整点自动触发，多账号轮询创建，触达上限自动切下一个
- 🌐 **Web UI** — 暖色面板，仪表盘 + 账号列表 + 别名管理 + 跨账号批量创建

## 前提条件

- **iCloud+ 订阅**（Hide My Email 需要 iCloud+）
- Python 3.10+
- Windows / macOS / Linux

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动 Web UI
python web_ui.py

# 3. 打开 http://127.0.0.1:5050
#    点击左下角「导入 Cookie」添加第一个账号
#    支持粘贴 Cookie Editor 的 Header String 或 JSON
```

## 使用方式

### Web UI（推荐）

```bash
python web_ui.py                    # 启动 Web 界面
python web_ui.py --port 8080        # 指定端口
python web_ui.py --scheduler        # 启动 Web 后自动开启调度器
```

界面功能：

| 模块 | 功能 |
|------|------|
| **账号管理** | 添加/切换/删除账号，每个账号独立 Cookie + 会话 |
| **仪表盘** | 账号总数、总别名数、今日创建数，每账号一张状态卡片 |
| **别名列表** | 实时拉取所有别名，标注所属账号 + 真实邮箱 |
| **邮箱详情** | 搜索单个 HME 邮箱，查看最新邮件，按需展开正文 |
| **共享管理** | 为单个 HME 邮箱生成/吊销公网只读 shared 链接 |
| **批量创建** | 勾选目标账号 → 输入数量 → 跨账号轮询创建 |
| **调度器** | 一键启停，自动遍历活跃账号；单账号达到 750 个 HME 后跳过调度 |

### 开机自启

```bash
scripts/install-autostart-service.sh
```

脚本需要 systemd 正在运行；会安装并启用 `icloud-hme.service`，服务启动命令固定带 `--scheduler`，并设置 `AUTO_START_SCHEDULER=1`。系统重启或服务重启后，Web UI 和调度器会一起启动。

可选覆盖：

```bash
PORT=8080 HOST=0.0.0.0 SERVICE_NAME=icloud-hme.service scripts/install-autostart-service.sh
```

调度规则：只调度活跃账号；单账号本地/刷新后的 `alias_total >= 750` 时跳过，不再创建新 HME。


### 命令行调度器

```bash
# 多账号定时调度（需要先通过 Web UI 添加账号）
python scheduler.py

# 指定账号间间隔
python scheduler.py --interval 5

# 后台守护进程
python scheduler.py -d
```

### CLI 手动操作

```bash
# 列出所有别名
python icloud_hme.py list --cookies cookies.json

# 创建别名
python icloud_hme.py create -n 5 --cookies cookies.json

# 删除别名
python icloud_hme.py delete --email xxx@icloud.com --cookies cookies.json
```

### API Key 与接口

可在 Web UI 左侧 `API Key` 页面创建、保存、查看和吊销。首次创建 API Key 也可用命令行（仅无 key 时开放）：

```bash
curl -X POST http://127.0.0.1:5050/api/keys \
  -H 'Content-Type: application/json' \
  -d '{"name":"default"}'
```

后续请求使用 `Authorization: Bearer <api_key>` 或 `X-API-Key: <api_key>`。

核心接口：

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/config` | 主配置入口：返回 API base、鉴权方式、可用入口和 shared 主入口 |
| `GET` | `/api/v1/hme/available` | 用一把 API Key 全局列出可用 HME，跨账号聚合 |
| `GET` | `/api/v1/hme/available/next?include_latest=1` | 返回一个可用 HME，可选带最新邮件 |
| `GET` | `/api/v1/hme/{alias}/latest?force=0` | 通过一把 API Key 全局读取指定 HME 最新邮件 |
| `POST` | `/api/v1/accounts` | 导入 Cookie 并校验 iCloud 会话 |
| `POST` | `/api/v1/accounts/{id}/session/validate` | 重新校验登录会话 |
| `POST` | `/api/v1/accounts/{id}/mail-settings` | 配置接收邮箱 IMAP 登录并测试，用于读取 HME 转发邮件 |
| `POST` | `/api/v1/accounts/{id}/hme/generate` | 生成未预留候选别名，返回 `result.hme` |
| `POST` | `/api/v1/accounts/{id}/hme/reserve` | 预留候选别名，body 为 `hme,label,note` |
| `GET` | `/api/v1/accounts/{id}/aliases` | 列出别名，字段对齐 Apple HME |
| `POST` | `/api/v1/accounts/{id}/aliases/{anonymousId}/deactivate` | 停用别名 |
| `DELETE` | `/api/v1/accounts/{id}/aliases/{anonymousId}` | 删除别名 |
| `GET` | `/api/v1/mailboxes?q=&account_id=&status=` | 搜索/列出全部 HME 邮箱 |
| `GET` | `/api/v1/mailboxes/search?q=xxx` | 邮箱搜索快捷入口 |
| `GET` | `/api/v1/mailboxes/{alias}/messages?limit=1` | 读取指定 HME 邮箱邮件，默认最新一封 |
| `GET` | `/api/v1/mailboxes/{alias}/messages/{message_id}` | 读取指定邮件正文详情 |
| `POST` | `/api/v1/shared-mailboxes` | 为单个 HME 创建独立兑换码，body 为 `alias_email`，明文兑换码只返回一次 |
| `GET` | `/api/v1/shared-mailboxes` | 列出 shared 记录，只返回 prefix，不返回明文兑换码 |
| `POST` | `/api/v1/shared-mailboxes/{id}/revoke` | 吊销兑换码 |

全局调用推荐入口：

```bash
curl http://127.0.0.1:5050/api/v1/config \
  -H "Authorization: Bearer <api_key>"

curl "http://127.0.0.1:5050/api/v1/hme/available" \
  -H "Authorization: Bearer <api_key>"

curl "http://127.0.0.1:5050/api/v1/hme/available/next?include_latest=1" \
  -H "Authorization: Bearer <api_key>"

curl "http://127.0.0.1:5050/api/v1/hme/alias@icloud.com/latest?force=1" \
  -H "Authorization: Bearer <api_key>"
```

`/api/v1/hme/available` 只返回活跃账号下的活跃 HME；读取邮件不可用时，邮件字段返回 `null`，不会再要求旧的密码配置项。

管理端 UI 使用 `/api/mailboxes*` 与 `/api/shared*`，沿用本地管理端边界，不要求 API Key。不要把管理端口直接暴露到公网。

邮件读取依赖 HME 的接收邮箱。账号卡片中的“邮件登录”应填写接收邮箱、IMAP 服务器和邮箱授权码或密码；QQ 邮箱常用 `imap.qq.com`，163 邮箱常用 `imap.163.com`。未配置或登录失败时，HME 管理仍可用，但邮件内容读取会返回 `邮件读取暂不可用`。

别名对象字段对齐 Apple HME：`hme`、`label`、`note`、`isActive`、`createTimestamp`、`anonymousId`、`forwardToEmail`、`origin`。

### Shared 公网只读入口

公网只暴露一个共享主入口：

```text
/shared
```

公网域名可用环境变量配置：

```bash
SHARED_PUBLIC_BASE_URL=https://shared.example.com
# 或直接指定完整入口
SHARED_PUBLIC_URL=https://shared.example.com/shared
```

为单个 HME 邮箱生成独立兑换码：

```bash
curl -X POST http://127.0.0.1:5050/api/v1/shared-mailboxes \
  -H "Authorization: Bearer <api_key>" \
  -H "Content-Type: application/json" \
  -d '{"alias_email":"alias@icloud.com"}'
```

响应里的 `redemption_code` 只在创建时返回一次；`share_url` 是统一主入口。用户打开主入口后输入兑换码，提取邮箱并读取最新一封邮件。兼容旧链接：`/shared/<redemption_code>`。

程序读取或刷新最新一封邮件：

```bash
curl -X POST http://127.0.0.1:5050/api/shared/latest \
  -H "Content-Type: application/json" \
  -d '{"redemption_code":"shk_xxx","force":true}'
```

安全边界：

- 兑换码只存 SHA-256 摘要，`shared_mailboxes.json` 不保存明文兑换码。
- 吊销后，公网 HTML 和 JSON 接口统一返回 404，不区分无效、吊销或邮箱不存在。
- 公网 JSON 只返回白名单字段：`mailbox`、`label`、`message`、`fetched_at`、`cache_age_sec`；`message` 不含 `to`、账号、真实邮箱、cookies、内部异常。
- 公网接口有内存限流：同一兑换码每分钟 10 次，同一 IP 每分钟 30 次。多进程或反代部署建议再叠加网关限流。

## Cookie 获取

| 方式 | 说明 |
|------|------|
| Web UI 导入 | 点击左下角按钮，粘贴 Cookie Editor 的 Header String |
| Chrome 自动提取 | Windows 下 `python icloud_hme.py export-cookies` |
| 命令行 `--cookies` | 指定 JSON 文件路径 |

支持两种输入格式：
- **Header String**：`name1=value1; name2=value2; ...`
- **JSON**：`{"name1":"value1", "name2":"value2"}`

导入后自动持久化到 `accounts.json`，重启无需重新粘贴。

## 调度逻辑

```
每整点触发一轮
  → 遍历所有活跃账号
  → 每个账号创建到 iCloud 返回上限
  → 账号间间隔 3 秒（可配）
  → 全部完成后等待下一个整点
```

## 文件结构

```
├── icloud_hme.py        # 核心库：Cookie 提取 / HME API / 账号身份提取
├── account_manager.py   # 多账号管理器：CRUD / 批量创建 / 别名索引
├── mailbox_service.py   # 单邮箱搜索 / 邮件读取 / shared 公网视图
├── shared_mailboxes.py  # shared key 摘要存储与吊销
├── web_ui.py            # Flask Web 面板 + 内置调度器
├── templates/           # 管理页与公网 shared 页面
├── static/              # 管理页 CSS/JS 与 shared 样式
├── scheduler.py         # 独立命令行调度器
└── requirements.txt     # pip 依赖
```

运行时生成：

```
accounts.json          # 所有账号及 Cookie（自动持久化）
shared_mailboxes.json  # shared key 摘要、prefix、访问统计
scheduler_state.json   # 调度器历史状态
logs/                  # 运行日志
results/               # 创建的邮箱列表
```

## 依赖

```
requests>=2.25          # HTTP
pycryptodome>=3.15     # Chrome cookie 解密 (Windows)
pywin32>=305           # Windows DPAPI (仅 Windows)
flask>=3.0             # Web UI
```

## License

MIT
