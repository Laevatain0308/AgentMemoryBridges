"""Web UI 路由 —— Jinja2 模板渲染"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from auth import (
    SessionManager,
    verify_admin_password,
    create_token,
    device_id_exists,
    list_tokens as list_tokens_fn,
    revoke_token as revoke_token_fn,
)
from database import get_db
from service import MemoryService, ConfigService

logger = logging.getLogger("agent-memory-bridge")

router = APIRouter()
templates = Jinja2Templates(directory="templates")


# ── 辅助函数 ──────────────────────────────────────────────────
def _require_admin(request: Request):
    session_id = request.cookies.get("admin_session")
    if not session_id or not SessionManager.is_valid(session_id):
        raise HTTPException(status_code=302, headers={"Location": "/login"})


def _context(request: Request, **kwargs) -> dict:
    """构建模板上下文，包含认证状态"""
    session_id = request.cookies.get("admin_session")
    is_admin = SessionManager.is_valid(session_id) if session_id else False
    return {"request": request, "is_admin": is_admin, **kwargs}


# ── 公开页面 ──────────────────────────────────────────────────
@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    project: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    project_val = project or ""
    results = await MemoryService.search(db, project_val, category, query=q, limit=50)

    import json
    def _parse(val):
        try:
            return json.loads(val) if val else None
        except json.JSONDecodeError:
            return None

    projects = await MemoryService.get_projects(db)
    memories = [
        {
            "id": m.id,
            "project": m.project,
            "category": m.category,
            "title": m.title,
            "content": m.content[:200] + ("..." if len(m.content) > 200 else ""),
            "tags": _parse(m.tags),
            "source_device": m.source_device,
            "status": m.status,
            "created_at": m.created_at,
        }
        for m in results
    ]
    return templates.TemplateResponse(request, "index.html", _context(
        request,
        memories=memories,
        projects=projects,
        current_project=project_val,
        current_category=category or "",
        current_query=q or "",
        categories=["finding", "decision", "context", "note"],
    ))


@router.get("/memory/{memory_id}", response_class=HTMLResponse)
async def memory_detail(
    memory_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    memory = await MemoryService.get_by_id(db, memory_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="记忆不存在")

    import json
    return templates.TemplateResponse(request, "memory.html", _context(
        request,
        memory={
            "id": memory.id,
            "project": memory.project,
            "category": memory.category,
            "title": memory.title,
            "content": memory.content,
            "tags": json.loads(memory.tags) if memory.tags else [],
            "related_files": json.loads(memory.related_files) if memory.related_files else [],
            "related_urls": json.loads(memory.related_urls) if memory.related_urls else [],
            "source_device": memory.source_device,
            "status": memory.status,
            "created_at": memory.created_at,
            "updated_at": memory.updated_at,
        },
    ))


@router.post("/memory/{memory_id}/delete")
async def memory_delete(
    memory_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_admin(request)
    success = await MemoryService.delete(db, memory_id)
    if not success:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return RedirectResponse(url="/", status_code=302)


# ── 登录 ──────────────────────────────────────────────────────
@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", _context(request))


@router.post("/login")
async def login_action(
    request: Request,
    username: str = Form(default="admin"),
    password: str = Form(default=""),
    db: AsyncSession = Depends(get_db),
):
    if await verify_admin_password(db, username, password):
        session_id = SessionManager.create()
        response = RedirectResponse(url="/admin", status_code=302)
        # 根据反向代理头判断是否为 HTTPS 连接
        is_https = request.headers.get("X-Forwarded-Proto", "") == "https"
        response.set_cookie(
            "admin_session", session_id,
            httponly=True, samesite="lax", secure=is_https, max_age=86400,
        )
        return response
    return templates.TemplateResponse(request, "login.html", _context(request, error="密码错误"))


# ── 管理页面（需 admin session） ──────────────────────────────
@router.get("/admin", response_class=HTMLResponse)
async def admin_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_admin(request)
    tokens = await list_tokens_fn(db)
    # 读取闪存 Cookie 中的新 Token 信息
    new_token = None
    new_token_label = None
    new_token_device_id = None
    flash_cookie = request.cookies.get("token_flash")
    if flash_cookie:
        flash_data = SessionManager.read_flash(flash_cookie)
        if flash_data:
            parts = flash_data.split("|||", 2)
            if len(parts) == 3:
                new_token, new_token_label, new_token_device_id = parts
    kwargs = {"tokens": tokens}
    if new_token:
        kwargs["new_token"] = new_token
        kwargs["new_token_label"] = new_token_label
        kwargs["new_token_device_id"] = new_token_device_id
    response = templates.TemplateResponse(request, "admin.html", _context(request, **kwargs))
    if flash_cookie:
        response.delete_cookie("token_flash")
    return response


@router.post("/admin/tokens")
async def admin_create_token(
    request: Request,
    label: str = Form(default=""),
    device_id: str = Form(default=""),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(request)
    if not device_id.strip():
        raise HTTPException(status_code=400, detail="需要提供设备标识")
    if await device_id_exists(db, device_id.strip()):
        return templates.TemplateResponse(request, "admin.html", _context(
            request,
            tokens=await list_tokens_fn(db),
            error=f"设备标识 '{device_id.strip()}' 已有有效 Token，请先吊销旧 Token 再创建",
        ))
    raw_token = await create_token(db, label.strip(), device_id.strip())
    # 使用闪存 Cookie 传递新 Token 信息，避免刷新重复提交
    flash_data = f"{raw_token}|||{label.strip()}|||{device_id.strip()}"
    flash_value = SessionManager.create_flash(flash_data)
    response = RedirectResponse(url="/admin", status_code=302)
    response.set_cookie("token_flash", flash_value, httponly=True, samesite="lax", max_age=120)
    return response


@router.post("/admin/tokens/{token_id}/revoke")
async def admin_revoke_token(
    token_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_admin(request)
    await revoke_token_fn(db, token_id)
    return RedirectResponse(url="/admin", status_code=302)


@router.get("/admin/settings", response_class=HTMLResponse)
async def admin_settings(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_admin(request)
    config = await ConfigService.get_all(db)
    return templates.TemplateResponse(request, "settings.html", _context(request, config=config))


@router.post("/admin/settings")
async def admin_save_settings(
    request: Request,
    git_repo_url: str = Form(default=""),
    git_repo_branch: str = Form(default="main"),
    git_user_name: str = Form(default=""),
    git_user_email: str = Form(default=""),
    git_token: str = Form(default=""),
    backup_interval_h: str = Form(default="6"),
    backup_enabled: str = Form(default="true"),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(request)
    items = {
        "git_repo_url": git_repo_url,
        "git_repo_branch": git_repo_branch,
        "git_user_name": git_user_name,
        "git_user_email": git_user_email,
        "backup_interval_h": backup_interval_h,
        "backup_enabled": backup_enabled,
    }
    if git_token and git_token != "********":
        # 加密存储 git token
        from cryptography.fernet import Fernet
        import os
        key = os.getenv("ENCRYPTION_KEY", "")
        if key:
            try:
                f = Fernet(key.encode() if isinstance(key, str) else key)
                items["git_token"] = f.encrypt(git_token.encode()).decode()
            except Exception:
                items["git_token"] = git_token  # 回退：明文存储
        else:
            items["git_token"] = git_token  # 无密钥则明文存储
    await ConfigService.update(db, items)
    # 动态更新备份调度器间隔
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler:
        try:
            new_interval = int(backup_interval_h) if backup_interval_h.isdigit() else 6
            job = scheduler.get_job("git_backup")
            if job:
                scheduler.reschedule_job("git_backup", trigger="interval", hours=new_interval)
        except Exception:
            pass
    return RedirectResponse(url="/admin/settings", status_code=302)


@router.post("/admin/settings/test-git")
async def admin_test_git(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_admin(request)
    from backup import test_git_connection as _test_git
    result = await _test_git(db)
    color = "var(--pico-ins-color)" if result["status"] == "ok" else "var(--pico-del-color)"
    is_htmx = request.headers.get("HX-Request") == "true"
    return HTMLResponse(f'<p style="color:{color}">Git 连接测试：<strong>{result["status"]}</strong> — {result["message"]}</p>') if is_htmx else RedirectResponse(url="/admin/settings", status_code=302)


@router.post("/admin/settings/backup-now")
async def admin_backup_now(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_admin(request)
    from backup import run_backup_manual
    result = await run_backup_manual(db)
    color = "var(--pico-ins-color)" if result["status"] == "ok" else "var(--pico-del-color)"
    is_htmx = request.headers.get("HX-Request") == "true"
    return HTMLResponse(f'<p style="color:{color}">备份结果：<strong>{result["status"]}</strong> — {result["message"]}</p>') if is_htmx else RedirectResponse(url="/admin/settings", status_code=302)
