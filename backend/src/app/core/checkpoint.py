"""Task 20 Git checkpoint：沙箱根目录内每成功 step 一 commit，为 M4-26 局部 rerun 铺路。"""

import asyncio
from pathlib import Path

from app.tools.base import ToolExecutionError


class GitCheckpoint:
    """对沙箱根目录执行 git CLI 操作；git 缺失/超时/失败均视为环境故障停机。"""

    def __init__(
        self,
        repo_root: Path,
        *,
        author_name: str = "prar-agent",
        author_email: str = "agent@prar.local",
        timeout: float = 30.0,
    ) -> None:
        self._root = repo_root
        self._author_name = author_name
        self._author_email = author_email
        self._timeout = timeout

    async def init(self, *, plan_version: int) -> None:
        """git init + 基线空 commit；幂等，重复调用追加新基线。"""
        await self._run_git("init", "-q")
        await self._run_git("commit", "--allow-empty", "-m", f"[prar:v{plan_version}] init")

    async def commit_step(self, *, plan_version: int, step_id: str, title: str) -> str:
        """提交当前工作树全部变更（含空变更），返回 40 位 commit hash。"""
        await self._run_git("add", "-A")
        await self._run_git(
            "commit", "--allow-empty", "-m", f"[prar:v{plan_version}:{step_id}] {title}",
        )
        return (await self._run_git("rev-parse", "HEAD")).strip()

    async def _run_git(self, *args: str) -> str:
        cmd = (
            "git",
            "-c",
            f"user.name={self._author_name}",
            "-c",
            f"user.email={self._author_email}",
            *args,
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=self._root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as e:
            raise ToolExecutionError(f"git spawn failed: {e}") from e
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except TimeoutError as e:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise ToolExecutionError(f"git timed out after {self._timeout}s") from e
        if proc.returncode != 0:
            detail = stderr_b.decode(errors="replace").strip()
            raise ToolExecutionError(f"git {' '.join(args)} failed: {detail}")
        return stdout_b.decode(errors="replace")
