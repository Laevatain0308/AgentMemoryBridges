# 跨设备 Agent 记忆共享桥梁 —— 架构与实现计划

## Context

用户在 Mac 与 Windows 等多台设备上使用 Claude Code（通过 VSCode / JetBrains 插件），各设备上的 Agent 独立运作、相互隔离。
需要构建一个部署在云服务器上的中心化服务，作为"共享大脑"，打破设备间的信息隔阂，让任意设备上的 Agent 都能存取其他设备产生的分析结论、决策记录和会话上下文。

核心目标：
- **MCP 协议**：Claude Code 可自主调用，主动查询/写入共享信息
- **HTTP API**：用户可通过浏览器或 curl 直接操作知识库
- **Web 管理界面**：提供可视化操作，包括 Token 申请、知识库浏览
- **Token 认证**：保证只有授权的设备/用户才能访问
- **定期备份**：将数据 commit 到 GitHub 仓库做持久化
- **始终在线**：以云服务器为单一真相来源，不依赖任一设备在线

---

## 架构总览

```
┌──────────────────────────────────────────────────┐
│                    云服务器 (2核/2GiB)             │
│                                                  │
│  ┌──────────────────────────────────────────┐    │
│  │     宿主机 Nginx (PM2, :443)              │    │
│  │     location /kb/ → 127.0.0.1:3004       │    │
│  │     proxy_buffering off (SSE 优化)        │    │
│  └────────────────┬─────────────────────────┘    │
│                   │                              │
│  ┌────────────────▼─────────────────────────┐    │
│  │  Docker: Uvicorn (2 workers) + FastMCP   │    │
│  │  Port: 127.0.0.1:3004, mem_limit: 300m   │    │
│  │                                          │    │
│  │  ┌─────────┐  ┌──────────┐  ┌────────┐  │    │
│  │  │ MCP SSE │  │ REST API │  │ Web UI │  │    │
│  │  └────┬────┘  └────┬─────┘  └───┬────┘  │    │
│  │       └──────────┬──┴───────────┘        │    │
│  │                  ▼                       │    │
│  │    ┌──────────────────────────┐          │    │
│  │    │  AuthMiddleware (Token)  │          │    │
│  │    └──────────┬───────────────┘          │    │
│  │               ▼                          │    │
│  │    ┌──────────────────────────┐          │    │
│  │    │     LRU Cache (TTL)      │          │    │
│  │    └──────────┬───────────────┘          │    │
│  │               ▼                          │    │
│  │       MemoryService (async)              │    │
│  │               │                          │    │
│  │       ┌───────▼──────┐                   │    │
│  │       │   SQLite     │                   │    │
│  │       │  WAL + FTS5  │                   │    │
│  │       └──────────────┘                   │    │
│  └──────────────────────────────────────────┘    │
│                     │                            │
│              ┌──────▼──────┐                     │
│              │ 定时 Git 备份 │                     │
│              │(间隔/路径可配)│                     │
│              └─────────────┘                     │
└──────────────────────────────────────────────────┘
         │                         │
    MCP over SSE            HTTP/HTTPS
    (经过 Nginx :443)       (经过 Nginx :443)
         │                         │
  ┌──────▼──────┐           ┌──────▼──────┐
  │  设备 A     │           │  设备 B     │
  │  (Mac)      │           │  (Win)      │
  │ Claude Code │           │ Claude Code │
  │ 浏览器/curl │           │ 浏览器/curl │
  └─────────────┘           └─────────────┘
```

---

## 认证流程设计

### Token 管理

```
用户 → 浏览器打开 Web UI
         │
         ▼
    ┌─────────────┐
    │ 登录页面     │  (管理员密码 / 初始设置)
    │ /login      │
    └──────┬──────┘
           │
           ▼
    ┌─────────────┐
    │ Token 管理   │
    │ /admin      │
    │             │
    │ [申请新Token] │  输入标签: "Mac VSCode"
    │             │  系统返回一次性显示的 Token
    │ Token 列表   │  ┌────┬────────┬──────────┬──────┐
    │             │  │标签 │ 创建时间 │ 最后使用  │ 操作 │
    │             │  ├────┼────────┼──────────┼──────┤
    │             │  │Mac  │ 05-20  │ 05-20    │ 吊销 │
    │             │  │Win  │ 05-20  │ 05-20    │ 吊销 │
    │             │  └────┴────────┴──────────┴──────┘
    └─────────────┘
```

### Token 使用方式

用户将获取的 Token 配置到 Claude Code 设置中：

**VSCode 插件**（`.vscode/settings.json` 或用户级设置）：
```json
{
  "claude.mcpServers": {
    "shared-kb": {
      "type": "sse",
      "url": "https://kb.your-domain.com/mcp/sse",
      "headers": {
        "Authorization": "Bearer sk-xxxxxxxxxxxx"
      }
    }
  }
}
```

**JetBrains 插件**（设置 → Claude Code → MCP Servers）：
```json
{
  "mcpServers": {
    "shared-kb": {
      "type": "sse",
      "url": "https://kb.your-domain.com/mcp/sse",
      "headers": {
        "Authorization": "Bearer sk-xxxxxxxxxxxx"
      }
    }
  }
}
```

### Token 验证流程

```
请求到达 → AuthMiddleware
              │
              ├── 检查 Authorization: Bearer <token>
              │     ├── 有效 → 注入 device 信息 → 放行
              │     └── 无效 → 401
              │
              └── Web UI 页面请求（/admin, /login）
                    ├── Session Cookie 有效 → 放行
                    └── 无 Cookie → 重定向到 /login
```

**Token 表结构**：
```sql
CREATE TABLE tokens (
    id            TEXT PRIMARY KEY,         -- UUID
    token_hash    TEXT NOT NULL UNIQUE,     -- SHA256(token)
    token_prefix  TEXT NOT NULL,            -- 前4位，用于 UI 展示
    label         TEXT NOT NULL,            -- "Mac VSCode", "Win JetBrains"
    device_id     TEXT,                     -- 设备标识
    last_used_at  TEXT,
    created_at    TEXT NOT NULL,
    revoked       INTEGER DEFAULT 0
);

-- 管理员凭据（初期简单实现，后续可换 OAuth）
CREATE TABLE admin (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL
);

-- 系统配置（key-value）
CREATE TABLE system_config (
    key           TEXT PRIMARY KEY,
    value         TEXT NOT NULL,
    description   TEXT,
    updated_at    TEXT NOT NULL
);
-- 初始化配置项：
--   git_repo_url      - Git 仓库地址
--   git_repo_branch   - 备份目标分支 (默认 "main")
--   git_user_name     - Git 提交用户名
--   git_user_email    - Git 提交邮箱
--   git_token         - GitHub Personal Access Token（加密存储）
--   backup_interval_h - 备份间隔小时 (默认 6)
--   backup_enabled    - 是否启用自动备份 (默认 true)
```

---

## 数据模型（通用化）

将原先针对"跨平台调试"的数据模型，调整为更通用的"Agent 记忆条目"结构：

```sql
-- Agent 记忆条目（替代原先的 findings）
CREATE TABLE memories (
    id            TEXT PRIMARY KEY,        -- UUID
    project       TEXT NOT NULL,           -- 所属项目，如 "LaevaPlayer", "global"
    category      TEXT NOT NULL,           -- 分类: "finding" | "decision" | "context" | "note"
    title         TEXT NOT NULL,           -- 简短标题
    content       TEXT NOT NULL,           -- 详细内容（Markdown）
    tags          TEXT,                    -- JSON array: ["bug","mac-specific"]
    related_files TEXT,                    -- JSON array: ["src/Video.cs"]
    related_urls  TEXT,                    -- JSON array: ["https://..."], 关联外部链接
    source_device TEXT NOT NULL,           -- 来源设备标签
    status        TEXT DEFAULT 'active',   -- "active" | "resolved" | "archived"
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- 会话摘要
CREATE TABLE sessions (
    id            TEXT PRIMARY KEY,
    project       TEXT NOT NULL,
    source_device TEXT NOT NULL,
    summary       TEXT NOT NULL,           -- 对话摘要
    key_points    TEXT,                    -- JSON array: 关键发现/决定
    memory_refs   TEXT,                    -- JSON array of memory IDs, 关联的记忆条目
    created_at    TEXT NOT NULL
);
```

**Category 枚举说明**：
- `finding`：分析发现、问题根因
- `decision`：设计决策及推理依据
- `context`：项目背景、环境配置注意事项
- `note`：一般性记录

---

## API 设计

### MCP 工具（供 Claude Code 调用）

| 工具名 | 参数 | 功能 |
|---|---|---|
| `search_memories` | project, category?, tags?, query?, limit? | 搜索记忆条目 |
| `add_memory` | project, category, title, content, tags?, related_files?, related_urls? | 创建记忆条目 |
| `update_memory` | id, content?, status?, tags? | 更新记忆条目 |
| `get_recent_memories` | project?, category?, limit? | 查询最近记忆 |
| `get_memories_by_file` | project, file_path | 按关联文件查询 |
| `add_session` | project, summary, key_points?, memory_refs? | 记录会话摘要 |
| `get_recent_sessions` | project?, limit? | 获取最近会话 |

### REST API（供浏览器/curl 操作，路由后缀可后续调整）

| 方法 | 路径 | 功能 |
|---|---|---|
| GET | `/api/v1/memories?project=&category=&tags=&q=` | 查询记忆列表 |
| POST | `/api/v1/memories` | 创建记忆 |
| PUT | `/api/v1/memories/{id}` | 更新记忆 |
| DELETE | `/api/v1/memories/{id}` | 删除记忆 |
| GET | `/api/v1/sessions?project=&device=` | 查询会话摘要 |
| POST | `/api/v1/sessions` | 创建会话记录 |
| GET | `/api/v1/projects` | 获取项目列表 |
| GET | `/api/v1/stats?project=` | 统计概览 |
| POST | `/api/v1/tokens` | 申请新 Token（需管理会话） |
| GET | `/api/v1/tokens` | Token 列表（需管理会话） |
| DELETE | `/api/v1/tokens/{id}` | 吊销 Token（需管理会话） |
| GET | `/api/v1/config` | 获取系统配置（需管理会话） |
| PUT | `/api/v1/config` | 更新系统配置（需管理会话） |
| POST | `/api/v1/config/test-git` | 测试 Git 连接（需管理会话） |
| POST | `/api/v1/config/backup-now` | 手动触发备份（需管理会话） |

---

## Web UI 页面规划

| 页面 | 路由 | 功能 |
|---|---|---|
| 知识库首页 | `/` | 记忆列表、项目筛选、分类筛选、搜索 |
| 记忆详情 | `/memory/{id}` | 完整内容展示、关联信息、编辑入口 |
| 登录页 | `/login` | 管理员密码登录 |
| 管理后台 | `/admin` | Token 申请/吊销、查看 Token 使用记录 |
| 系统设置 | `/admin/settings` | Git 备份仓库 URL/分支/间隔配置、手动触发备份、测试连接 |

---

## 技术选型

| 层 | 选型 | 理由 |
|---|---|---|
| 服务框架 | FastAPI (async) + FastMCP | 全异步非阻塞，原生 async/await，同时承载 MCP SSE 长连接与 REST 短连接 |
| ASGI 服务器 | Uvicorn (2 workers) | 匹配 2 核 CPU，每个 worker 单线程事件循环；单用户场景无需更多 |
| 数据库 | SQLite (WAL 模式) + aiosqlite | WAL 模式支持并发读写不互斥；aiosqlite 异步驱动不阻塞事件循环 |
| ORM | SQLAlchemy 2.0 (async) | 原生异步 Session，与 FastAPI 依赖注入深度集成 |
| 全文搜索 | SQLite FTS5 + BM25 排序 | 内建无需外部服务，中文分词用 simple tokenizer + 前缀匹配 |
| 缓存 | cachetools.TTLCache (LRU) | 进程内内存缓存，热点查询 TTL 30s，零网络开销 |
| 密码哈希 | bcrypt (passlib) | Token 哈希用 SHA256；管理员密码用 bcrypt |
| 后台任务 | APScheduler (AsyncIOScheduler) | 异步定时备份，不阻塞主事件循环 |
| Git 操作 | GitPython (async wrapper) | 备份 commit + push |
| 部署 | Docker 单容器 + 宿主机现有 Nginx | 容器暴露 3004 端口，宿主机 Nginx 增加 location 反代 |
| 认证 | Bearer Token + Session Cookie | Token 用于 API/MCP（无状态），Cookie 用于 Web UI |
| 前端 | Jinja2 + 原生 JS + HTMX | 零构建步骤，HTMX 实现无刷新交互 |

### 资源预估（匹配 2核 / 可用约 1.3GiB）

| 进程 | CPU | 内存 |
|---|---|---|
| Uvicorn (2 workers) | ~0.2 核（空闲） | ~80MB |
| Python 应用 + SQLAlchemy | — | ~30MB |
| SQLite 缓存 + LRU | — | ~10MB |
| **合计** | **~0.2 核** | **~120MB** |

预留充足余量，在实际请求负载下峰值内存不超过 200MB。Nginx 反代由宿主机现有 Nginx 承担，不额外占用资源。

### 内存优化措施

- Uvicorn 使用 `--limit-max-requests 10000` 定期回收 worker，防止内存泄漏累积
- SQLAlchemy 连接池 `pool_size=3, max_overflow=5`（降低持有连接数）
- `PYTHONUNBUFFERED=1` + `PYTHONOPTIMIZE=1` 镜像构建
- Docker 容器限制 `mem_limit: 300m`，预留安全边界

### 端口与反代方案

```
Internet (HTTPS :443)
    │
    ▼
┌─────────────────────────┐
│  宿主机 Nginx (PM2)      │  ← 现有，已有其他服务反代规则
│                         │
│  location /kb/ {        │  ← 新增 location
│    proxy_pass           │
│      http://127.0.0.1:3004;
│  }                      │
│                         │
│  # SSE 长连接必须配置     │
│  proxy_buffering off;   │
│  proxy_read_timeout 24h;│
└────────┬────────────────┘
         │ proxy_pass
         ▼
┌─────────────────────────┐
│  Docker App Container   │
│  Uvicorn :3004          │  ← 暴露到宿主机 127.0.0.1:3004
│  mem_limit: 300m        │
└─────────────────────────┘
```

容器仅绑定 `127.0.0.1:3004`，不直接暴露到公网，安全性由宿主机 Nginx 保证。与其他 PM2 服务（3000-3002）模式一致。

---

## 项目结构

```
agent-memory-bridge/
├── server/
│   ├── main.py              # FastAPI + FastMCP 入口
│   ├── models.py            # SQLAlchemy async 模型
│   ├── database.py          # 异步引擎、WAL 配置、索引初始化
│   ├── service.py           # MemoryService 核心逻辑
│   ├── mcp_tools.py         # MCP 工具定义
│   ├── api_v1.py            # REST API 路由 (/api/v1/*)
│   ├── auth.py              # 认证中间件 + Token 管理
│   ├── cache.py             # LRU 缓存封装
│   ├── config.py            # 系统配置 service
│   ├── web.py               # Web UI 路由
│   ├── templates/           # Jinja2 模板
│   │   ├── base.html
│   │   ├── index.html
│   │   ├── memory.html
│   │   ├── login.html
│   │   ├── admin.html
│   │   └── settings.html
│   ├── static/
│   │   └── style.css
│   └── backup.py
├── docker-compose.yml        # 单容器，端口 127.0.0.1:3004
├── Dockerfile
├── requirements.txt
└── README.md
```

---

## 实现步骤

### 1. 项目骨架搭建
- 创建项目结构，初始化 Python 环境
- Dockerfile + docker-compose.yml（Uvicorn 多 worker）
- FastAPI 应用 + 挂载 FastMCP SSE 端点

### 2. 数据库引擎与模型
- `database.py`：异步引擎创建，配置 WAL 模式与连接池
- 定义 SQLAlchemy 模型（memories / sessions / tokens / admin / system_config）
- 启动时自动建表、创建 FTS5 全文索引、创建复合索引
- 首次启动自动创建管理员账号，写入默认 system_config 配置项
- 预填充默认 Git 备份配置（空值，通过 Web UI 设置）

### 3. 认证模块
- `auth.py`：Bearer Token 中间件（拦截 API + MCP 请求）
- Session Cookie 管理（Web UI 页面路由）
- Token 生成（`secrets.token_urlsafe`）+ SHA256 哈希存储
- 管理员密码 bcrypt 哈希 + 登录/登出

### 4. 缓存层
- `cache.py`：cachetools.TTLCache 封装
- 缓存 `get_recent_memories`（TTL 30s）、`stats`（TTL 60s）
- 写操作时主动失效相关缓存

### 5. 核心服务层
- `MemoryService`：异步 CRUD + FTS5 全文搜索 + 关联查询
- `SessionService`：会话摘要管理
- `TokenService`：Token 生命周期管理
- `ConfigService`：system_config 读写 + Git 连接测试

### 6. MCP 工具层
- `mcp_tools.py`：注册 MCP 工具函数
- 从请求 Header 提取 Token → 识别 source_device
- 对接 MemoryService + SessionService

### 7. REST API 层
- `api_v1.py`：实现所有端点（memory / session / token / config）
- Token 认证中间件
- 请求验证与错误处理

### 8. Web UI
- Jinja2 模板 + HTMX 无刷新交互
- 使用 Pico.css 轻量 CSS 框架
- 页面：首页、记忆详情、登录、管理后台（Token）、系统设置（Git 配置）
- 系统设置页：Git 仓库 URL / 分支 / Token / 备份间隔表单、测试连接按钮、手动触发备份按钮

### 9. Git 备份模块
- `backup.py`：从 system_config 读取 Git 仓库配置
- APScheduler AsyncIOScheduler 定时任务（间隔可配置）
- 导出 memories + sessions 为 Markdown 文件
- GitPython commit + push
- 备份状态写入日志，Web UI 可查看最近备份时间

### 10. 部署
- `docker-compose.yml` 单容器：
  - `app`：Uvicorn 2 workers，端口映射 `127.0.0.1:3004:3004`，volume 挂载 SQLite，mem_limit 300m
  - 不对外暴露端口，仅 localhost 可访问
- 宿主机现有 Nginx 添加 location 块：
  ```nginx
  location /kb/ {
      proxy_pass http://127.0.0.1:3004;
      proxy_buffering off;          # SSE 长连接必须关闭缓冲
      proxy_read_timeout 24h;       # MCP SSE 长连接超时
      proxy_set_header Host $host;
      proxy_set_header X-Real-IP $remote_addr;
  }
  ```
  （或使用子域名如 `kb.your-domain.com`，`proxy_pass` 方式相同）
- 环境变量注入初始管理员密码 + GitHub Token

### 11. 设备端配置
- 在 Claude Code 的 MCP 配置中添加 shared-kb 服务器
- 在 CLAUDE.md 中添加使用指引
- 配置 Startup hook 自动拉取对端上下文

---

## 验证方式

1. **Token 申请**：浏览器访问 Web UI → 登录 → 申请 Token → 复制 Token
2. **MCP 连通性**：配置 Token 后，Claude Code 中调用 `search_memories` 返回成功
3. **REST API**：`curl -H "Authorization: Bearer <token>" https://<domain>/api/v1/stats`
4. **跨设备共享**：设备 A 写入 memory → 设备 B Claude 启动通过 hook 获知
5. **Git 备份**：检查 GitHub 仓库有定期 Markdown 提交
6. **Token 吊销**：Web 管理后台吊销某 Token → 对应设备请求返回 401
