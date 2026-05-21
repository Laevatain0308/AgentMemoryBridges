# Agent Memory Bridge 开发记录

## v1.1.1 — 2026-05-21：修复删除记忆 FTS5 触发器 SQL logic error

### Bug 修复

| # | 问题 | 根因 | 修复 |
|---|------|------|------|
| 10 | 删除记忆报 `SQL logic error`（五次修复未解决） | FTS5 的 `'delete'` INSERT 命令在 SQLite 3.51.0 中不工作：`INSERT INTO fts(fts, rowid, ...) VALUES('delete', ...)` 一律报 SQL logic error | 触发器改用 `DELETE FROM memories_fts WHERE rowid = old.rowid`（天然幂等） |

### 根因分析过程

五次提交（`b079e4f` ~ `172d3a2`）尝试了 try/except 重试、主动 INSERT OR IGNORE 预同步、触发器 INSERT OR REPLACE、绕过触发器手动清理等方案，全部失败。根本原因是所有方案都依赖 FTS5 的 `'delete'` INSERT 命令来清理 FTS 索引，但该命令在当前 SQLite 3.51.0 版本中**根本不生效**。

通过独立脚本验证确认：`INSERT INTO memories_fts(memories_fts, rowid, ...) VALUES('delete', ...)` 无论是 `INSERT` 还是 `INSERT OR REPLACE`，无论是否带 `content_rowid`，均报 `SQL logic error`。而 `DELETE FROM memories_fts WHERE rowid = ?` 工作正常。

### 架构改进

- DELETE 触发器：`DELETE FROM memories_fts WHERE rowid = old.rowid`（替代 `'delete'` INSERT 命令）
- UPDATE 触发器：`DELETE FROM ... WHERE rowid = old.rowid; INSERT INTO ... VALUES (new.rowid, ...)`
- INSERT 触发器：保持不变
- 服务层简化：移除 delete() 和 update() 中的 FTS 预同步代码（`INSERT OR IGNORE`），因 `DELETE FROM WHERE rowid` 天然幂等
- 移除重复日志行

### 涉及文件（2 个）

`server/database.py`, `server/service.py`

---

## v1.1 — 2026-05-21：MCP 服务稳定性与 Web UI 完善

### Bug 修复

| # | 问题 | 根因 | 修复 |
|---|------|------|------|
| 1 | 知识库"全部项目"筛选返回空 | `service.py` search() project="" 时 SQL 条件不匹配 | project 为空时不添加 WHERE 条件 |
| 2 | Web UI 频繁重新登录 | Session Fernet 密钥每次重启随机生成 | 密钥持久化到 `system_config` 表 |
| 3 | 取消自动备份不生效 | HTML checkbox 取消勾选不发送字段 | 加 hidden input |
| 4 | 备份间隔修改不动态生效 | 调度器创建后未更新 | 保存配置时 `scheduler.reschedule_job()` |
| 5 | Token 创建后刷新页面重复提交 | POST 直接返回模板 | PRG 模式（闪存 Cookie + Redirect） |
| 6 | bcrypt/passlib 不兼容 | bcrypt 4.1+ 移除 `__about__` 属性 | 锁定 `bcrypt>=4.0.0,<4.1.0` |
| 7 | 多 worker init_db 并发 disk I/O | 2 worker 同时写 SQLite | 添加 OperationalError 重试 |
| 8 | MCP SSE 多 worker session 丢失 | SSE session 存进程内存 | worker 改为 1 |
| 9 | MCP OAuth 发现端点返回 401 | `/.well-known/` 未在公开路径 | 加入 `PUBLIC_PATH_PREFIXES` |

### 架构改进

- `source_device` 从 Token.label 改为 Token.device_id（设备标识符）
- Token device_id 互斥（`device_id_exists()` 校验）
- `list_tokens()` 过滤已吊销 Token
- `DELETE /api/v1/memories/{id}` 认证从 Token 改为 admin session
- Web UI 记忆详情页添加管理员删除按钮
- 所有时间统一为 `datetime.now()` 本地时间
- 管理员密码在启动日志中始终可见
- Markdown 记忆内容使用 marked.js 渲染（GitHub 风格）
- Git 连接测试改用 `git ls-remote`（无需本地仓库）
- 容器时区通过 `TZ` 环境变量 + entrypoint 管理，`datetime.now().astimezone()` 跟随系统时区
- 系统设置测试/备份按钮改用 htmx AJAX，不再刷新页面导致未保存输入丢失
- Docker 镜像安装 git + tzdata，`GIT_PYTHON_REFRESH=quiet` 静默警告
- 备份导出按项目分组，JSON 字段解析为可读格式
- 备份推送锁 + push 失败检测 + 本地文件清理，Web UI 加载指示器
- Git 连接测试改用 `git ls-remote`，错误信息人性化
- 全站 URL 支持 `ROOT_PATH` 前缀，`_prefix()` 统一拼接，Nginx 不再需要 `proxy_redirect`

### 涉及文件（19 个）

`server/service.py`, `server/auth.py`, `server/database.py`, `server/main.py`,
`server/web.py`, `server/mcp_tools.py`, `server/models.py`, `server/api_v1.py`,
`server/backup.py`, `server/entrypoint.sh`, `Dockerfile`, `docker-compose.yml`,
`requirements.txt`, `server/templates/base.html`, `server/templates/admin.html`,
`server/templates/memory.html`, `server/templates/settings.html`,
`server/templates/login.html`, `server/static/style.css`, `skills/bridge-memory/SKILL.md`

### 已知限制

- MCP SSE 必须单 worker 运行（session 存进程内存）
- macOS Docker Desktop 的 `/etc/localtime` 为 UTC，需通过 `TZ` 环境变量设时区
- 管理员注销需手动清除 Cookie（登录页无退出功能）
