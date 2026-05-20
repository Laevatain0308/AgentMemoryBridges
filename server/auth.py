"""认证中间件 + Token 管理 + Session 管理"""
import hashlib
import hmac
import logging
import secrets
import time
from typing import Optional

from fastapi import Request, Response
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, RedirectResponse

from models import Admin, Token

logger = logging.getLogger("agent-memory-bridge")

pwd_ctx = CryptContext(schemes=["bcrypt"])

# ── Web UI 公开路径（无需认证） ──────────────────────────────
PUBLIC_PATH_PREFIXES = ("/static/", "/.well-known/")
PUBLIC_PATHS = {"/", "/login", "/health", "/register", "/favicon.ico"}
PUBLIC_PATH_PREFIXES_GET = ("/memory/",)


# ── Web UI 管理路径（需 session） ───────────────────────────
ADMIN_PATH_PREFIXES = ("/admin",)

# ── Admin API 路径（可用 Session Cookie 代替 Bearer Token） ──
ADMIN_API_PREFIXES = ("/api/v1/tokens", "/api/v1/config")


# ── Session 管理（Fernet 签名 Cookie，跨 worker 共享） ────────
import os as _os
from cryptography.fernet import Fernet

_SESSION_FERNET = None


def _get_fernet() -> Fernet:
    """获取 Fernet 实例（延迟初始化）"""
    global _SESSION_FERNET
    if _SESSION_FERNET is None:
        key = _os.getenv("ENCRYPTION_KEY", "")
        if key:
            _SESSION_FERNET = Fernet(key.encode() if isinstance(key, str) else key)
        else:
            _SESSION_FERNET = Fernet(Fernet.generate_key())
    return _SESSION_FERNET


class SessionManager:
    """基于 Fernet 签名 Cookie 的 Session 管理，多 worker 安全"""

    @classmethod
    async def init_from_db(cls, session_factory) -> None:
        """从数据库读取/持久化 Session 加密密钥，在 lifespan 中调用"""
        global _SESSION_FERNET
        async with session_factory() as db:
            from service import ConfigService
            key = await ConfigService.get(db, "session_encryption_key")
            if key:
                _SESSION_FERNET = Fernet(key.encode())
            else:
                new_key = Fernet.generate_key().decode()
                await ConfigService.update(db, {"session_encryption_key": new_key})
                _SESSION_FERNET = Fernet(new_key.encode())

    @classmethod
    def create(cls) -> str:
        """创建 session cookie 值：加密 'admin|<timestamp>'"""
        payload = f"admin|{time.time() + 86400}"
        return _get_fernet().encrypt(payload.encode()).decode()

    @classmethod
    def is_valid(cls, session_value: str) -> bool:
        """验证 session cookie 是否有效且未过期"""
        try:
            plain = _get_fernet().decrypt(session_value.encode()).decode()
            _role, expiry_str = plain.split("|", 1)
            return time.time() < float(expiry_str)
        except Exception:
            return False

    @classmethod
    def create_flash(cls, data: str) -> str:
        """创建一次性闪存 Cookie（120s 有效）"""
        payload = f"flash|{time.time() + 120}|{data}"
        return _get_fernet().encrypt(payload.encode()).decode()

    @classmethod
    def read_flash(cls, flash_value: str) -> str | None:
        """读取并验证闪存 Cookie，返回数据或 None"""
        try:
            plain = _get_fernet().decrypt(flash_value.encode()).decode()
            parts = plain.split("|", 2)
            if parts[0] != "flash" or time.time() > float(parts[1]):
                return None
            return parts[2]
        except Exception:
            return None


# ── BearerTokenMiddleware ────────────────────────────────────
class BearerTokenMiddleware(BaseHTTPMiddleware):
    """双轨认证中间件：
    - API/MCP 路径：检查 Bearer Token
    - Web UI 管理路径：检查 Session Cookie
    - 公开路径：直接放行
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        # 公开路径直接放行
        if path in PUBLIC_PATHS:
            return await call_next(request)
        if path == "/login" and request.method == "GET":
            return await call_next(request)
        if any(path.startswith(p) for p in PUBLIC_PATH_PREFIXES):
            return await call_next(request)
        if any(path.startswith(p) for p in PUBLIC_PATH_PREFIXES_GET):
            return await call_next(request)

        # Web UI 管理路径 —— Session Cookie 认证
        if any(path.startswith(p) for p in ADMIN_PATH_PREFIXES):
            session_id = request.cookies.get("admin_session")
            if session_id and SessionManager.is_valid(session_id):
                request.state.admin = True
                return await call_next(request)
            login_url = request.scope.get("root_path", "") + "/login"
            return RedirectResponse(url=login_url, status_code=302)

        # POST /login —— 特殊处理（由路由自行验证密码）
        if path == "/login" and request.method == "POST":
            return await call_next(request)

        # Admin API 路径 —— 可用 Session Cookie 代替 Bearer Token
        if any(path.startswith(p) for p in ADMIN_API_PREFIXES):
            session_id = request.cookies.get("admin_session")
            if session_id and SessionManager.is_valid(session_id):
                request.state.admin = True
                return await call_next(request)

        # API / MCP 路径 —— Bearer Token 认证
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(status_code=401, content={"error": "missing_token"})

        raw_token = auth_header[7:]
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

        from database import async_session
        async with async_session() as db:
            result = await db.execute(
                select(Token).where(Token.token_hash == token_hash)
            )
            token_record = result.scalar_one_or_none()

            if token_record is None or token_record.revoked == 1:
                return JSONResponse(status_code=401, content={"error": "invalid_token"})

            # 注入设备标识和 token id（同时写入 state 和 scope，兼容 FastAPI 和 MCP）
            request.state.device_id = token_record.device_id
            request.state.device_label = token_record.label
            request.state.token_id = token_record.id
            request.scope["device_id"] = token_record.device_id
            request.scope["device_label"] = token_record.label
            request.scope["token_id"] = token_record.id

            # 更新最后使用时间（跟随容器系统时区）
            token_record.last_used_at = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
            await db.commit()

        return await call_next(request)


# ── Token 操作 ────────────────────────────────────────────────
async def device_id_exists(db: AsyncSession, device_id: str) -> bool:
    """检查 device_id 是否已被有效 Token 占用"""
    result = await db.execute(
        select(Token).where(Token.device_id == device_id, Token.revoked == 0)
    )
    return result.scalar_one_or_none() is not None


async def create_token(db: AsyncSession, label: str, device_id: str) -> str:
    """生成 Token，返回原始值（仅此一次），存储 SHA256 哈希"""
    raw_token = "sk-" + secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    token_prefix = raw_token[:7]  # "sk-xxxx"

    token = Token(
        token_hash=token_hash,
        token_prefix=token_prefix,
        label=label,
        device_id=device_id,
    )
    db.add(token)
    await db.commit()
    logger.info("Token 已创建: prefix=%s device_id=%s label=%s", token_prefix, device_id, label)
    return raw_token


async def revoke_token(db: AsyncSession, token_id: str) -> bool:
    """吊销 Token"""
    result = await db.execute(select(Token).where(Token.id == token_id))
    token = result.scalar_one_or_none()
    if token is None:
        return False
    token.revoked = 1
    await db.commit()
    logger.info("Token 已吊销: id=%s", token_id)
    return True


async def list_tokens(db: AsyncSession) -> list[dict]:
    """返回有效 Token 列表（不含 hash，仅前缀；已吊销的不返回）"""
    result = await db.execute(
        select(Token).where(Token.revoked == 0).order_by(Token.created_at.desc())
    )
    tokens = result.scalars().all()
    return [
        {
            "id": t.id,
            "token_prefix": t.token_prefix,
            "label": t.label,
            "device_id": t.device_id,
            "last_used_at": t.last_used_at,
            "created_at": t.created_at,
            "revoked": bool(t.revoked),
        }
        for t in tokens
    ]


# ── Admin 操作 ────────────────────────────────────────────────
async def verify_admin_password(db: AsyncSession, username: str, password: str) -> bool:
    """验证管理员密码"""
    result = await db.execute(select(Admin).where(Admin.username == username))
    admin = result.scalar_one_or_none()
    if admin is None:
        return False
    return pwd_ctx.verify(password, admin.password_hash)
