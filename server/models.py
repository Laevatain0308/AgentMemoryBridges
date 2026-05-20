"""SQLAlchemy async 模型定义 —— Agent Memory Bridge"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, Integer
from sqlalchemy.orm import DeclarativeBase, Mapped, MappedAsDataclass, mapped_column


def _new_id() -> str:
    return uuid.uuid4().hex


def _now() -> str:
    return datetime.now().astimezone().isoformat()


class Base(MappedAsDataclass, DeclarativeBase):
    pass


class Memory(Base):
    """Agent 记忆条目"""
    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default_factory=_new_id)
    project: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="note")
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tags: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    related_files: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    related_urls: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    source_device: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default_factory=_now)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False, default_factory=_now)


class AgentSession(Base):
    """会话摘要（类名 AgentSession 避免与 SQLAlchemy 的 Session 冲突）"""
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default_factory=_new_id)
    project: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    source_device: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    key_points: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    memory_refs: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default_factory=_now)


class Token(Base):
    """API Token —— 无默认值字段必须在带默认值字段之前"""
    __tablename__ = "tokens"

    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default_factory=_new_id)
    token_prefix: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    device_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    last_used_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, default=None)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default_factory=_now)
    revoked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Admin(Base):
    """管理员凭据 —— 无默认值字段必须在带默认值字段之前"""
    __tablename__ = "admin"

    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default_factory=_new_id)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False, default="")


class SystemConfig(Base):
    """系统配置 key-value"""
    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False, default_factory=_now)
