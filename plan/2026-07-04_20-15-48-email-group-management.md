---
mode: plan
cwd: /mnt/x/project/icloud-hme
task: 追加 HME 邮箱分组管理，参考 X:\project\outlookEmail
complexity: medium
tool: sequential-thinking
total_thoughts: 7
created_at: 2026-07-04T20:15:52+08:00
issue_csv: issues/2026-07-04_20-15-48-email-group-management.csv
---

# Plan: 追加 HME 邮箱分组管理

🎯 任务概述

在当前 iCloud HME Flask 项目中追加“HME 邮箱分组管理”能力，参考 `/mnt/x/project/outlookEmail` 的分组、排序和批量移动设计。分组归属于已经制作出的 HME 邮箱，不归属于 iCloud 账号；账号管理只保留 Cookie、会话、邮件登录和创建能力。

📋 执行计划

1. **确认范围与数据契约**：定义本项目的 `Group` 与邮箱 `group_*` 字段契约；分组状态以 `mailbox_groups` 维护 `alias_email -> group_id` 映射，账号对象不暴露也不保存 `group_id`。
2. **实现分组持久化层**：沿用当前 JSON 持久化风格，在 `accounts.json` 中保存 `groups` 与 `mailbox_groups`；启动时自动补默认分组、归一化邮箱地址、清理无效映射，并移除旧误实现留下的账号 `group_id`。
3. **接入邮箱管理后端**：保留 `/api/groups*` 管理端接口；新增 `/api/mailboxes/batch-update-group` 批量移动邮箱；删除分组时把邮箱映射清回默认分组，避免悬空引用。
4. **接入邮箱列表服务**：在 `mailbox_service.py` 的 `MailboxSummary` 中增加 `group_id`、`group_name`、`group_color`；`list_mailboxes()` 支持 `group_id` 过滤，并保留现有 `q/account_id/status/refresh` 语义；单邮箱详情展示所属邮箱分组。
5. **改造前端交互**：在 `templates/index.html` 与 `static/js/01-core.js` 中加入邮箱分组入口；新增/维护 `static/js/07-groups.js`；账号导入/编辑弹窗不出现分组选项；邮箱列表和详情页提供“移动分组”。
6. **补充目标测试**：覆盖旧数据自动补默认分组、旧账号 `group_id` 清理、分组 CRUD、排序、删除分组迁移邮箱、邮箱列表按分组过滤、前端邮箱分组筛选/移动渲染与 HTML 转义。
7. **执行验证与文档判断**：运行新增目标测试，再运行全量测试；做 Flask test client smoke：`/api/groups`、`/api/mailboxes?group_id=...`、`/api/mailboxes/batch-update-group`；公共 API 或 README 功能表变化时同步文档。

🧠 当前思考摘要

- 当前项目没有 `llmdoc/` 或项目级 `SKILL.md`；按 README 与相邻实现推进。
- 当前项目是轻量 Flask + JSON 存储，参考项目是 SQLite + 多邮箱系统；只借鉴分组模型和交互，不照搬存储架构。
- 分组绑定 HME 邮箱本身，HME 邮箱通过 `mailbox_groups` 映射获得分组；账号不再作为分组对象。
- 默认分组、删除迁移、排序归一化和旧账号 `group_id` 清理是兼容旧数据的关键，不应依赖前端兜底。

⚠️ 风险与注意事项

- `accounts.json` 存在敏感 Cookie 和邮件密码；新增分组字段时只做结构性最小变更，不打印、不暴露敏感字段。
- 旧数据没有邮箱分组字段，且可能已有误写入的账号 `group_id`；必须有幂等迁移和测试，否则升级后邮箱筛选或移动可能丢失。
- 参考项目有分组代理、标签、转发、临时邮箱等扩展能力；本计划明确排除，避免把当前 HME 项目变成另一个 Outlook 管理系统。
- 前端 JS 当前通过字符串拼 HTML；新增分组名、颜色、说明必须继续使用 `S.esc()` 或受控校验，避免 XSS。

## Issue CSV

- Path: `issues/2026-07-04_20-15-48-email-group-management.csv`
- 状态：本次只创建技术执行计划；Issue CSV 可在计划确认后按同一 timestamp/slug 生成。

📎 参考

- `README.md:223-245`：当前项目文件结构与运行时 JSON 文件。
- `account_manager.py:94-126`：当前 `AccountManager` 与 `accounts.json` 保存方式。
- `web_ui.py:807-871`：当前账号列表、分组 CRUD 与邮箱批量移动管理端接口。
- `mailbox_service.py:73-118`：当前 HME 邮箱列表合并、过滤和排序入口。
- `static/js/01-core.js:48-61`：当前基础状态刷新与侧边栏账号渲染。
- `static/js/03-mailboxes.js:25-122`：当前邮箱列表筛选、表格渲染、详情与移动分组入口。
- `/mnt/x/project/outlookEmail/outlook_web/segments/01_bootstrap.py:999-1038`：参考项目分组数据模型。
- `/mnt/x/project/outlookEmail/outlook_web/segments/02_groups_accounts.py:20-190`：参考项目分组加载、排序、创建、更新、删除迁移实现。
- `/mnt/x/project/outlookEmail/outlook_web/segments/04_routes_groups_accounts.py:145-257`：参考项目分组 CRUD 与排序 API。
