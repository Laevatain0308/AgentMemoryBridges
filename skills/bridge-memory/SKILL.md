---
name: bridge-memory
description: 启用跨设备 Agent 记忆共享。当用户需要跨会话/跨设备保留分析结论、设计决策或项目上下文时使用。调用后 Agent 将按需读写共享知识库。
---

# Bridge Memory Skill

启用跨设备 Agent 记忆共享。调用后，Agent 将在本对话中按需读写共享知识库。

## 可用 MCP 工具

（工具名以 `mcp__AgentMemoryBridges` 开头，使用 `ToolSearch` 加载 schema）

| 工具 | 用途 |
|------|------|
| `search_memories` | 全文搜索记忆（支持项目、分类、标签筛选） |
| `get_recent_memories` | 最近记忆列表 |
| `get_memories_by_file` | 按关联文件查找记忆 |
| `add_memory` | 创建记忆（source_device 自动注入） |
| `update_memory` | 部分更新记忆（内容、状态、标签） |
| `add_session` | 记录会话摘要 |
| `get_recent_sessions` | 最近会话列表 |

## 何时读取

当以下情况时，考虑搜索共享记忆：

- 用户的问题涉及某个项目的背景、历史决策或已知问题
- 正在修改一个文件，想了解之前是否有相关的分析记录
- 接手一个任务时需要了解之前的进展

不需要在每次回答前都查询。

## 何时写入

当以下情况时，考虑调用 `add_memory`：

| 触发条件 | category | 示例 |
|----------|----------|------|
| 发现 bug 根因或重要分析结论 | `finding` | "确认 crash 由空指针导致" |
| 做出影响后续开发的设计决策 | `decision` | "选择方案 A 而非 B，因为…" |
| 遇到值得跨设备共享的配置或限制 | `context` | "此项目需 Xcode 15+ 才能编译" |
| 一般性值得记录的信息 | `note` | |

不需要为琐碎操作或每次对话都写入——只记录对后续设备上 Agent 有参考价值的内容。

## 参数说明

**add_memory**:
- `project`: 项目名，必填
- `category`: `finding` / `decision` / `context` / `note`
- `title`: 简短标题
- `content`: 详细内容（Markdown）
- `tags`: 可选标签列表，如 `["bug", "mac"]`
- `related_files`: 可选关联文件路径列表
- `related_urls`: 可选关联链接列表

**add_session** (较长对话结束时可选):
- `project`: 项目名
- `summary`: 本次会话做了什么
- `key_points`: 可选，关键发现列表
- `memory_refs`: 可选，关联的记忆 ID 列表

## 权限说明

- MCP 工具仅提供记忆的读写和查询功能，不包含 Token 管理、系统配置、记忆删除等管理操作
- Token 由管理员在 Web 管理界面创建和分配，每台设备一个 Token
- 记忆删除仅在 Web UI 中提供（需管理员登录）
- `source_device` 自动注入为 Token 绑定的设备标识（device_id），Agent 无需也无法手动指定

## 注意

- `source_device` 自动注入为 Token 绑定的设备标识（device_id），无需手动提供
- 调用失败时不要反复重试
