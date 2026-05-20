# Agent Memory Bridge 开发记录

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

`server/service.py`, `server/auth.py`, `server/database.py`, `server/main.py`,
`server/web.py`, `server/mcp_tools.py`, `server/models.py`, `server/api_v1.py`,
`server/backup.py`, `server/entrypoint.sh`, `Dockerfile`, `docker-compose.yml`,
`requirements.txt`, `server/templates/base.html`, `server/templates/admin.html`,
`server/templates/memory.html`, `server/templates/settings.html`,
`server/static/style.css`, `skills/bridge-memory/SKILL.md`

### 已知限制

- MCP SSE 必须单 worker 运行（session 存进程内存）
- macOS Docker Desktop 的 `/etc/localtime` 为 UTC，需通过 `TZ` 环境变量设时区
- 管理员注销需手动清除 Cookie（登录页无退出功能）
