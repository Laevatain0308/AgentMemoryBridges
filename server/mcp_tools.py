"""MCP 工具注册 —— 7 个工具供 Claude Code Agent 调用"""
from typing import Optional

from fastmcp import FastMCP

from database import async_session
from service import MemoryService, SessionService

mcp = FastMCP("agent-memory-bridge")


def _get_device_id() -> str:
    """从 ASGI scope 中获取中间件注入的 device_id"""
    try:
        from fastmcp.server.dependencies import get_http_request
        request = get_http_request()
        device_id = request.scope.get("device_id", "unknown")
        return device_id if device_id else "unknown"
    except RuntimeError:
        return "unknown"


async def _get_db():
    """创建独立数据库会话（不使用 FastAPI 依赖注入）"""
    async with async_session() as db:
        yield db


@mcp.tool()
async def search_memories(
    project: str,
    category: Optional[str] = None,
    tags: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 20,
) -> list[dict]:
    """搜索跨设备的共享记忆条目，支持 FTS5 全文搜索与条件筛选。

    使用场景：
    - 开始处理任何任务前，先根据项目名与用户问题关键词搜索是否有相关的前置分析
    - 调试时搜索类似问题的历史根因分析
    - 接手他人工作时了解过往的设计决策

    参数说明：
    - project: 必填，项目名（如 'LaevaPlayer'、'global'）
    - query: 搜索关键词，支持中文
    - category: 可选筛选，finding/decision/context/note
    - tags: 可选标签筛选（JSON 字符串或逗号分隔）
    - limit: 返回数量上限，默认 20
    """
    async for db in _get_db():
        results = await MemoryService.search(db, project, category, tags, query, limit=limit)
        return [_memory_to_dict(m) for m in results]
    return []


@mcp.tool()
async def add_memory(
    project: str,
    category: str,
    title: str,
    content: str,
    tags: Optional[list[str]] = None,
    related_files: Optional[list[str]] = None,
    related_urls: Optional[list[str]] = None,
) -> dict:
    """向跨设备共享知识库写入一条记忆。source_device 自动从 API Token 注入。

    使用场景（按 category 选择）：
    - 'finding':   调试发现的问题根因、分析结论、已验证的 bug 成因
    - 'decision':  设计决策及其推理依据，供其他设备后续参考
    - 'context':   项目背景、环境配置、已知限制、跨设备通用的注意事项
    - 'note':      一般性记录，不归入以上分类的信息

    参数说明：
    - project: 必填，项目名
    - category: 必填，finding / decision / context / note
    - title: 必填，简短标题（建议少于 50 字）
    - content: 必填，详细内容（支持 Markdown）
    - tags: 可选，标签列表如 ['mac-specific', 'ui']
    - related_files: 可选，关联文件路径列表如 ['src/Video.cs']
    - related_urls: 可选，关联链接列表

    返回：创建的完整记忆条目（含 id、source_device 等）
    """
    device_id = _get_device_id()
    data = {
        "project": project,
        "category": category,
        "title": title,
        "content": content,
        "tags": tags,
        "related_files": related_files,
        "related_urls": related_urls,
    }
    async for db in _get_db():
        memory = await MemoryService.create(db, data, device_id)
        return _memory_to_dict(memory)
    return {}


@mcp.tool()
async def update_memory(
    id: str,
    content: Optional[str] = None,
    status: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> dict:
    """部分更新已有的记忆条目，只需提供要更新的字段。

    使用场景：
    - 问题修复后将记忆状态改为 'resolved'
    - 补充新的分析结果到已有记忆的 content 中
    - 调整记忆的分类标签

    参数说明：
    - id: 必填，记忆 ID（从 search_memories 或 get_recent_memories 获取）
    - content: 可选，新的内容（覆盖原内容）
    - status: 可选，active / resolved / archived
    - tags: 可选，新的标签列表（覆盖原标签）

    返回：更新后的完整记忆条目；未找到则返回 {"error": "not_found"}
    """
    data: dict = {}
    if content is not None:
        data["content"] = content
    if status is not None:
        data["status"] = status
    if tags is not None:
        data["tags"] = tags
    async for db in _get_db():
        memory = await MemoryService.update(db, id, data)
        if memory:
            return _memory_to_dict(memory)
        return {"error": "not_found"}


@mcp.tool()
async def get_recent_memories(
    project: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 20,
) -> list[dict]:
    """获取最近的记忆条目列表，按创建时间倒序排列。

    使用场景：
    - 了解某项目最近的分析进展
    - 查看特定分类的最新记忆（如最近的 design decisions）
    - 会话开始时快速获取近期上下文

    参数说明：
    - project: 可选，筛选项目；不填则返回所有项目的最近记忆
    - category: 可选，筛选分类
    - limit: 可选，返回数量上限，默认 20
    """
    async for db in _get_db():
        results = await MemoryService.get_recent(db, project, category, limit)
        return [_memory_to_dict(m) for m in results]
    return []


@mcp.tool()
async def get_memories_by_file(project: str, file_path: str) -> list[dict]:
    """按关联的文件路径查找相关记忆。

    使用场景：
    - 修改某文件前，查看是否有相关的已知问题或设计决策
    - 跨设备协作时了解特定文件的历史分析记录

    参数说明：
    - project: 必填，项目名
    - file_path: 必填，文件路径（支持部分匹配，如 'src/Video' 可匹配 'src/Video.cs'）
    """
    async for db in _get_db():
        results = await MemoryService.get_by_file(db, project, file_path)
        return [_memory_to_dict(m) for m in results]
    return []


@mcp.tool()
async def add_session(
    project: str,
    summary: str,
    key_points: Optional[list[str]] = None,
    memory_refs: Optional[list[str]] = None,
) -> dict:
    """记录本次会话的摘要，便于其他设备或后续会话了解之前做了什么。

    使用场景：
    - 完成一个较长的调试/开发会话后，记录关键过程和结论
    - 将本次会话与相关记忆关联（通过 memory_refs 传入记忆 ID 列表）

    参数说明：
    - project: 必填，项目名
    - summary: 必填，会话摘要（做了什么、结论是什么）
    - key_points: 可选，关键发现列表如 ['发现A', '决策B']
    - memory_refs: 可选，关联的记忆 ID 列表
    """
    device_id = _get_device_id()
    data = {
        "project": project,
        "summary": summary,
        "key_points": key_points,
        "memory_refs": memory_refs,
    }
    async for db in _get_db():
        session = await SessionService.create(db, data, device_id)
        return {
            "id": session.id,
            "project": session.project,
            "source_device": session.source_device,
            "summary": session.summary,
            "key_points": session.key_points,
            "memory_refs": session.memory_refs,
            "created_at": session.created_at,
        }
    return {}


@mcp.tool()
async def get_recent_sessions(project: Optional[str] = None, limit: int = 20) -> list[dict]:
    """获取最近会话摘要列表，了解其他设备上的工作历史。

    使用场景：
    - 新会话开始时查看其他设备最近做了什么
    - 了解某项目近期的活跃会话

    参数说明：
    - project: 可选，筛选项目；不填返回所有项目
    - limit: 可选，返回数量上限，默认 20
    """
    async for db in _get_db():
        results = await SessionService.get_recent(db, project, limit)
        return [
            {
                "id": s.id,
                "project": s.project,
                "source_device": s.source_device,
                "summary": s.summary,
                "key_points": s.key_points,
                "memory_refs": s.memory_refs,
                "created_at": s.created_at,
            }
            for s in results
        ]
    return []


def _memory_to_dict(m) -> dict:
    """将 Memory 模型转为 dict"""
    import json

    def _try_parse(val):
        if val is None:
            return None
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return val

    return {
        "id": m.id,
        "project": m.project,
        "category": m.category,
        "title": m.title,
        "content": m.content,
        "tags": _try_parse(m.tags),
        "related_files": _try_parse(m.related_files),
        "related_urls": _try_parse(m.related_urls),
        "source_device": m.source_device,
        "status": m.status,
        "created_at": m.created_at,
        "updated_at": m.updated_at,
    }
