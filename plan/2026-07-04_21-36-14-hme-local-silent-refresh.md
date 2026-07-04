---
mode: plan
cwd: /mnt/x/project/icloud-hme
task: HME 本地固定列表、静默校验、按选择刷新收件箱、调度和日志优化
complexity: medium
tool: sequential-thinking
total_thoughts: 7
created_at: 2026-07-04T21:36:14+08:00
issue_csv: issues/2026-07-04_21-36-14-hme-local-silent-refresh.csv
---

# Plan: HME 本地固定列表与后台静默刷新优化

## Goal
- HME 邮箱列表默认只展示本地固定内容，不因页面动作等待远端同步。
- 账号校验、健康检查和会话验证改为后台静默执行，UI 保持可操作，不显示空白等待页。
- 只有用户进入或刷新某一个 HME 详情时，才拉取该 HME 的最新收件箱内容。
- 调度器减少不必要远端查询和不可中断等待，日志页改成可读、可追踪的实时追加视图。

## Scope
- In:
  - `web_ui.py`：后台校验队列、状态接口、日志缓冲、内置调度器流程。
  - `account_manager.py`：账号保存与会话校验解耦，必要时增加后台验证状态字段。
  - `mailbox_service.py`：明确本地邮箱索引、远端刷新、单 HME 邮件读取边界。
  - `scheduler.py`：独立调度器与 Web 调度器共享效率策略。
  - `static/js/01-core.js`、`static/js/03-mailboxes.js`、`static/js/05-inbox-docs.js`：避免整页空白等待、按需刷新、日志差量追加。
  - 相关 `tests/` 单元测试和前端 Node VM 测试。
- Out:
  - 不改变 iCloud HME 协议客户端登录方式。
  - 不新增公网管理端能力。
  - 不把邮件内容提前批量拉取到全部 HME。
  - 不修改敏感运行时文件或提交账号/Cookie 数据。

## Assumptions / Dependencies
- “固定本地内容”指邮箱列表以 `latest_emails.txt`、`mailbox_index.json`、`accounts.json` 中的本地索引为准；远端同步只在用户显式触发时发生。
- “校验静默”指会话校验仍执行，但通过后台任务更新状态，不阻塞页面渲染和普通操作。
- “只有选择一个 HME 才刷新收件箱”指列表页不读取邮件；单邮箱详情页可以先显示缓存，再由用户进入该详情或点击刷新触发一次该 HME 邮件读取。
- 外部 iCloud/IMAP 网络调用在测试中必须 mock 或隔离。

## Phases
1. Baseline and contracts
   - 固化当前行为入口：`/api/state`、`/api/accounts`、`/api/mailboxes`、单邮箱详情、调度器启动、日志流。
   - 增加或标记目标测试，先覆盖“列表不远端刷新”“详情按 HME 读取”“校验后台化”“日志差量显示”。

2. Local HME list as the default source
   - 保持 `MailboxService.list_mailboxes(refresh=False)` 只读本地索引和本地创建结果。
   - 为 `/api/mailboxes` 响应补充 `source`、`last_synced_at` 或 `refreshed` 等轻量状态，前端清楚显示“本地列表”。
   - 保留显式“云端同步”按钮，但该按钮只刷新邮箱索引，不读取任何邮件正文。

3. Silent background account validation
   - 将账号新增、会话更新、手动校验拆成“保存本地会话 → 入队后台校验 → 立即返回当前账号状态”。
   - 增加后台验证队列或轻量 worker，记录 `validation_status`、`validation_started_at`、`last_validated`、`last_error`。
   - 防止旧校验覆盖新会话：校验任务携带 session 指纹或更新时间，落库前比对当前账号版本。

4. Non-blank UI rendering
   - 调整 `refreshAll()` 和路由渲染：后台刷新基础数据时不无条件重绘当前页面。
   - 邮箱列表、单邮箱详情、收件箱和日志页改为先渲染稳定外壳，再在局部区域显示加载状态。
   - 按钮操作用禁用态、toast 和行内状态反馈，不用整页 skeleton 替换现有内容。

5. Per-HME inbox refresh only on selection
   - 单邮箱详情先调用邮箱元数据接口，展示本地 HME 信息。
   - 最新邮件区域只在进入该 HME 详情或点击“刷新此邮箱邮件”时调用 `/api/mailboxes/{alias}/messages`。
   - 默认优先展示缓存；强制刷新只在用户选择该 HME 后加 `force=1`，不从列表页或全局刷新触发。

6. Scheduler efficiency cleanup
   - 统一 Web 内置调度器和 `scheduler.py` 的候选账号筛选、容量判断、limit 错误处理。
   - 默认使用本地 `alias_total` 决定是否跳过；只在计数缺失、接近上限或超过刷新阈值时远端 `list_aliases()` 校准。
   - 所有长等待改为 `_stop_event.wait()` 或可中断短分片，停止调度器能快速生效。
   - 调度日志按账号输出摘要，避免每轮重复无效刷新和噪声日志。

7. Log display optimization
   - 后端 `_emit_log` 写入内存环形缓冲，并提供 `/api/logs?limit=&since=` 历史读取接口。
   - `/api/log-stream` 保留 SSE 实时追加，必要时支持最近日志补发或客户端断线恢复。
   - 前端日志页改成 append 行，不再每条日志 `renderLogs()` 整页重绘；增加级别样式、自动滚动、暂停和清空本地视图。

8. Verification and cleanup
   - 运行新增目标测试，再运行现有相关测试：邮箱服务、前端邮箱详情、前端收件箱、调度器、回归测试。
   - 运行 `python -m py_compile web_ui.py account_manager.py mailbox_service.py scheduler.py`。
   - 启动 `web_ui.py` 做最小 smoke：`/`、`/api/state`、`/api/mailboxes`、日志流连接、单邮箱详情缓存路径。
   - 根据实际变更判断是否需要更新 README 或 llmdoc；本仓库当前未发现 `llmdoc/`。

## Tests & Verification
- Local mailbox list does not fetch remote aliases -> extend `tests/test_mailbox_service.py` around `list_mailboxes(refresh=False)` and `refresh=True`.
- Single HME detail renders before mail fetch failure -> extend `tests/test_frontend_mailbox_detail_errors.py` with non-blank shell assertion.
- Inbox refresh only happens after selected alias/account action -> extend `tests/test_frontend_inbox_imap.py` or add new Node VM test for `renderMailboxDetail` call sequence.
- Background validation returns immediately and later updates status -> add Flask test around `/api/accounts/add` or `/api/accounts/<id>/validate` with mocked `ICloudHME` delay.
- Scheduler skips unnecessary remote alias refresh -> extend `tests/test_scheduler_autostart.py` with stale/fresh count cases.
- Logs append without full rerender and can load history -> add Flask route test for `/api/logs` and Node VM test for log append.
- Syntax check -> `python -m py_compile web_ui.py account_manager.py mailbox_service.py scheduler.py`.
- Target pytest -> `python -m pytest tests/test_mailbox_service.py tests/test_frontend_mailbox_detail_errors.py tests/test_frontend_inbox_imap.py tests/test_scheduler_autostart.py`.

## Issue CSV
- Path: `issues/2026-07-04_21-36-14-hme-local-silent-refresh.csv`
- Status: not created in this Plan step; create it only if execution is split into trackable issue rows.

## Tools / MCP
- `sequential-thinking`: used for plan decomposition, final total thoughts 7.
- `read` / `grep` / `glob`: used to inspect current repo files and references.
- `write`: used to save this plan file.

## Acceptance Checklist
- [ ] 邮箱列表默认从本地索引渲染，不触发远端别名或 IMAP 调用。
- [ ] 用户进入普通页面、筛选、刷新基础状态时不出现整页空白等待。
- [ ] 账号会话校验在后台执行，UI 可见状态从 pending/running 更新到 active/error。
- [ ] 只有用户选择某个 HME 详情或点击该 HME 刷新时才读取该 HME 邮件。
- [ ] 调度器停止响应快，且不会每轮无条件远端刷新全部账号别名数。
- [ ] 日志页支持历史加载、实时追加、级别区分和本地清空。
- [ ] 目标测试、语法检查和最小 smoke 通过。

## Risks / Blockers
- 本地固定列表会有陈旧风险；必须显示最近同步时间或本地来源，避免用户误以为实时云端状态。
- 后台校验与用户编辑会话可能竞态；必须用账号版本或 session 指纹防止旧任务覆盖新数据。
- 调度器减少远端校准后可能晚发现 iCloud 上限；需保留接近上限和错误后的强制校准策略。
- 日志历史如果只在内存中保存，进程重启会丢失；本阶段只优化 UI 体验，不替代磁盘日志归档。

## Rollback / Recovery
- 后台校验可保留同步校验函数作为内部实现，只改变调用路径；必要时恢复路由同步调用。
- 邮箱列表刷新保留 `refresh=1` 显式路径；若本地索引异常，可手动云端同步重建。
- 调度器效率策略用小函数封装，出问题可回退到现有每轮校准逻辑。
- 日志 UI 改动不影响 `_emit_log` 消息格式；前端可回退到原 SSE 全量数组渲染。

## Checkpoints
- Commit after: tests for current behavior and non-blank UI guards.
- Commit after: local HME list and per-HME mail refresh boundary.
- Commit after: background validation and scheduler efficiency.
- Commit after: log display optimization and final verification.

## References
- `README.md:40-52` Web UI 功能边界。
- `README.md:215-247` 调度逻辑和运行时文件说明。
- `mailbox_service.py:73-89` 本地索引与 `refresh=True` 远端同步路径。
- `mailbox_service.py:186-206` 单 HME 邮件读取与缓存路径。
- `web_ui.py:431-439` 当前健康检查同步校验循环。
- `web_ui.py:824-844` `/api/state` 和 `/api/accounts` 基础状态接口。
- `web_ui.py:959-1016` 账号新增、会话更新、手动校验接口。
- `web_ui.py:1083-1132` 账号收件箱和指定 alias 邮件读取接口。
- `web_ui.py:1188-1215` Web 调度器启动停止接口。
- `web_ui.py:1215-1221` 当前 SSE 日志流。
- `scheduler.py:77-122` 独立调度器单轮创建逻辑。
- `scheduler.py:136-159` 独立调度器主循环。
- `static/js/01-core.js:49-108` 前端基础刷新和路由重绘。
- `static/js/03-mailboxes.js:25-90` 邮箱列表和单邮箱详情入口。
- `static/js/05-inbox-docs.js:37-65` 收件箱读取与 IMAP 测试入口。
- `static/js/05-inbox-docs.js:96-114` 当前日志页和 SSE 客户端。
- `tests/test_mailbox_service.py:86-116` 本地列表与显式刷新测试基础。
- `tests/test_scheduler_autostart.py:38-74` 调度器跳过上限与容量测试基础。
