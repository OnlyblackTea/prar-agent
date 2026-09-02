"""Task 20 GitCheckpoint 测试（真实 git CLI 集成测试，tmp_path 隔离）。

宿主 gitconfig 隔离：autouse fixture 把 GIT_CONFIG_* 全部指向不存在的路径，
保证 C6 身份断言只验证 checkpoint 自身的 `-c user.*` 注入，不受开发机配置影响。
"""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.core.checkpoint import GitCheckpoint
from app.tools.base import ToolExecutionError


@pytest.fixture(autouse=True)
def _isolate_host_gitconfig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "missing-global"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(tmp_path / "missing-system"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "session"
    root.mkdir()
    return root


@pytest.fixture
def ck(repo: Path) -> GitCheckpoint:
    return GitCheckpoint(repo)


async def _git(root: Path, *args: str) -> str:
    """在 root 上跑 git CLI，断言成功并返回 stdout。"""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=root,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    assert proc.returncode == 0, stderr_b.decode(errors="replace")
    return stdout_b.decode(errors="replace")


def _subjects(log: str) -> list[str]:
    return log.strip().splitlines()


# ===== C1-C10 =====


async def test_init_creates_repo_and_baseline(ck: GitCheckpoint, repo: Path) -> None:
    await ck.init(plan_version=1)
    assert (repo / ".git").is_dir()
    assert _subjects(await _git(repo, "log", "--format=%s")) == ["[prar:v1] init"]


async def test_init_idempotent_adds_second_baseline(ck: GitCheckpoint, repo: Path) -> None:
    await ck.init(plan_version=1)
    await ck.init(plan_version=2)
    assert _subjects(await _git(repo, "log", "--format=%s")) == [
        "[prar:v2] init",
        "[prar:v1] init",
    ]


async def test_commit_step_returns_hash_and_message(ck: GitCheckpoint, repo: Path) -> None:
    await ck.init(plan_version=3)
    target = repo / "steps" / "step_001" / "a.txt"
    target.parent.mkdir(parents=True)
    target.write_text("x", encoding="utf-8")
    commit = await ck.commit_step(plan_version=3, step_id="step_001", title="写文件")
    assert len(commit) == 40
    assert all(c in "0123456789abcdef" for c in commit)
    assert _subjects(await _git(repo, "log", "--format=%s")) == [
        "[prar:v3:step_001] 写文件",
        "[prar:v3] init",
    ]


async def test_commit_step_commits_files(ck: GitCheckpoint, repo: Path) -> None:
    await ck.init(plan_version=1)
    target = repo / "steps" / "step_001" / "out.txt"
    target.parent.mkdir(parents=True)
    target.write_text("hello", encoding="utf-8")
    commit = await ck.commit_step(plan_version=1, step_id="step_001", title="t")
    shown = await _git(repo, "show", f"{commit}:steps/step_001/out.txt")
    assert shown == "hello"


async def test_commit_step_empty_change_still_commits(ck: GitCheckpoint, repo: Path) -> None:
    await ck.init(plan_version=1)
    commit = await ck.commit_step(plan_version=1, step_id="step_001", title="无产出")
    assert len(commit) == 40
    assert _subjects(await _git(repo, "log", "--format=%s")) == [
        "[prar:v1:step_001] 无产出",
        "[prar:v1] init",
    ]


async def test_commit_identity_injected(ck: GitCheckpoint, repo: Path) -> None:
    await ck.init(plan_version=1)
    await ck.commit_step(plan_version=1, step_id="step_001", title="t")
    authors = _subjects(await _git(repo, "log", "--format=%an|%ae"))
    assert authors == ["prar-agent|agent@prar.local", "prar-agent|agent@prar.local"]


async def test_title_with_special_characters(ck: GitCheckpoint, repo: Path) -> None:
    await ck.init(plan_version=1)
    await ck.commit_step(plan_version=1, step_id="step_001", title='中文 "引号" 与空 格')
    subjects = _subjects(await _git(repo, "log", "--format=%s"))
    assert subjects[0] == '[prar:v1:step_001] 中文 "引号" 与空 格'


async def test_init_fails_when_root_is_file(tmp_path: Path) -> None:
    target = tmp_path / "not-a-dir"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(ToolExecutionError, match="git spawn failed"):
        await GitCheckpoint(target).init(plan_version=1)


async def test_git_missing_on_path_raises(
    ck: GitCheckpoint, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "")
    with pytest.raises(ToolExecutionError, match="git spawn failed"):
        await ck.init(plan_version=1)


async def test_timeout_raises_tool_execution_error(
    ck: GitCheckpoint, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(coro: Any, timeout: float) -> Any:
        coro.close()
        raise TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", _boom)
    with pytest.raises(ToolExecutionError, match="timed out"):
        await ck.init(plan_version=1)


async def test_grep_locates_step_commit(ck: GitCheckpoint, repo: Path) -> None:
    await ck.init(plan_version=1)
    first = await ck.commit_step(plan_version=1, step_id="step_001", title="a")
    await ck.commit_step(plan_version=1, step_id="step_002", title="b")
    hits = _subjects(await _git(repo, "log", "--format=%H", "--grep=step_001"))
    assert hits == [first]
