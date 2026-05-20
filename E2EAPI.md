# 前后端数据通信约定

## 认证模式

| 通道 | 认证方式 | 适用场景 |
|------|---------|---------|
| MCP SSE | `Authorization: Bearer sk-xxx` 请求头 | Agent 工具调用 |
| REST API | `Authorization: Bearer sk-xxx` 请求头 | 外部 HTTP 调用 |
| Web UI | `admin_session` Cookie（Fernet 签名） | 管理员页面 |
| 公开页面 | 无需认证 | `/`、`/memory/{id}`、`/login`、`/.well-known/*` |

## REST API 响应格式

### 成功

```json
// 列表
[{"id": "...", "project": "...", ...}]

// 单条
{"id": "...", "project": "...", ...}

// 统计
{"total_memories": 42, "total_sessions": 7, "by_category": {...}}

// 创建 Token（仅一次）
{"token": "sk-xxx", "device_id": "macbook", "label": "VSCode", "message": "..."}
```

### 错误

```json
{"error": "missing_token"}        // 401
{"error": "invalid_token"}        // 401
{"error": "validation_error", "detail": [...]}  // 422
{"error": "not_found"}            // 404
{"error": "internal_error"}       // 500
```

## MCP 工具返回值

所有 MCP 工具返回 JSON 字典或列表，`source_device` 自动注入为 `device_id`。

```json
// add_memory / update_memory 返回
{"id": "abc123", "project": "MyProject", "source_device": "macbook", ...}

// search_memories 返回
[{"id": "abc123", ...}, ...]
```

## 时间格式

所有时间戳使用 `datetime.now().astimezone().isoformat()`，跟随容器时区（默认 `Asia/Shanghai`，可通过 `TZ` 环境变量配置）：

```
2026-05-21T12:34:56.789012+08:00
```
