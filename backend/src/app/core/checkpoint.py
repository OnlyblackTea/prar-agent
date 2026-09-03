"""Task 20 Git checkpoint：沙箱根目录内每成功 step 一 commit，为 M4-26 局部 rerun 铺路。"""

import asyncio
from pathlib import Path

from app.tools.base import ToolExecutionError


def _step_id_of(subject: str, prefix: str) -> str | None:
    """从 `[prar:v{n}:{step_id}] title`（或其 `Revert "..."` 包裹）提取 step_id。

    不匹配 prefix（基线 init / 用户手工提交）返回 None。
    """
    if subject.startswith('Revert "') and subject.endswith('"'):
        subject = subject[len('Revert "'):-1]
    if not subject.startswith(prefix):
        return None
    return subject[len(prefix):].split("]", 1)[0]


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

    async def rollback_to(self, *, plan_version: int, step_id: str) -> int:
        """回退到 step_id 执行前的工作区状态，返回 revert 掉的 commit 条数。

        倒序 revert 保留历史（线性可审计）。幂等：目标不在历史中（失败 step 无
        commit）或已被 Revert 提交撤销 → 不收集，只 clean 返回 0；revert 中途
        中断后重试只补做未撤销的那几条。
        """
        await self._run_git("reset", "--hard", "HEAD")
        await self._run_git("clean", "-fd")
        prefix = f"[prar:v{plan_version}:"
        reverted: set[str] = set()
        collected: list[str] = []
        for line in (await self._run_git("log", "--format=%H%x00%s")).splitlines():
            sha, sep, subject = line.partition("\x00")
            if not sep:
                continue
            sid = _step_id_of(subject, prefix)
            if sid is None:
                # 基线 / 用户手工提交：跳过但继续向前找
                continue
            if subject.startswith('Revert "'):
                reverted.add(sid)
            elif sid not in reverted:
                collected.append(sha)
            if sid == step_id:
                break
        else:
            collected = []
        for sha in collected:
            await self._run_git("revert", "--no-edit", sha)
        return len(collected)

    async def _run_git(self, *args: str) -> str:
        cmd = (
            "git",
            "-c",
            f"user.name={self._author_name}",
            "-c",
            f"user.email={self._author_email}",
            # 宿主 gpgsign 会唤起 pinentry 卡死沙箱提交
            "-c",
            "commit.gpgsign=false",
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
