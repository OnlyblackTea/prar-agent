"""Task 20 GitCheckpoint 测试（真实 git CLI 集成测试，tmp_path 隔离）。

宿主 gitconfig 隔离见 tests/conftest.py 的 autouse fixture（GIT_CONFIG_* 指向
不存在的路径），保证 C6 身份断言只验证 checkpoint 自身的 `-c user.*` 注入。
"""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.core.checkpoint import GitCheckpoint
from app.tools.base import ToolExecutionError


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


# ===== C11-C14: rollback_to（Task 26 局部 rerun） =====


def _write_step_file(repo: Path, step_id: str, name: str, content: str) -> None:
    target = repo / "steps" / step_id / name
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")


async def test_rollback_to_success_step_reverts(ck: GitCheckpoint, repo: Path) -> None:
    await ck.init(plan_version=1)
    _write_step_file(repo, "step_001", "a.txt", "a")
    await ck.commit_step(plan_version=1, step_id="step_001", title="a")
    _write_step_file(repo, "step_002", "b.txt", "b")
    await ck.commit_step(plan_version=1, step_id="step_002", title="b")

    count = await ck.rollback_to(plan_version=1, step_id="step_001")

    assert count == 2  # step_002 + step_001 两条
    assert not (repo / "steps" / "step_001" / "a.txt").exists()
    assert not (repo / "steps" / "step_002" / "b.txt").exists()
    subjects = _subjects(await _git(repo, "log", "--format=%s"))
    assert subjects == [
        'Revert "[prar:v1:step_001] a"',
        'Revert "[prar:v1:step_002] b"',
        "[prar:v1:step_002] b",
        "[prar:v1:step_001] a",
        "[prar:v1] init",
    ]


async def test_rollback_to_failed_step_cleans_only(
    ck: GitCheckpoint, repo: Path,
) -> None:
    await ck.init(plan_version=1)
    _write_step_file(repo, "step_001", "a.txt", "a")
    await ck.commit_step(plan_version=1, step_id="step_001", title="a")
    # step_002 失败：只留下未提交残留，无 commit
    _write_step_file(repo, "step_002", "junk.txt", "junk")

    count = await ck.rollback_to(plan_version=1, step_id="step_002")

    assert count == 0
    assert not (repo / "steps" / "step_002" / "junk.txt").exists()  # clean 掉残留
    assert (repo / "steps" / "step_001" / "a.txt").exists()  # 成功 step 不动
    subjects = _subjects(await _git(repo, "log", "--format=%s"))
    assert subjects == ["[prar:v1:step_001] a", "[prar:v1] init"]


async def test_rollback_to_idempotent(ck: GitCheckpoint, repo: Path) -> None:
    await ck.init(plan_version=1)
    _write_step_file(repo, "step_001", "a.txt", "a")
    await ck.commit_step(plan_version=1, step_id="step_001", title="a")
    _write_step_file(repo, "step_002", "b.txt", "b")
    await ck.commit_step(plan_version=1, step_id="step_002", title="b")

    first = await ck.rollback_to(plan_version=1, step_id="step_001")
    second = await ck.rollback_to(plan_version=1, step_id="step_001")

    assert first == 2
    assert second == 0
    subjects = _subjects(await _git(repo, "log", "--format=%s"))
    # 第二次不产生任何新 commit
    assert subjects[0] == 'Revert "[prar:v1:step_001] a"'
    assert len(subjects) == 5


async def test_rollback_revert_conflict_raises(
    ck: GitCheckpoint, repo: Path,
) -> None:
    await ck.init(plan_version=1)
    _write_step_file(repo, "step_001", "a.txt", "v1")
    await ck.commit_step(plan_version=1, step_id="step_001", title="a")
    # 手动改写并提交（message 无 step 前缀 → 不被收集，但制造 revert 冲突）
    (repo / "steps" / "step_001" / "a.txt").write_text("v3", encoding="utf-8")
    await _git(
        repo,
        "-c", "user.name=user",
        "-c", "user.email=u@e",
        "commit", "-am", "user edit",
    )

    with pytest.raises(ToolExecutionError, match="revert"):
        await ck.rollback_to(plan_version=1, step_id="step_001")
