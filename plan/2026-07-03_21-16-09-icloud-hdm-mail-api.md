---
mode: plan
cwd: /mnt/x/project/icloud-hme
task: iCloud HDM/HME 单邮箱 API、共享查看与管理页面（含完整 UI 设计）
complexity: complex
tool: claude-code-fable
created_at: 2026-07-03T21:16:15+08:00
updated_at: 2026-07-03T21:45:24+08:00
---

# Plan: iCloud HDM/HME 单邮箱 API、共享查看与管理页面（含完整 UI 设计）

## Goal
- 在当前 Flask/Python 项目内交付三层能力：
  1. **单邮箱 API 调度**：受 API Key 保护的 `/api/v1` 接口，支持搜索邮箱、拉取指定邮箱邮件（默认最新一封）、获取邮件详情。
  2. **shared 公网只读入口**：每个 HME 邮箱可生成唯一 shared key，凭 key 公网访问 `/shared/<key>` 查看该邮箱最新一封邮件，key 是唯一鉴权凭据。
  3. **邮箱管理 UI**：账号级管理 → 全部 HME 邮箱列表 → 单邮箱详情页 → shared key 管理弹窗，UI 借鉴 `/mnt/x/project/outlookEmail` 的模板/JS 拆分与三栏信息架构，但保留本项目 paper/ink/mono 暖纸质视觉。
- 同步完成安全边界：shared key 只存 SHA-256 摘要、可吊销、限流 + 缓存、响应字段脱敏。

## Scope
- In:
  - 新模块 `shared_mailboxes.py`（SharedMailboxStore）、`mailbox_service.py`（单邮箱查询服务）。
  - `web_ui.py` 新增管理端 `/api/mailboxes*`、外部 `/api/v1/mailboxes*`、`/api/v1/shared-mailboxes*`、公网 `/shared/<key>` 与 `/api/shared/<key>/latest`。
  - UI 骨架拆分：`UI_HTML` 内联字符串 → `templates/index.html` + `templates/shared.html` + `static/css/app.css` + `static/js/` 编号模块（借鉴 outlookEmail 的 `static/js/index/NN-*.js` 模式）。
  - 管理端新增两个导航视图（邮箱列表升级版、共享管理）、一个动态详情视图、一个 shared 创建/吊销弹窗。
  - 内存限流器（per-key + per-IP）与 shared 缓存策略。
  - 目标单测 + 回归测试 + 手动验证脚本。
- Out:
  - 不改 iCloud HME 创建协议（`icloud_hme.py`）与调度器创建逻辑（`scheduler.py`、`_scheduler_loop`）。
  - 不引入数据库，沿用 JSON 文件持久化（对齐 `api_keys.json` / `accounts.json` 模式）。
  - 不做 outlookEmail 的分组、OAuth、转发、临时邮箱供应商等能力。
  - 不做公网写操作、附件下载、多封 shared 邮件列表、shared 页自动轮询。
  - 不解决 `accounts.json` 已明文存 App 专用密码的历史问题（记入 Risks）。

## Assumptions / Dependencies
- 用户所说 HDM = 本项目 HME（Hide My Email），单邮箱 = 单个 HME 别名地址（如 `xxx@icloud.com`）。
- 邮件读取依赖账号已配置 `icloud_email` + App 专用密码（`AccountManager.get_mail_client`，`account_manager.py:313`）；未配置时 shared/API 必须返回可定位错误而非空列表。
- 别名与账号的归属关系来源于两处：本地创建记录 `results/latest_emails.txt`（`/api/emails`）与云端 `get_all_aliases()`（`account_manager.py:583`）；`mailbox_service` 需要合并两者做别名→账号解析。
- 公网部署方式（反代/端口映射）由用户自理；本计划只保证 `/shared/*` 路由自身的鉴权、限流与脱敏正确。
- Flask 与 waitress 沿用现有版本，不新增第三方依赖（限流用内存实现，不引入 flask-limiter）。

## Phases

### Phase 1 — 数据契约与 SharedMailboxStore 存储层
- **步骤**
  - 定义三份 JSON 契约（写入 `mailbox_service.py` 模块 docstring，后续同步 README）：
    - `MailboxSummary`：`alias_email`、`account_id`、`account_name`、`label`、`is_active`、`created_at`、`shared`（`null` 或 `{id, prefix, active, created_at, last_accessed_at, access_count}`）。
    - `MessageSummary`：`message_id`、`subject`、`from`、`to`、`date`、`body_preview`（≤200 字符纯文本）。
    - `SharedPublicView`（公网专用）：`mailbox`（仅 alias 地址）、`label`、`message`（`MessageSummary` 去掉 `to`，正文为 `body` 纯文本全文）、`fetched_at`、`cache_age_sec`。**白名单序列化**：只允许上述字段输出，新增字段默认不进公网响应。
  - 新建 `shared_mailboxes.py`，仿照 `api_keys.py:21` 的 `APIKeyStore` 实现 `SharedMailboxStore`，持久化到 `shared_mailboxes.json`：
    - 记录字段：`id`（`shr_` + hex8）、`account_id`、`alias_email`（小写归一）、`prefix`（明文前 12 位）、`sha256`、`active`、`created_at`、`revoked_at`、`last_accessed_at`、`access_count`。
    - 明文 key 格式 `shk_` + `secrets.token_urlsafe(24)`，仅 `create()` 返回一次；文件只存摘要，`verify()` 用 `hmac.compare_digest`。
    - 方法：`create(account_id, alias_email)`（同一别名已有 active 记录时抛 `ValueError`，先吊销再建）、`verify(raw_key)`（命中后更新 `last_accessed_at`/`access_count`）、`revoke(share_id)`、`list()`、`get_for_alias(alias_email)`。
- **验证**：`python -m pytest tests/test_shared_store.py`（用 `tmp_path` 隔离 JSON 文件），覆盖创建/校验/吊销/重复创建/明文只出现一次。

### Phase 2 — mailbox_service 单邮箱查询服务
- **步骤**
  - 新建 `mailbox_service.py`，构造时注入 `AccountManager` 实例，提供：
    - `list_mailboxes(q="", account_id="", status="") -> List[MailboxSummary]`：合并 `latest_emails.txt` 本地记录 + `MailCache`/云端别名缓存，按 `alias_email` 去重，附加 `SharedMailboxStore.get_for_alias` 的 shared 状态；`q` 匹配 alias 地址/label/账号名（不区分大小写）。
    - `resolve_alias(alias_email) -> (account, alias_meta)`：找不到抛 `MailboxNotFound(KeyError)`。
    - `get_latest_message(alias_email, force=False) -> Optional[MessageSummary]`：内部走 `AccountManager.check_alias_mail(..., limit=1)`（`account_manager.py:360`），取时间最新一封；`force=True` 跳缓存。
    - `get_message_detail(alias_email, message_id) -> Dict`：校验该 message 属于该 alias 后调 `get_mail_client().fetch_full`（`icloud_mail.py:224`）。
  - 定义异常层级：`MailboxNotFound`、`IMAPNotConfigured(ValueError)`、`IMAPUnavailable(RuntimeError)`——分别被现有 errorhandler（`web_ui.py:32`）映射为 404/400/502。修掉本链路上 `check_alias_mail` 静默吞 IMAP 异常表现为空列表的问题：service 层区分「无邮件」与「读取失败」。
- **验证**：`python -m pytest tests/test_mailbox_service.py`，monkeypatch `AccountManager.get_mail_client` 用 FakeMail，覆盖：搜索命中/未命中、latest 只返回一封、IMAP 未配置抛 `IMAPNotConfigured`、IMAP 异常抛 `IMAPUnavailable`。

### Phase 3 — 外部 API（API Key 鉴权，`/api/v1`）
- **步骤**：在 `web_ui.py` 现有 `/api/v1` 区块（`web_ui.py:192` 之后）追加，全部挂 `@_require_api_key`：
  - `GET /api/v1/mailboxes?q=&account_id=&status=&limit=50&offset=0` → `{ok, mailboxes:[MailboxSummary], count, total}`。
  - `GET /api/v1/mailboxes/search?q=xxx` → 同上（语义化别名路由，内部同一实现）。
  - `GET /api/v1/mailboxes/<alias_email>/messages?limit=1&force=0` → `{ok, messages:[MessageSummary], count}`；`limit` 上限 10，默认 1；允许 `force=1`。
  - `GET /api/v1/mailboxes/<alias_email>/messages/<message_id>` → `{ok, message:{...MessageSummary, body}}`。
  - `POST /api/v1/shared-mailboxes`（body `{alias_email}`）→ `{ok, shared:{id, prefix, alias_email, created_at}, share_key, share_url}`；`share_key` 明文仅此一次。
  - `GET /api/v1/shared-mailboxes` → 列表（只含 prefix，无明文）。
  - `POST /api/v1/shared-mailboxes/<share_id>/revoke` → `{ok}`。
- **鉴权边界**：`/api/v1/*` 一律 API Key（Bearer / X-API-Key，`api_keys.py:112`）；旧管理端 `/api/*`（无 Key，供本地 UI）不变，但**新增管理端路由不得复用 `/api/v1` 前缀**，两套边界在 API 文档页明确标注。
- **验证**：`python -m pytest tests/test_api_v1_mailboxes.py`，Flask test client：无 Key→401、有效 Key→200、`limit` 默认 1、创建 shared 响应含明文且再查列表无明文。

### Phase 4 — 公网 shared 入口（key 唯一鉴权 + 限流 + 缓存）
- **步骤**
  - 新增内存限流器 `_RateLimiter`（`web_ui.py` 内部小类）：滑动窗口，规则 `shared_key: 10 次/分钟`、`ip: 30 次/分钟`，超限返回 429 `{ok:false,error:"rate limited"}`；IP 取 `X-Forwarded-For` 首段回退 `remote_addr`。
  - `GET /api/shared/<key>/latest`（**无 API Key，无 Session，key 即鉴权**）：
    1. 限流检查 → 2. `SharedMailboxStore.verify(key)`，无效/已吊销/绑定邮箱已删 **统一返回 404** `{"ok":false,"error":"not found"}`（防枚举，不区分原因）→ 3. `mailbox_service.get_latest_message(alias, force=False)`——公网**永远禁止 `force`**，缓存 5 分钟内直接回缓存（沿用 `MailCache` age 判断）→ 4. 按 `SharedPublicView` 白名单序列化返回。
    - `IMAPNotConfigured` 对公网降级为 200 + `{message:null, note:"mailbox not ready"}`，不暴露内部配置细节。
  - `GET /shared/<key>`：返回 `templates/shared.html`（key 无效同样 404 页面）；页面 JS 只调 `/api/shared/<key>/latest`。
  - **脱敏红线**（写入实现注释与测试断言）：公网响应与 HTML 中禁止出现 `account_id`、`account_name`、`real_email`、`icloud_email`、`anonymousId`、`cookies`、`app_password`、API Key 记录、服务器路径、Python 异常文本。
- **验证**：`python -m pytest tests/test_shared_public.py`：有效 key→200 且仅一封；吊销后→404；无效 key→404 且响应体与吊销 key 一致；连续 11 次→429；响应 JSON 断言不含脱敏红线字段。

### Phase 5 — UI 骨架拆分（templates + static）
- **步骤**（借鉴 outlookEmail：`templates/index.html` 62 行壳 + partials + `static/js/index/NN-*.js` 编号模块）
  - 把 `web_ui.py:166` 的 `UI_HTML` 拆为：
    - `templates/index.html`：文档壳，引 CSS/JS。
    - `static/css/app.css`：现有全部样式原样迁出，保留 `--paper/--ink/--red/--green/--mono` 设计 token；新增 `skeleton`（加载骨架）、`badge-shared`、`breadcrumb`、`error-box` 少量类。
    - `static/js/01-core.js`（`api()`/`apiSlow()`/`esc()`/`toast()`/tab 路由/SSE）、`02-accounts.js`（sidebar/仪表盘/账号弹窗）、`03-mailboxes.js`（邮箱列表视图）、`04-mailbox-detail.js`（单邮箱详情视图）、`05-shared.js`(共享管理视图+弹窗)、`06-inbox-batch.js`（收件箱/批量创建，现状迁移）、`07-docs-logs.js`（API 文档/日志，现状迁移）。
  - `index()` 路由改 `render_template("index.html")`；行为零变更，纯迁移。
  - tab 路由升级为 hash 路由（`#/mailboxes`、`#/mailbox/<alias>`、`#/shared`），支持刷新/直达/返回。
- **验证**：`python web_ui.py --port 5050 --no-sync` 启动后，仪表盘/邮箱列表/批量创建/收件箱/文档/日志六个旧视图行为与拆分前一致；浏览器 console 无报错。

### Phase 6 — 管理端 UI：账号级 → 邮箱列表 → 单邮箱详情 → shared 管理
- **整体布局与导航**：保留左 260px sidebar + 右主区。sidebar 导航项定为：`仪表盘`、`邮箱列表`、`共享管理`（新）、`批量创建`、`收件箱`、`API 文档`、`运行日志`；下方账号列表与调度器开关不变。
- **账号级视图（仪表盘增强）**：每张账号卡（`renderDashboard`）新增「查看邮箱」按钮 → 跳 `#/mailboxes?account=<acc_id>` 并预置筛选；账号卡增加 `IMAP 已配置/未配置` 徽标（有无 `app_password`），未配置时点击直接打开现有 `showAppPwdModal`。
- **全部邮箱列表 `#/mailboxes`**（改造现有 `view-emails`，数据源从 `/api/emails` 换成新 `GET /api/mailboxes`）：
  - 工具栏：搜索输入框（300ms 防抖，match 地址/标签/账号）、账号 select、状态 select（全部/活跃/停用）、`云端同步`、`CSV`、`复制全部` 按钮（沿用）。
  - 表格列：`#`、`邮箱地址`（点击进详情）、`所属账号`、`标签`、`状态`、`共享`（无 → `--`；有 → 红色菱形 `SHARED` 徽标+prefix）、`操作`（详情 / 复制 / 共享）。
  - 三态：加载=6 行灰色 skeleton 表格行；空=现有 empty 样式 + 文案「暂无邮箱 — 去仪表盘或批量创建生成」+ CTA 按钮跳批量创建；错误=`error-box`（红边框、错误摘要、`重试` 按钮）。
- **单邮箱详情 `#/mailbox/<alias_email>`**（新视图 `view-mailbox-detail`，数据源 `GET /api/mailboxes/<alias>` + `GET /api/mailboxes/<alias>/messages?limit=1`）：
  - 面包屑：`邮箱列表 / xxx@icloud.com`（前段可点回列表）。
  - 头部卡：邮箱地址（大号 mono + 复制按钮）、归属账号（点击跳仪表盘该卡）、标签、活跃徽标、创建时间。
  - 「最新邮件」panel：主题/发件人/收件人/时间 + 正文（`fetch` 详情接口按需展开，纯文本渲染）；右上 `刷新`（走缓存）与 `强制刷新`（`force=1`）两个按钮；缓存年龄小字提示（复用 `cacheStatus` 模式）。三态：加载 skeleton / 空=「暂无邮件」/ 错误=分类文案（IMAP 未配置 → 内嵌「去设置应用密码」按钮打开 `showAppPwdModal`；IMAP 失败 → 错误摘要+重试）。
  - 「共享访问」panel：无 shared → 说明文案 + `生成共享链接` 按钮；有 shared → prefix、创建时间、最近访问、访问次数、`复制公网链接`（仅路径提示 `/shared/shk_xxxx…`，因明文不可再现，复制按钮只在创建弹窗内可用）、红色 `吊销` 按钮。
- **shared 管理弹窗**（`05-shared.js`，复用 `modal-overlay/modal-box` 样式）：
  - 创建流程：点「生成共享链接」→ 弹窗确认（说明「持有链接者可公网查看该邮箱最新一封邮件」）→ 调 `POST /api/mailboxes/<alias>/share` → 成功态在弹窗内一次性展示完整 URL（`location.origin + /shared/<key>`）+ `复制链接` 按钮 + 红字警示「明文 key 仅此一次显示，关闭后无法找回，只能吊销重建」。
  - 吊销流程：`confirm` 二次确认 → `POST /api/shared/<id>/revoke` → toast + 刷新面板。
- **共享管理视图 `#/shared`**（新 `view-shared`，数据源 `GET /api/shared`）：全部 shared key 表格（邮箱地址、prefix、状态 active/revoked、创建时间、最近访问、访问次数、操作=进详情/吊销）；同样三态。
- **管理端配套路由**（`web_ui.py`，无 API Key，与旧 `/api/*` 同边界）：`GET /api/mailboxes`、`GET /api/mailboxes/<alias>`、`GET /api/mailboxes/<alias>/messages`、`GET /api/mailboxes/<alias>/messages/<mid>`、`POST /api/mailboxes/<alias>/share`、`GET /api/shared`、`POST /api/shared/<id>/revoke`——全部薄封装 `mailbox_service` + `SharedMailboxStore`。
- **API 文档页**：`renderDocs` 的 sections 增加「邮箱 (v1, API Key)」「共享邮箱 (v1, API Key)」「公网 shared (仅 key)」三组，并加一段三层鉴权边界说明。
- **验证**：手动路径走查——仪表盘→查看邮箱→筛选→进详情→刷新最新邮件→创建 shared→复制链接→浏览器隐身窗打开 `/shared/<key>` 可见→回详情吊销→隐身窗刷新变 404。

### Phase 7 — 公网 shared 页面 `templates/shared.html`
- **步骤**：独立极简页面，不引用管理端任何 JS/CSS 文件（内联 <style>+<script> 或独立 `static/css/shared.css`），移动优先：
  - 视觉：复用 paper/ink token 精简版；居中单列卡片 `max-width:560px`；顶部红色菱形 + `SHARED MAILBOX` mono 标题。
  - 内容区：邮箱地址（标题，mono）、`最新一封邮件` 卡片（主题加粗 / 发件人 / 时间 / 正文纯文本 `white-space:pre-wrap`，用 `textContent` 注入防 XSS）、`刷新` 按钮（禁用 3 秒防连点）、`fetched_at`/缓存年龄小字、页脚「只读共享视图 · 仅展示最新一封邮件」。
  - 状态：加载=居中 spinner 文案「读取中…」；无邮件=「该邮箱暂无邮件，稍后刷新查看」；429=「访问过于频繁，请稍后再试」；key 失效（fetch 404 或服务端直接 404 渲染）=「链接不存在或已失效」+ 不提供任何进一步信息。
- **验证**：`python -m pytest tests/test_shared_public.py::test_shared_page_*`（200/404 两个 HTML 断言）+ 手机宽度 375px 手动检查。

### Phase 8 — 文档、回归与收尾
- README：API 表格补三层接口、shared 使用示例（curl + 浏览器）、安全边界段落（摘要存储/吊销/限流/脱敏）、文件结构补 `shared_mailboxes.json`、`templates/`、`static/`。
- `.gitignore` 增加 `shared_mailboxes.json`（与 `api_keys.json` 同策略，确认现状后对齐）。
- 全量回归：`python tests/test_regressions.py` + `python -m pytest tests/ -q`。

## Tests & Verification
- 新增测试文件（pytest，均用 `tmp_path`/monkeypatch 隔离，不触真实 IMAP/iCloud）：
  - `tests/test_shared_store.py`：创建/校验/吊销/重复创建冲突/明文不落盘（读 JSON 文件断言无 `shk_` 前缀完整 key）。
  - `tests/test_mailbox_service.py`：列表合并去重、搜索过滤、latest 单封、异常分类（NotFound/NotConfigured/Unavailable）。
  - `tests/test_api_v1_mailboxes.py`：401/200、limit 钳制、shared 创建-列表-吊销全流程、明文只出现一次。
  - `tests/test_shared_public.py`：有效/吊销/伪造 key 响应一致性（404 防枚举）、限流 429、脱敏字段断言、`/shared/<key>` HTML 200/404。
- 既有回归：`python tests/test_regressions.py`（`tests/test_regressions.py:186` 已有 APIKeyStore 隔离测试可作模板）。
- 手动验证：`python web_ui.py --port 5050 --no-sync` → Phase 6/7 的走查清单；`curl -H "X-API-Key: ..."` 打 4 条 v1 接口；隐身窗验证 shared 页三种状态。

## Issue CSV
- Path: `issues/2026-07-03_21-16-09-icloud-hdm-mail-api.csv`
- 进入执行模式时按 Phase 1–8 拆行生成（每行含 phase、模块、验证命令）；本次仅重写 plan，不生成 CSV。

## Tools / MCP
- `read`/`grep`：定位 `web_ui.py` 路由区块与 `UI_HTML` 拆分边界。
- `edit`/`write`：Phase 5 拆分时用 `write` 建新文件、`edit` 收缩 `web_ui.py`。
- `codex`（gpt-5.5 high）：Phase 5 大体量 UI 字符串迁移与 Phase 6 JS 模块编写可外包，Claude 负责契约与验收。
- `ui-ux-pro-max`：Phase 6/7 完成后做一次 UI 审查（状态完备性 + 移动端）。
- `chrome-devtools` MCP（按需）：shared 页 375px 视口与 console 校验。

## Acceptance Checklist
- [ ] `GET /api/v1/mailboxes` / `/search` 能按关键字、账号、状态列出与搜索全部 HME 邮箱（API Key 鉴权）。
- [ ] `GET /api/v1/mailboxes/<alias>/messages` 默认只返回最新一封；`/<message_id>` 可取正文详情。
- [ ] shared key 可经 UI 与 `/api/v1/shared-mailboxes` 创建、吊销；`shared_mailboxes.json` 中只有 SHA-256 摘要与 prefix。
- [ ] 公网 `/shared/<key>` 与 `/api/shared/<key>/latest` 仅凭 key 访问，只返回最新一封邮件；无效/吊销 key 统一 404。
- [ ] 公网响应与页面不含 `account_id`、真实邮箱、cookies、App 密码、内部路径、异常堆栈。
- [ ] 公网入口有 per-key 与 per-IP 限流，且不允许 `force` 穿透缓存。
- [ ] 管理页：仪表盘账号卡可跳邮箱列表；邮箱列表支持搜索/筛选并可点进单邮箱详情；详情页可看最新邮件、刷新/强制刷新、创建与吊销 shared。
- [ ] 邮箱列表、单邮箱详情、共享管理、shared 公网页均实现 loading/empty/error 三态。
- [ ] shared 创建弹窗一次性展示明文链接并带「不可找回」警示；关闭后任何接口不再返回明文。
- [ ] `python tests/test_regressions.py` 与 `python -m pytest tests/ -q` 全部通过。

## Risks / Blockers
- **公网面暴露**：`/shared/*` 是无账号公网入口，限流为内存实现，多进程部署（waitress threads 单进程可用）或重启后计数清零——记录为已知限制，反代层可叠加限流。
- **明文密钥历史问题**：`accounts.json` 明文存 cookies 与 App 专用密码（`account_manager.py:308`），本计划不改造，但 shared/v1 响应必须持续脱敏。
- **管理端 `/api/*` 无鉴权**：现状即如此（仅供本地 UI）；新增管理端路由沿用该边界，README 需明示「管理端口不得直接暴露公网」。
- **IMAP 慢与限流**：`check_alias_mail` 单次 IMAP 搜索可达数秒；shared 强制走 5 分钟缓存 + 限流即为兜底，仍需观察 Apple 侧限流表现。
- **UI 拆分回归面大**：`UI_HTML` 约 3000 行等价内容一次迁出，Phase 5 必须"零行为变更"独立提交，出问题可整体回退该 commit。
- **别名归属解析不全**：仅存在于云端而无本地记录的别名依赖 `get_all_aliases()` 实时拉取，账号 Cookie 失效时列表会缺项——UI 错误态需提示「账号会话失效，请校验」。

## Rollback / Recovery
- shared 功能：吊销单条记录即可切断对应公网访问；整体回滚删除 `shared_mailboxes.json` + 回退路由 commit。
- 新增 `/api/v1/mailboxes*` 与管理端 `/api/mailboxes*` 均为纯新增路由，独立 revert 不影响既有 `/api/accounts`、`/api/emails`、`/api/v1/accounts/*`。
- UI：Phase 5（拆分）与 Phase 6（新视图）分开提交；Phase 6 出错回退后旧视图仍可用；Phase 5 出错整体 revert 恢复内联 `UI_HTML`。
- IMAP 异常时管理端继续回 `MailCache` 缓存数据，但响应带 `error` 标记；公网入口降级为 `message:null`。

## Checkpoints
- Commit after Phase 1+2: `feat: shared mailbox store + mailbox service (with tests)`。
- Commit after Phase 3+4: `feat: v1 mailbox/shared APIs + public shared endpoint (rate-limited)`。
- Commit after Phase 5: `refactor: extract inline UI into templates/static (no behavior change)`。
- Commit after Phase 6+7: `feat: mailbox list/detail/shared management UI + public shared page`。
- Commit after Phase 8: `docs+test: README API/security sections, full regression green`。

## References
- `plan/2026-07-03_21-16-09-icloud-hdm-mail-api.md`（前版计划，本文件为其重写）。
- 当前项目：
  - `web_ui.py:48`：`_require_api_key` 装饰器（v1 鉴权复用点）。
  - `web_ui.py:166`：`UI_HTML` 内联单页（Phase 5 拆分对象）。
  - `web_ui.py:192`：既有 `/api/v1/accounts*` 区块（新 v1 路由插入位置）。
  - `web_ui.py:435`：`/api/accounts/<id>/mail/<alias>` 现有按别名查件。
  - `account_manager.py:313`：`get_mail_client`（IMAP 未配置错误来源）。
  - `account_manager.py:360`：`check_alias_mail`（latest 邮件底层，需修静默吞错）。
  - `account_manager.py:583`：`get_all_aliases`（列表合并数据源）。
  - `api_keys.py:21`：`APIKeyStore`（SharedMailboxStore 摹本：摘要存储/一次性明文/吊销）。
  - `mail_cache.py:106`：`cache_age_seconds`（shared 缓存 TTL 判断）。
  - `icloud_mail.py:94`：`find_by_recipient`；`icloud_mail.py:224`：`fetch_full`。
  - `tests/test_regressions.py:186`：APIKeyStore 隔离测试写法模板。
- 参考项目 `/mnt/x/project/outlookEmail`：
  - `templates/index.html:14`：壳 + partials + 编号 JS 模块的拆分方式（Phase 5 摹本）。
  - `templates/partials/index/layout.html:92`：三栏信息架构与面板头操作按钮布局（邮箱列表工具栏参考）。
  - `static/js/index/`：`01-core.js`…`10-batch-actions.js` 编号模块命名法。
  - `docs/api.md:10`：对外 API（`/api/external/*` + API Key）与管理 API（Session）分层写法（三层鉴权边界文档参考）。
  - `docs/api.md:119`：`/api/temp-emails/<addr>/messages/<message_id>` 邮件列表/详情两级接口结构。
  - `templates/index.html:46`：原始邮件模态框的「谨慎分享」警示文案模式（shared 弹窗警示参考）。
