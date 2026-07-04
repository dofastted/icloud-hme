---
mode: plan
cwd: /mnt/x/project/icloud-hme
task: 追加邮箱管理与分组管理，参考 X:\project\outlookEmail
complexity: medium
tool: sequential-thinking
total_thoughts: 7
created_at: 2026-07-04T20:15:52+08:00
issue_csv: issues/2026-07-04_20-15-48-email-group-management.csv
---

# Plan: 追加邮箱管理与分组管理

🎯 任务概述

在当前 iCloud HME Flask 项目中追加“邮箱/账号分组管理”能力，参考 `/mnt/x/project/outlookEmail` 的分组、账号归属、排序和批量移动设计。当前项目已具备账号管理、HME 邮箱列表、单邮箱详情与 shared 管理；本计划不重做这些能力，只把“分组”作为新的管理维度接入账号列表、邮箱列表和批量创建流程。

📋 执行计划

1. **确认范围与数据契约**：定义本项目的 `Group`、账号 `group_id`、邮箱列表 `group_*` 字段契约；明确只引入分组、颜色、排序、账号归属和批量移动，不引入参考项目的 SQLite 迁移、OAuth 邮箱体系、分组代理、标签、转发、临时邮箱等能力。
2. **实现分组持久化层**：沿用当前 JSON 持久化风格，优先在 `account_manager.py` 中集中维护分组；启动时自动补齐默认分组，旧 `accounts.json` 无 `group_id` 时全部归入默认分组；提供 `list_groups`、`add_group`、`update_group`、`delete_group`、`reorder_groups`、`move_accounts_to_group` 等方法。
3. **接入账号管理后端**：扩展 `/api/accounts` 返回分组字段和分组摘要；新增 `/api/groups*` 管理端接口；账号新增、编辑和批量创建入口支持 `group_id`；删除分组时把账号迁回默认分组，避免悬空引用。
4. **接入邮箱列表服务**：在 `mailbox_service.py` 的 `MailboxSummary` 中增加 `group_id`、`group_name`、`group_color`；`list_mailboxes()` 支持 `group_id` 过滤，并保留现有 `q/account_id/status/refresh` 语义；单邮箱详情展示所属分组。
5. **改造前端交互**：在 `templates/index.html` 与 `static/js/01-core.js` 的侧边栏或账号区加入分组入口；新增 `static/js/07-groups.js` 或按现有编号调整；账号导入/编辑弹窗增加分组选择；邮箱列表增加分组筛选；共享、API Key、收件箱等现有视图不改变主流程。
6. **补充目标测试**：新增或扩展测试覆盖旧数据自动补默认分组、分组 CRUD、排序、删除分组迁移账号、账号新增/编辑写入 `group_id`、邮箱列表按分组过滤、前端分组选择渲染与 HTML 转义；确保现有 mailbox/shared/API key 回归不破坏。
7. **执行验证与文档判断**：运行新增目标测试，再运行受影响测试集；做 Flask test client smoke：`/api/groups`、`/api/accounts`、`/api/mailboxes?group_id=...`；若公共 API 或 README 功能表变化，更新文档或向用户确认是否同步 `llmdoc/`（当前项目未发现 `llmdoc/`）。

🧠 当前思考摘要

- 当前项目没有 `llmdoc/` 或项目级 `SKILL.md`；按 README 与相邻实现推进。
- 当前项目是轻量 Flask + JSON 存储，参考项目是 SQLite + 多邮箱系统；应借鉴分组模型和交互，不照搬存储架构。
- 分组应先绑定当前“账号”，HME 邮箱通过所属账号继承分组；这样改动小，避免给每个 HME 别名单独维护重复分组状态。
- 默认分组、删除迁移、排序归一化是兼容旧数据的关键，不应依赖前端兜底。

⚠️ 风险与注意事项

- `accounts.json` 存在敏感 Cookie 和邮件密码；新增分组字段时只做结构性最小变更，不打印、不暴露敏感字段。
- 旧数据没有分组字段；必须有幂等迁移和测试，否则升级后账号或邮箱筛选可能丢失。
- 参考项目有分组代理、标签、转发、临时邮箱等扩展能力；本计划明确排除，避免把当前 HME 项目变成另一个 Outlook 管理系统。
- 前端 JS 当前通过字符串拼 HTML；新增分组名、颜色、说明必须继续使用 `S.esc()` 或受控校验，避免 XSS。

## Issue CSV

- Path: `issues/2026-07-04_20-15-48-email-group-management.csv`
- 状态：本次只创建技术执行计划；Issue CSV 可在计划确认后按同一 timestamp/slug 生成。

📎 参考

- `README.md:223-245`：当前项目文件结构与运行时 JSON 文件。
- `account_manager.py:94-126`：当前 `AccountManager` 与 `accounts.json` 保存方式。
- `web_ui.py:743-869`：当前账号新增、编辑、删除、批量创建管理端接口。
- `mailbox_service.py:73-116`：当前 HME 邮箱列表合并、过滤和排序入口。
- `static/js/01-core.js:47-58`：当前基础状态刷新与侧边栏账号渲染。
- `static/js/03-mailboxes.js:15-40`：当前邮箱列表筛选和表格渲染。
- `/mnt/x/project/outlookEmail/outlook_web/segments/01_bootstrap.py:999-1038`：参考项目 `groups` / `accounts.group_id` 模型。
- `/mnt/x/project/outlookEmail/outlook_web/segments/02_groups_accounts.py:20-190`：参考项目分组加载、排序、创建、更新、删除迁移实现。
- `/mnt/x/project/outlookEmail/outlook_web/segments/04_routes_groups_accounts.py:145-257`：参考项目分组 CRUD 与排序 API。
