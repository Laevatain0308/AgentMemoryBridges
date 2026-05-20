"""REST API 路由 —— /api/v1/* 端点"""
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from auth import SessionManager
from database import get_db
from service import MemoryService, SessionService, TokenService, ConfigService

logger = logging.getLogger("agent-memory-bridge")

router = APIRouter(prefix="/api/v1")


# ── 认证依赖 ──────────────────────────────────────────────────
async def get_device_from_token(request: Request) -> str:
    """API Token 认证 —— 从中间件注入的 state 获取 device_id"""
    device_id = getattr(request.state, "device_id", None)
    if device_id is None:
        raise HTTPException(status_code=401, detail="missing_token")
    return device_id


async def require_admin(request: Request) -> bool:
    """Session 认证 —— 检查 admin session cookie"""
    session_id = request.cookies.get("admin_session")
    if not session_id or not SessionManager.is_valid(session_id):
        raise HTTPException(status_code=401, detail="unauthorized")
    return True


# ── 请求/响应模型 ─────────────────────────────────────────────
class MemoryCreate(BaseModel):
    project: str = ""
    category: str = "note"
    title: str = ""
    content: str = ""
    tags: Optional[list] = None
    related_files: Optional[list] = None
    related_urls: Optional[list] = None


class MemoryUpdate(BaseModel):
    content: Optional[str] = None
    status: Optional[str] = None
    tags: Optional[list] = None
    title: Optional[str] = None
    category: Optional[str] = None
    project: Optional[str] = None


class SessionCreate(BaseModel):
    project: str = ""
    summary: str = ""
    key_points: Optional[list] = None
    memory_refs: Optional[list] = None


class TokenCreate(BaseModel):
    label: str
    device_id: str


class ConfigUpdate(BaseModel):
    items: dict


# ── Memory 端点 ───────────────────────────────────────────────
@router.get("/memories")
async def list_memories(
    project: str = Query(default=""),
    category: Optional[str] = None,
    tags: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _device: str = Depends(get_device_from_token),
):
    results = await MemoryService.search(db, project, category, tags, q, limit, offset)
    return _memories_response(results)


@router.post("/memories", status_code=201)
async def create_memory(
    body: MemoryCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _device: str = Depends(get_device_from_token),
):
    device_id = getattr(request.state, "device_id", "unknown")
    data = body.model_dump()
    memory = await MemoryService.create(db, data, device_id)
    return _memory_to_dict(memory)


@router.put("/memories/{memory_id}")
async def update_memory(
    memory_id: str,
    body: MemoryUpdate,
    db: AsyncSession = Depends(get_db),
    _device: str = Depends(get_device_from_token),
):
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    if not data:
        raise HTTPException(status_code=400, detail="no_fields_to_update")
    memory = await MemoryService.update(db, memory_id, data)
    if memory is None:
        raise HTTPException(status_code=404, detail="not_found")
    return _memory_to_dict(memory)


@router.delete("/memories/{memory_id}", status_code=204)
async def delete_memory(
    memory_id: str,
    db: AsyncSession = Depends(get_db),
    _admin: bool = Depends(require_admin),
):
    success = await MemoryService.delete(db, memory_id)
    if not success:
        raise HTTPException(status_code=404, detail="not_found")


# ── Session 端点 ──────────────────────────────────────────────
@router.get("/sessions")
async def list_sessions(
    project: Optional[str] = None,
    device: Optional[str] = None,
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _device: str = Depends(get_device_from_token),
):
    results = await SessionService.get_recent(db, project, limit)
    return [
        {
            "id": s.id,
            "project": s.project,
            "source_device": s.source_device,
            "summary": s.summary,
            "key_points": _try_parse_json(s.key_points),
            "memory_refs": _try_parse_json(s.memory_refs),
            "created_at": s.created_at,
        }
        for s in results
        if device is None or s.source_device == device
    ]


@router.post("/sessions", status_code=201)
async def create_session(
    body: SessionCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _device: str = Depends(get_device_from_token),
):
    device_id = getattr(request.state, "device_id", "unknown")
    session = await SessionService.create(db, body.model_dump(), device_id)
    return {
        "id": session.id,
        "project": session.project,
        "source_device": session.source_device,
        "summary": session.summary,
        "key_points": _try_parse_json(session.key_points),
        "memory_refs": _try_parse_json(session.memory_refs),
        "created_at": session.created_at,
    }


# ── 辅助端点 ──────────────────────────────────────────────────
@router.get("/projects")
async def list_projects(
    db: AsyncSession = Depends(get_db),
    _device: str = Depends(get_device_from_token),
):
    return await MemoryService.get_projects(db)


@router.get("/stats")
async def get_stats(
    project: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    _device: str = Depends(get_device_from_token),
):
    return await MemoryService.get_stats(db, project)


# ── Token 端点（需 admin session） ──────────────────────────────
@router.post("/tokens", status_code=201)
async def create_token(
    body: TokenCreate,
    db: AsyncSession = Depends(get_db),
    _admin: bool = Depends(require_admin),
):
    raw_token = await TokenService.create(db, body.label, body.device_id)
    return {"token": raw_token, "label": body.label, "device_id": body.device_id, "message": "请保存此 Token，仅显示一次"}


@router.get("/tokens")
async def list_tokens(
    db: AsyncSession = Depends(get_db),
    _admin: bool = Depends(require_admin),
):
    return await TokenService.list_tokens(db)


@router.delete("/tokens/{token_id}", status_code=204)
async def revoke_token(
    token_id: str,
    db: AsyncSession = Depends(get_db),
    _admin: bool = Depends(require_admin),
):
    success = await TokenService.revoke(db, token_id)
    if not success:
        raise HTTPException(status_code=404, detail="not_found")


# ── Config 端点（需 admin session） ────────────────────────────
@router.get("/config")
async def get_config(
    db: AsyncSession = Depends(get_db),
    _admin: bool = Depends(require_admin),
):
    return await ConfigService.get_all(db)


@router.put("/config")
async def update_config(
    body: ConfigUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: bool = Depends(require_admin),
):
    await ConfigService.update(db, body.items)
    return {"status": "ok"}


@router.post("/config/test-git")
async def test_git_connection(
    db: AsyncSession = Depends(get_db),
    _admin: bool = Depends(require_admin),
):
    from backup import test_git_connection as _test_git
    result = await _test_git(db)
    return result


@router.post("/config/backup-now")
async def backup_now(
    db: AsyncSession = Depends(get_db),
    _admin: bool = Depends(require_admin),
):
    from backup import run_backup_manual
    result = await run_backup_manual(db)
    return result


# ── 辅助函数 ──────────────────────────────────────────────────
def _try_parse_json(val):
    if val is None:
        return None
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return val


def _memory_to_dict(m) -> dict:
    return {
        "id": m.id,
        "project": m.project,
        "category": m.category,
        "title": m.title,
        "content": m.content,
        "tags": _try_parse_json(m.tags),
        "related_files": _try_parse_json(m.related_files),
        "related_urls": _try_parse_json(m.related_urls),
        "source_device": m.source_device,
        "status": m.status,
        "created_at": m.created_at,
        "updated_at": m.updated_at,
    }


def _memories_response(results: list) -> list[dict]:
    return [_memory_to_dict(m) for m in results]
