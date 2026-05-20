"""Agent Memory Bridge —— FastAPI + FastMCP 入口"""
import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# 确保 server 目录在 sys.path 中（Docker 内 WORKDIR 已在 server/）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("agent-memory-bridge")

# 在导入其他模块前先确保 data 目录存在
os.makedirs("data", exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动
    logger.info("Agent Memory Bridge 正在启动...")
    from database import init_db, async_session, seed_defaults
    await init_db()
    async with async_session() as db:
        await seed_defaults(db)
    logger.info("数据库已就绪")

    # 初始化 Session 加密密钥（从 DB 读取或生成并持久化）
    from auth import SessionManager
    await SessionManager.init_from_db(async_session)
    logger.info("Session 加密密钥已就绪")

    # 启动备份调度器
    from backup import setup_scheduler
    from service import ConfigService
    async with async_session() as db:
        interval_str = await ConfigService.get(db, "backup_interval_h")
        interval = int(interval_str) if interval_str and interval_str.isdigit() else 6
    app.state.scheduler = setup_scheduler(async_session, interval)

    logger.info("Agent Memory Bridge 启动完成")
    yield
    # 关闭
    if hasattr(app.state, "scheduler"):
        app.state.scheduler.shutdown(wait=False)
    from database import engine
    await engine.dispose()
    logger.info("Agent Memory Bridge 已关闭")


# 创建 FastAPI 应用
app = FastAPI(
    title="Agent Memory Bridge",
    lifespan=lifespan,
)

# 注册认证中间件（需在路由之前添加）
from auth import BearerTokenMiddleware
app.add_middleware(BearerTokenMiddleware)

# 注册 REST API 路由
from api_v1 import router as api_router
app.include_router(api_router)

# 注册 Web UI 路由
from web import router as web_router
app.include_router(web_router)

# 挂载 FastMCP SSE 端点（FastMCP 3.x 使用 http_app）
from mcp_tools import mcp
mcp_app = mcp.http_app(transport="sse")
app.mount("/mcp", mcp_app)

# 挂载静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")

# ── 健康检查 ──────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": "agent-memory-bridge"}


# ── 入口 ──────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=3004,
        workers=1,
        limit_max_requests=10000,
    )
