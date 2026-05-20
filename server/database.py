"""异步数据库引擎、WAL 配置、FTS5 全文索引、启动初始化"""
import asyncio
import logging
import os
import secrets
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from models import Base

logger = logging.getLogger("agent-memory-bridge")

DATABASE_URL = "sqlite+aiosqlite:///./data/bridge.db"

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=3,
    max_overflow=5,
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _exec_sql(session: AsyncSession, sql: str) -> None:
    """执行原生 SQL（用于 PRAGMA / FTS / 索引等 DDL）"""
    await session.execute(text(sql))


async def init_db() -> None:
    """创建表、FTS5 虚拟表、触发器、索引（多 worker 并发安全）"""
    import os as _os
    _os.makedirs("data", exist_ok=True)

    # 多 worker 并发启动时可能同时写 SQLite，添加重试逻辑
    max_retries = 5
    for attempt in range(max_retries):
        try:
            async with engine.begin() as conn:
                await conn.execute(text("PRAGMA journal_mode=WAL"))
                await conn.execute(text("PRAGMA synchronous=NORMAL"))
                await conn.execute(text("PRAGMA foreign_keys=ON"))

            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

            # FTS5 虚拟表 + 触发器
            async with engine.begin() as conn:
                await conn.execute(text("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                        title, content, tags,
                        content_rowid='rowid',
                        tokenize='unicode61'
                    )
                """))
                for op, timing in [("INSERT", "AFTER"), ("DELETE", "AFTER"), ("UPDATE", "AFTER")]:
                    if op == "INSERT":
                        body = "INSERT INTO memories_fts(rowid, title, content, tags) VALUES (new.rowid, new.title, new.content, new.tags)"
                    elif op == "DELETE":
                        body = "INSERT INTO memories_fts(memories_fts, rowid, title, content, tags) VALUES ('delete', old.rowid, old.title, old.content, old.tags)"
                    else:
                        body = ("INSERT INTO memories_fts(memories_fts, rowid, title, content, tags) VALUES ('delete', old.rowid, old.title, old.content, old.tags);"
                                "INSERT INTO memories_fts(rowid, title, content, tags) VALUES (new.rowid, new.title, new.content, new.tags)")
                    sql = f"CREATE TRIGGER IF NOT EXISTS tr_memories_fts_{op.lower()} {timing} {op} ON memories BEGIN {body}; END"
                    await conn.execute(text(sql))

            # 复合索引
            async with engine.begin() as conn:
                await conn.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_memories_project_category_created
                        ON memories(project, category, created_at DESC)
                """))
                await conn.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_sessions_project_created
                        ON sessions(project, created_at DESC)
                """))

            logger.info("数据库初始化完成（WAL、FTS5、索引）")
            return
        except OperationalError as e:
            if attempt < max_retries - 1:
                delay = 0.5 * (2 ** attempt)
                logger.warning("数据库初始化冲突（attempt %d/%d），%0.1fs 后重试: %s",
                               attempt + 1, max_retries, delay, e)
                await asyncio.sleep(delay)
            else:
                raise


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：提供异步数据库会话"""
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()


async def seed_defaults(db: AsyncSession) -> None:
    """首次启动时创建默认管理员和 system_config 项"""
    from models import Admin, SystemConfig

    # 创建默认 admin
    from sqlalchemy import select
    result = await db.execute(select(Admin).limit(1))
    if result.scalar_one_or_none() is None:
        password = os.getenv("ADMIN_PASSWORD")
        if not password:
            password = secrets.token_urlsafe(12)
        from passlib.context import CryptContext
        pwd_ctx = CryptContext(schemes=["bcrypt"])
        admin = Admin(username="admin", password_hash=pwd_ctx.hash(password))
        db.add(admin)
        await db.commit()
        if os.getenv("ADMIN_PASSWORD"):
            logger.info("管理员账号已创建: 用户名=admin 密码=%s (来自 ADMIN_PASSWORD 环境变量)", password)
        else:
            logger.warning("未设置 ADMIN_PASSWORD，已随机生成管理员账号: 用户名=admin 密码=%s", password)
    else:
        env_password = os.getenv("ADMIN_PASSWORD")
        if env_password:
            logger.info("管理员账号已存在: 用户名=admin 当前 ADMIN_PASSWORD=%s", env_password)
        else:
            logger.info("管理员账号已存在: 用户名=admin（密码由首次启动时设置，未设置 ADMIN_PASSWORD 环境变量）")

    # 插入默认 system_config 项（不存在则插入）
    defaults = [
        ("session_encryption_key", "", "Session Cookie 加密密钥（Fernet），首次启动自动生成"),
        ("git_repo_url", "", "Git 仓库地址"),
        ("git_repo_branch", "main", "Git 备份目标分支"),
        ("git_user_name", "Agent Memory Bridge", "Git 提交用户名"),
        ("git_user_email", "bridge@localhost", "Git 提交邮箱"),
        ("git_token", "", "GitHub Personal Access Token（Fernet 加密存储）"),
        ("backup_interval_h", "6", "自动备份间隔（小时）"),
        ("backup_enabled", "true", "是否启用自动备份"),
    ]
    for key, value, desc in defaults:
        row = await db.execute(select(SystemConfig).where(SystemConfig.key == key))
        if row.scalar_one_or_none() is None:
            db.add(SystemConfig(key=key, value=value, description=desc))
    await db.commit()
    logger.info("默认 system_config 已就绪")
