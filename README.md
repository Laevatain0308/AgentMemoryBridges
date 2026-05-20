# Agent Memory Bridge

跨设备 Agent 记忆共享桥梁。部署在云服务器上，通过 **MCP 协议 + REST API + Web UI** 三层接口，让多台设备上的 Claude Code Agent 共享分析结论、设计决策和会话上下文。

## 架构

```
Claude Code (Mac) ──MCP SSE──┐
Claude Code (Win) ──MCP SSE──┤
浏览器 ────────────REST/Web──┤
                              ▼
                     ┌────────────────┐
                     │  Nginx (:443)  │
                     │  /bridges/*    │
                     └───────┬────────┘
                             │
                     ┌───────▼────────┐
                     │   FastAPI      │
                     │ + FastMCP      │
                     │ Uvicorn x1     │
                     └───────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        ┌──────────┐  ┌──────────┐  ┌──────────┐
        │Auth MW   │  │LRU Cache │  │ Routes   │
        │Token/    │  │(TTL 30s) │  │Web/MCP/  │
        │Cookie    │  │          │  │API       │
        └──────────┘  └──────────┘  └──────────┘
                             │
                    ┌────────▼────────┐
                    │  Service Layer  │
                    │  Memory/Session │
                    │  Token/Config   │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │ SQLite (async)  │
                    │ WAL + FTS5      │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │  APScheduler    │
                    │  Git 定时备份    │
                    └─────────────────┘
```

## 快速开始

### 前提

- 服务器：2核 / 2GiB RAM，Docker ≥ 24.x + Docker Compose ≥ v2
- 宿主机 Nginx 监听 :443，SSL 已配置
- Python 3.12+

### 依赖检查与安装

```bash
# Docker（≥ 24.x）
docker --version || echo "请安装 Docker: https://docs.docker.com/engine/install/"

# Docker Compose（≥ v2）
docker compose version || echo "请安装 Docker Compose: https://docs.docker.com/compose/install/"

# Python 3（生成密钥用）
python3 --version || echo "请安装 Python 3"

# Git（可选，自动备份用）
git --version || echo "请安装 Git"

# Nginx（可选，反代用）
nginx -v 2>&1 || echo "请安装 Nginx"
```

一键安装 Docker（Ubuntu/Debian）：

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo apt install -y docker-compose-plugin python3 git nginx
```

### 部署

```bash
cd /opt/AgentMemoryBridges

# 1. 生成 Fernet 密钥
ENCRYPTION_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
echo "密钥: $ENCRYPTION_KEY  # 请记录备用"

# 2. 创建 .env
cat > .env << EOF
# ROOT_PATH=/bridges   # 取消注释并设置 Nginx 反代前缀
TZ=Asia/Shanghai
ADMIN_PASSWORD=<你的强密码>
ENCRYPTION_KEY=$ENCRYPTION_KEY
EOF
chmod 600 .env

# 3. 构建并启动
docker compose up -d --build

# 4. 验证
curl http://127.0.0.1:3004/health
# → {"status":"ok","service":"agent-memory-bridge"}

# 5. 查看管理员密码
docker compose logs 2>&1 | grep "管理员账号"
```

### Nginx 反代

```nginx
location /bridges/ {
    proxy_pass http://127.0.0.1:3004/;
    proxy_buffering off;
    proxy_read_timeout 24h;
    # proxy_redirect 已移除：后端通过 ROOT_PATH 自行处理前缀
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

```bash
sudo nginx -s reload
```

> **重要**：使用 Nginx 反代时，必须在 `.env` 中设置 `ROOT_PATH=/bridges`，否则页面链接会缺少前缀导致跳转 404。

### 环境变量

| 变量 | 说明 |
|------|------|
| `ROOT_PATH` | Nginx 反代前缀，如 `/bridges`。本地直接访问留空 |
| `TZ` | 容器时区，默认 `Asia/Shanghai`。设为空则跟随宿主机 `/etc/localtime` |
| `ADMIN_PASSWORD` | 管理员密码，未设置则随机生成输出到日志 |
| `ENCRYPTION_KEY` | **强烈建议**。Fernet 密钥，用于 session cookie 签名 + git_token 加密。不设置则每次重启 session 全部失效 |

## 使用

### 1. 登录管理后台

浏览器访问 `https://your-server.com/bridges/login`，用户名 `admin`，密码见启动日志：

```bash
docker compose logs 2>&1 | grep "管理员账号"
```

### 2. 申请 API Token

进入「Token 管理」→ 填写**设备标识**（必填，如 `macbook-pro`）→ 点击申请 → **立即复制保存 Token**（仅显示一次）。

> 同一设备标识只能持有一个有效 Token。更换需先吊销旧的。

### 3. 配置 MCP 客户端

在 Claude Code 的 `mcpServers` 配置中添加：

```json
{
  "mcpServers": {
    "shared-kb": {
      "type": "sse",
      "url": "https://your-server.com/bridges/mcp/sse",
      "headers": {
        "Authorization": "Bearer sk-xxxxxxxx..."
      }
    }
  }
}
```

配置文件位置：

| 工具 | 路径 |
|------|------|
| CC Switch | 由 CC Switch 管理的 MCP 配置文件 |
| VSCode | `.vscode/settings.json` |
| JetBrains | Settings → Claude Code → MCP Servers |
| CLI | `~/.claude/claude-code.json` |

### 4. 验证 MCP 连通

在 Claude Code 对话中：

```
请调用 search_memories 工具，查询 project="global" 的记忆
```

返回 `[]` 表示连接成功。

## API 参考

所有写操作端点需要 `Authorization: Bearer <token>` 请求头。Token 管理、系统配置、记忆删除需 admin session（Cookie）。

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| `GET` | `/api/v1/memories` | Token | 查询记忆（?project=&category=&q=&limit=&offset=） |
| `POST` | `/api/v1/memories` | Token | 创建记忆 |
| `PUT` | `/api/v1/memories/{id}` | Token | 更新记忆 |
| `DELETE` | `/api/v1/memories/{id}` | **Admin** | 删除记忆 |
| `GET` | `/api/v1/sessions` | Token | 查询会话摘要 |
| `POST` | `/api/v1/sessions` | Token | 创建会话记录 |
| `GET` | `/api/v1/projects` | Token | 获取项目列表 |
| `GET` | `/api/v1/stats` | Token | 统计概览 |
| `POST` | `/api/v1/tokens` | **Admin** | 创建 Token（body: {label, device_id}） |
| `GET` | `/api/v1/tokens` | **Admin** | Token 列表（仅有效） |
| `DELETE` | `/api/v1/tokens/{id}` | **Admin** | 吊销 Token |
| `GET` | `/api/v1/config` | **Admin** | 系统配置 |
| `PUT` | `/api/v1/config` | **Admin** | 更新配置 |
| `POST` | `/api/v1/config/test-git` | **Admin** | 测试 Git 连接 |
| `POST` | `/api/v1/config/backup-now` | **Admin** | 手动备份 |

### curl 示例

```bash
BASE="https://your-server.com/bridges"
TOKEN="sk-你的token"

curl -H "Authorization: Bearer $TOKEN" $BASE/api/v1/stats
curl -H "Authorization: Bearer $TOKEN" "$BASE/api/v1/memories?project=my-project&q=关键词"
```

## MCP 工具

| 工具 | 用途 |
|------|------|
| `search_memories` | 全文搜索记忆（项目、分类、标签筛选） |
| `get_recent_memories` | 最近记忆列表 |
| `get_memories_by_file` | 按关联文件查找记忆 |
| `add_memory` | 创建记忆（source_device 自动注入） |
| `update_memory` | 部分更新记忆（内容、状态、标签） |
| `add_session` | 记录会话摘要 |
| `get_recent_sessions` | 最近会话列表 |

## 维护

```bash
docker compose logs -f --tail=100     # 查看日志
docker compose restart                # 重启（代码未变）
docker compose up -d --build          # 重建（代码变更后）
```

### 重置管理员密码

```bash
docker compose exec app sqlite3 /app/server/data/bridge.db "DELETE FROM admin;"
ADMIN_PASSWORD=newpassword docker compose up -d
```

### 数据备份

```bash
cp data/bridge.db data/bridge.db.bak.$(date +%Y%m%d)
```

## 故障排查

| 现象 | 可能原因 | 解决 |
|------|---------|------|
| 登录后跳转又回到登录页 | `ENCRYPTION_KEY` 未固定 | 在 `.env` 中固定密钥 |
| MCP 返回 `-32602` | SSE 初始化未完成 | 断开 MCP 重连，等待几秒 |
| `Could not find session` | 多 worker 冲突 | v1.1 已改为单 worker |
| Token 总是提示不正确 | Token 无效或已吊销 | `curl -H "Authorization: Bearer $TOKEN" localhost:3004/api/v1/stats` |
| Git 测试连接报 `Bad git executable` | 镜像未安装 git | v1.1 已在 Dockerfile 安装 git |
| Git 测试连接报认证错误 | 私有仓库未填写 Token | 填写 GitHub Personal Access Token |
| 测试连接后表单字段清空 | 按钮在独立 form 中未提交字段 | v1.1 已改用 htmx AJAX |

## 项目文件

| 文件 | 说明 |
|------|------|
| `server/main.py` | FastAPI + FastMCP 入口 |
| `server/auth.py` | Token/Session 认证中间件 |
| `server/service.py` | Memory/Session/Token/Config 业务层 |
| `server/database.py` | SQLite async 引擎 + 初始化 |
| `server/models.py` | SQLAlchemy ORM 模型 |
| `server/api_v1.py` | REST API 路由 |
| `server/web.py` | Web UI 路由（Jinja2） |
| `server/mcp_tools.py` | MCP 工具注册 |
| `server/backup.py` | Git 定时备份 |
| `skills/bridge-memory/SKILL.md` | MCP 记忆 Skill |
| `DESIGN.md` | 设计文档 |
| `DEVELOPMENT.md` | 开发记录 |
| `TODO.md` | 待实现功能 |
