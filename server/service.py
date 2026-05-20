"""核心服务层 —— Memory / Session / Token / Config 的异步 CRUD"""
import json
import logging
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select, text, delete as sa_delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from cache import cache
from models import Memory, AgentSession, SystemConfig

logger = logging.getLogger("agent-memory-bridge")


def _now() -> str:
    return datetime.now().astimezone().isoformat()


# ── MemoryService ─────────────────────────────────────────────
class MemoryService:

    @staticmethod
    async def search(
        db: AsyncSession,
        project: str,
        category: Optional[str] = None,
        tags: Optional[str] = None,
        query: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[Memory]:
        """FTS5 全文搜索 + 条件过滤"""
        if query and query.strip():
            # FTS5 MATCH 查询
            fts_sql = (
                "SELECT m.id FROM memories m "
                "INNER JOIN memories_fts fts ON m.rowid = fts.rowid "
                "WHERE memories_fts MATCH :q"
            )
            params: dict = {"q": query.strip()}
            if project:
                fts_sql += " AND m.project = :project"
                params["project"] = project
            if category:
                fts_sql += " AND m.category = :category"
                params["category"] = category
            fts_sql += " ORDER BY rank LIMIT :limit OFFSET :offset"
            params["limit"] = limit
            params["offset"] = offset
            from sqlalchemy import text
            result = await db.execute(text(fts_sql), params)
            ids = [row[0] for row in result.fetchall()]
            if not ids:
                return []
            stmt = select(Memory).where(Memory.id.in_(ids)).order_by(Memory.created_at.desc())
            result = await db.execute(stmt)
            return list(result.scalars().all())

        # 无全文搜索关键词时走普通查询
        stmt = select(Memory)
        if project:
            stmt = stmt.where(Memory.project == project)
        if category:
            stmt = stmt.where(Memory.category == category)
        stmt = stmt.order_by(Memory.created_at.desc()).offset(offset).limit(limit)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def create(db: AsyncSession, data: dict, source_device: str) -> Memory:
        """创建记忆，source_device 自动注入"""
        memory = Memory(
            project=data.get("project", ""),
            category=data.get("category", "note"),
            title=data.get("title", ""),
            content=data.get("content", ""),
            tags=json.dumps(data["tags"]) if data.get("tags") else None,
            related_files=json.dumps(data["related_files"]) if data.get("related_files") else None,
            related_urls=json.dumps(data["related_urls"]) if data.get("related_urls") else None,
            source_device=source_device,
            status=data.get("status", "active"),
        )
        db.add(memory)
        await db.commit()
        await db.refresh(memory)
        cache.invalidate("memories:*")
        cache.invalidate("stats:*")
        cache.invalidate("projects:*")
        logger.info("记忆已创建: id=%s title=%s device=%s", memory.id, memory.title, source_device)
        return memory

    @staticmethod
    async def update(db: AsyncSession, memory_id: str, data: dict) -> Optional[Memory]:
        """部分更新记忆（确保 FTS 索引一致）"""
        result = await db.execute(select(Memory).where(Memory.id == memory_id))
        memory = result.scalar_one_or_none()
        if memory is None:
            return None
        # 确保 FTS 索引中存在该行，防止 UPDATE 触发器 'delete' 命令失败
        await db.execute(text("""
            INSERT OR IGNORE INTO memories_fts(rowid, title, content, tags)
            SELECT rowid, title, content, coalesce(tags, '') FROM memories WHERE id = :id
        """), {"id": memory_id})
        await db.commit()
        for field in ("content", "status", "title", "category", "project"):
            if field in data and data[field] is not None:
                setattr(memory, field, data[field])
        if "tags" in data and data["tags"] is not None:
            memory.tags = json.dumps(data["tags"])
        memory.updated_at = _now()
        await db.commit()
        await db.refresh(memory)
        cache.invalidate("memories:*")
        logger.info("记忆已更新: id=%s", memory_id)
        return memory

    @staticmethod
    async def delete(db: AsyncSession, memory_id: str) -> bool:
        """删除记忆（先确保 FTS 行存在，再删主表让触发器清理）"""
        result = await db.execute(select(Memory).where(Memory.id == memory_id))
        memory = result.scalar_one_or_none()
        if memory is None:
            return False
        # 确保 FTS 索引中存在该行，否则 AFTER DELETE 触发器的 'delete' 命令会报错
        await db.execute(text("""
            INSERT OR IGNORE INTO memories_fts(rowid, title, content, tags)
            SELECT rowid, title, content, coalesce(tags, '') FROM memories WHERE id = :id
        """), {"id": memory_id})
        # 删除主表（同一事务内触发器可见上一步的 INSERT，正常清理 FTS）
        await db.execute(text("DELETE FROM memories WHERE id = :id"), {"id": memory_id})
        await db.commit()
        cache.invalidate("memories:*")
        cache.invalidate("stats:*")
        cache.invalidate("projects:*")
        logger.info("记忆已删除: id=%s", memory_id)
        logger.info("记忆已删除: id=%s", memory_id)
        return True

    @staticmethod
    async def get_recent(
        db: AsyncSession,
        project: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 20,
    ) -> list[Memory]:
        """最近记忆列表"""
        cache_key = f"memories:recent:{project}:{category}:{limit}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        stmt = select(Memory)
        if project:
            stmt = stmt.where(Memory.project == project)
        if category:
            stmt = stmt.where(Memory.category == category)
        stmt = stmt.order_by(Memory.created_at.desc()).limit(limit)
        result = await db.execute(stmt)
        memories = list(result.scalars().all())
        cache.set(cache_key, memories)
        return memories

    @staticmethod
    async def get_by_file(db: AsyncSession, project: str, file_path: str) -> list[Memory]:
        """按关联文件查找（LIKE 查询 related_files）"""
        stmt = select(Memory).where(
            Memory.project == project,
            Memory.related_files.like(f"%{file_path}%"),
        ).order_by(Memory.created_at.desc())
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_projects(db: AsyncSession) -> list[str]:
        """获取所有不重复的 project 列表"""
        cache_key = "projects:list"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        from sqlalchemy import text
        result = await db.execute(text("SELECT DISTINCT project FROM memories ORDER BY project"))
        projects = [row[0] for row in result.fetchall()]
        cache.set(cache_key, projects)
        return projects

    @staticmethod
    async def get_stats(db: AsyncSession, project: Optional[str] = None) -> dict:
        """统计概览"""
        cache_key = f"stats:{project}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        # total_memories
        mem_stmt = select(func.count(Memory.id))
        sess_stmt = select(func.count(AgentSession.id))
        if project:
            mem_stmt = mem_stmt.where(Memory.project == project)
            sess_stmt = sess_stmt.where(AgentSession.project == project)
        total_memories = (await db.execute(mem_stmt)).scalar() or 0
        total_sessions = (await db.execute(sess_stmt)).scalar() or 0

        # by_category
        from sqlalchemy import text
        cat_sql = "SELECT category, COUNT(*) FROM memories"
        params: dict = {}
        if project:
            cat_sql += " WHERE project = :project"
            params["project"] = project
        cat_sql += " GROUP BY category"
        result = await db.execute(text(cat_sql), params)
        by_category = {row[0]: row[1] for row in result.fetchall()}

        # by_project (only when no project filter)
        by_project: dict = {}
        if not project:
            result = await db.execute(text("SELECT project, COUNT(*) FROM memories GROUP BY project"))
            by_project = {row[0]: row[1] for row in result.fetchall()}

        stats = {
            "total_memories": total_memories,
            "total_sessions": total_sessions,
            "by_category": by_category,
            "by_project": by_project,
        }
        cache.set(cache_key, stats)
        return stats

    @staticmethod
    async def get_by_id(db: AsyncSession, memory_id: str) -> Optional[Memory]:
        """按 ID 查询单个记忆"""
        result = await db.execute(select(Memory).where(Memory.id == memory_id))
        return result.scalar_one_or_none()


# ── SessionService ────────────────────────────────────────────
class SessionService:

    @staticmethod
    async def create(db: AsyncSession, data: dict, source_device: str) -> AgentSession:
        """创建会话记录"""
        session = AgentSession(
            project=data.get("project", ""),
            source_device=source_device,
            summary=data.get("summary", ""),
            key_points=json.dumps(data["key_points"]) if data.get("key_points") else None,
            memory_refs=json.dumps(data["memory_refs"]) if data.get("memory_refs") else None,
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        cache.invalidate("stats:*")
        logger.info("会话已记录: id=%s device=%s", session.id, source_device)
        return session

    @staticmethod
    async def get_recent(
        db: AsyncSession,
        project: Optional[str] = None,
        limit: int = 20,
    ) -> list[AgentSession]:
        """最近会话列表"""
        stmt = select(AgentSession)
        if project:
            stmt = stmt.where(AgentSession.project == project)
        stmt = stmt.order_by(AgentSession.created_at.desc()).limit(limit)
        result = await db.execute(stmt)
        return list(result.scalars().all())


# ── TokenService ──────────────────────────────────────────────
class TokenService:

    @staticmethod
    async def create(db: AsyncSession, label: str, device_id: str) -> str:
        from auth import create_token
        return await create_token(db, label, device_id)

    @staticmethod
    async def revoke(db: AsyncSession, token_id: str) -> bool:
        from auth import revoke_token
        return await revoke_token(db, token_id)

    @staticmethod
    async def list_tokens(db: AsyncSession) -> list[dict]:
        from auth import list_tokens
        return await list_tokens(db)


# ── ConfigService ─────────────────────────────────────────────
class ConfigService:

    @staticmethod
    async def get_all(db: AsyncSession) -> dict:
        """返回所有系统配置（排除 git_token 敏感值）"""
        result = await db.execute(select(SystemConfig).order_by(SystemConfig.key))
        configs = result.scalars().all()
        out: dict = {}
        for c in configs:
            val = c.value
            if c.key == "git_token" and val:
                val = "********"  # 不暴露加密后的值
            out[c.key] = {
                "value": val,
                "description": c.description,
                "updated_at": c.updated_at,
            }
        return out

    @staticmethod
    async def update(db: AsyncSession, items: dict) -> None:
        """批量更新配置（UPSERT）"""
        for key, val in items.items():
            if val is None:
                continue
            result = await db.execute(select(SystemConfig).where(SystemConfig.key == key))
            config = result.scalar_one_or_none()
            if config:
                config.value = str(val)
                config.updated_at = _now()
            else:
                db.add(SystemConfig(key=key, value=str(val)))
        await db.commit()
        logger.info("系统配置已更新: keys=%s", list(items.keys()))

    @staticmethod
    async def get(db: AsyncSession, key: str) -> Optional[str]:
        """获取单个配置值"""
        result = await db.execute(select(SystemConfig).where(SystemConfig.key == key))
        config = result.scalar_one_or_none()
        return config.value if config else None
