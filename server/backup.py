"""Git 定时备份 —— 导出 Markdown → Git commit → push"""
import json
import logging
import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from service import ConfigService

logger = logging.getLogger("agent-memory-bridge")

BACKUP_DIR = Path("backup")
_push_lock = False  # 推送互斥锁（单 worker 下 bool 足够）


def _fmt_json(val: str | None) -> str:
    """将 JSON 字符串转为可读的 Markdown 列表格式"""
    if not val:
        return ""
    try:
        items = json.loads(val)
        if isinstance(items, list):
            return ", ".join(str(i) for i in items)
        return str(items)
    except (json.JSONDecodeError, TypeError):
        return val


async def _export_to_markdown(db: AsyncSession) -> str:
    """将 memories + sessions 导出为 Markdown 文件，返回导出目录路径"""
    from models import Memory, AgentSession
    BACKUP_DIR.mkdir(exist_ok=True)

    timestamp = datetime.now().astimezone().strftime("%Y-%m-%dT%H%M%SZ")

    # 导出 memories（按项目分组）
    result = await db.execute(select(Memory).order_by(Memory.project, Memory.created_at.desc()))
    memories = result.scalars().all()

    # 按项目分组
    mem_by_project: dict[str, list] = {}
    for m in memories:
        mem_by_project.setdefault(m.project, []).append(m)
    project_order = sorted(mem_by_project.keys())

    mem_path = BACKUP_DIR / f"memories-{timestamp}.md"
    with open(mem_path, "w", encoding="utf-8") as f:
        f.write("# Agent Memory Bridge — 记忆备份\n\n")
        f.write(f"导出时间：{timestamp}\n\n")
        f.write(f"总计 {len(memories)} 条记忆，{len(project_order)} 个项目\n\n")
        f.write("| 项目 | 数量 |\n|------|------|\n")
        for proj in project_order:
            f.write(f"| {proj} | {len(mem_by_project[proj])} |\n")
        f.write("\n---\n\n")
        for proj in project_order:
            items = mem_by_project[proj]
            f.write(f"## {proj}（{len(items)} 条）\n\n")
            for m in items:
                f.write(f"### {m.title}\n\n")
                f.write(f"- **ID**: `{m.id}`\n")
                f.write(f"- **分类**: {m.category}\n")
                f.write(f"- **来源设备**: {m.source_device}\n")
                f.write(f"- **状态**: {m.status}\n")
                f.write(f"- **标签**: {_fmt_json(m.tags)}\n")
                f.write(f"- **关联文件**: {_fmt_json(m.related_files)}\n")
                f.write(f"- **创建时间**: {m.created_at}\n")
                f.write(f"- **更新时间**: {m.updated_at}\n\n")
                f.write(f"{m.content}\n\n---\n\n")

    # 导出 sessions（按项目分组）
    result = await db.execute(select(AgentSession).order_by(AgentSession.project, AgentSession.created_at.desc()))
    sessions = result.scalars().all()

    sess_by_project: dict[str, list] = {}
    for s in sessions:
        sess_by_project.setdefault(s.project, []).append(s)
    sess_project_order = sorted(sess_by_project.keys())

    sess_path = BACKUP_DIR / f"sessions-{timestamp}.md"
    with open(sess_path, "w", encoding="utf-8") as f:
        f.write("# Agent Memory Bridge — 会话备份\n\n")
        f.write(f"导出时间：{timestamp}\n\n")
        f.write(f"总计 {len(sessions)} 条会话，{len(sess_project_order)} 个项目\n\n")
        f.write("| 项目 | 数量 |\n|------|------|\n")
        for proj in sess_project_order:
            f.write(f"| {proj} | {len(sess_by_project[proj])} |\n")
        f.write("\n---\n\n")
        for proj in sess_project_order:
            items = sess_by_project[proj]
            f.write(f"## {proj}（{len(items)} 条）\n\n")
            for s in items:
                f.write(f"### 会话 {s.id[:8]}\n\n")
                f.write(f"- **来源设备**: {s.source_device}\n")
                f.write(f"- **关键点**: {_fmt_json(s.key_points)}\n")
                f.write(f"- **关联记忆**: {_fmt_json(s.memory_refs)}\n")
                f.write(f"- **时间**: {s.created_at}\n\n")
                f.write(f"{s.summary}\n\n---\n\n")

    logger.info("备份已导出: memories=%d sessions=%d", len(memories), len(sessions))
    return str(BACKUP_DIR)


async def _git_commit_and_push(db: AsyncSession) -> dict:
    """执行 Git add → commit → push"""
    global _push_lock
    if _push_lock:
        return {"status": "error", "message": "正在推送中，请稍后再试"}
    _push_lock = True
    try:
        from git import Repo, Actor

        repo_url = await ConfigService.get(db, "git_repo_url")
        repo_branch = await ConfigService.get(db, "git_repo_branch") or "main"
        user_name = await ConfigService.get(db, "git_user_name") or "Agent Memory Bridge"
        user_email = await ConfigService.get(db, "git_user_email") or "bridge@localhost"
        git_token = await ConfigService.get(db, "git_token")

        if not repo_url:
            return {"status": "error", "message": "Git 仓库地址未配置"}

        # 准备仓库目录
        repo_path = BACKUP_DIR / "repo"
        if not (repo_path / ".git").exists():
            # 克隆仓库（带 token 认证）
            if git_token and git_token != "********":
                # 解密 git_token
                decrypted_token = _decrypt_token(git_token)
                auth_url = repo_url.replace("https://", f"https://{decrypted_token}@")
            else:
                auth_url = repo_url
            Repo.clone_from(auth_url, str(repo_path), branch=repo_branch)

        repo = Repo(str(repo_path))
        origin = repo.remote("origin")

        # 同步远程状态（处理远程文件被删除等情况）
        origin.fetch()
        try:
            repo.git.reset("--hard", f"origin/{repo_branch}")
        except Exception:
            pass  # 首次克隆后 reset 可能失败，忽略

        # 复制导出的文件到仓库
        for f in BACKUP_DIR.glob("memories-*.md"):
            target = repo_path / f.name
            with open(f, "r", encoding="utf-8") as src:
                target.write_text(src.read(), encoding="utf-8")
            repo.index.add([str(target.relative_to(repo_path))])
        for f in BACKUP_DIR.glob("sessions-*.md"):
            target = repo_path / f.name
            with open(f, "r", encoding="utf-8") as src:
                target.write_text(src.read(), encoding="utf-8")
            repo.index.add([str(target.relative_to(repo_path))])

        # commit
        if repo.index.diff("HEAD") or repo.untracked_files:
            actor = Actor(user_name, user_email)
            commit_msg = f"Auto backup — {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M')}"
            repo.index.commit(commit_msg, author=actor, committer=actor)
            push_info = origin.push()
            # 检查 push 是否成功
            if push_info and push_info[0].flags & push_info[0].ERROR:
                error_msg = push_info[0].summary
                logger.error("Git push 失败: %s", error_msg)
                return {"status": "error", "message": f"推送失败: {error_msg}"}
            # 推送成功后清理本地导出文件，避免下次备份重复提交
            for f in BACKUP_DIR.glob("memories-*.md"):
                f.unlink(missing_ok=True)
            for f in BACKUP_DIR.glob("sessions-*.md"):
                f.unlink(missing_ok=True)
            logger.info("Git 备份已推送")
            return {"status": "ok", "message": "备份已推送到远程仓库"}
        else:
            return {"status": "ok", "message": "无变更，跳过推送"}

    except Exception as e:
        logger.exception("Git 备份失败: %s", e)
        return {"status": "error", "message": str(e)}
    finally:
        _push_lock = False


def _decrypt_token(encrypted: str) -> str:
    """解密 git_token"""
    from cryptography.fernet import Fernet
    key = os.getenv("ENCRYPTION_KEY", "")
    if not key:
        return encrypted
    try:
        f = Fernet(key.encode() if isinstance(key, str) else key)
        return f.decrypt(encrypted.encode()).decode()
    except Exception:
        return encrypted  # 可能未加密


async def run_backup(db_session_factory) -> None:
    """执行完整备份流程（由调度器调用）"""
    async with db_session_factory() as db:
        enabled = await ConfigService.get(db, "backup_enabled")
        if enabled != "true":
            logger.info("自动备份已禁用，跳过")
            return

        await _export_to_markdown(db)
        result = await _git_commit_and_push(db)

        # 记录备份状态
        timestamp = datetime.now().astimezone().isoformat()
        await ConfigService.update(db, {
            "last_backup_at": timestamp,
            "last_backup_status": result["status"],
            "last_backup_message": result.get("message", ""),
        })


async def run_backup_manual(db: AsyncSession) -> dict:
    """手动触发备份（从 API 调用，已有 db session）"""
    try:
        await _export_to_markdown(db)
        result = await _git_commit_and_push(db)
        timestamp = datetime.now().astimezone().isoformat()
        await ConfigService.update(db, {
            "last_backup_at": timestamp,
            "last_backup_status": result["status"],
            "last_backup_message": result.get("message", ""),
        })
        return result
    except Exception as e:
        logger.exception("手动备份失败: %s", e)
        return {"status": "error", "message": str(e)}


async def test_git_connection(db: AsyncSession) -> dict:
    """测试 Git 连接（使用 git ls-remote 无需本地仓库）"""
    try:
        repo_url = await ConfigService.get(db, "git_repo_url")
        if not repo_url:
            return {"status": "error", "message": "Git 仓库地址未配置"}

        git_token = await ConfigService.get(db, "git_token")
        if git_token and git_token != "********":
            decrypted = _decrypt_token(git_token)
            auth_url = repo_url.replace("https://", f"https://{decrypted}@")
        else:
            # 无 Token 时用原始 URL（仅公开仓库可通）
            auth_url = repo_url

        # git ls-remote 是最轻量的连接验证方式，不需要本地仓库
        from git.cmd import Git
        g = Git()
        g.ls_remote(auth_url)
        return {"status": "ok", "message": "Git 连接成功"}
    except Exception as e:
        error_msg = str(e)
        # 精简 git 错误信息（按优先级匹配）
        if "could not read Username" in error_msg or "terminal prompts disabled" in error_msg:
            error_msg = "仓库需要认证，请填写 GitHub Token"
        elif "Authentication failed" in error_msg or "Invalid username" in error_msg or "401" in error_msg:
            error_msg = "Token 认证失败，请检查 GitHub Token 是否有效"
        elif "not found" in error_msg.lower() or "Repository not found" in error_msg:
            error_msg = "仓库不存在或无访问权限（私有仓库需填写 Token）"
        elif "Could not resolve host" in error_msg or "No such device or address" in error_msg:
            error_msg = "无法连接仓库服务器，请检查网络或 URL 是否正确"
        logger.error("Git 连接测试失败: %s", error_msg)
        return {"status": "error", "message": error_msg}


def setup_scheduler(db_session_factory, interval_hours: int = 6):
    """配置 APScheduler 定时备份"""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_backup,
        "interval",
        hours=interval_hours,
        args=[db_session_factory],
        id="git_backup",
        name="Git 自动备份",
    )
    scheduler.start()
    logger.info("备份调度器已启动（间隔 %d 小时）", interval_hours)
    return scheduler
