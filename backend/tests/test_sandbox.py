"""Task 16 本地 subprocess 沙箱的集成测试（真实 subprocess，双平台分支）。

- S1-S14、S21：平台无关通用用例
- S15-S17：Windows Job Object 资源限制分支（开发机跑）
- S18-S20：Linux rlimit 资源限制分支（VM 192.168.1.147 跑）
"""

import os
import sys
from pathlib import Path

import pytest

from app.tools.base import ToolExecutionError
from app.tools.sandbox import Sandbox, SandboxLimits

IS_WIN = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")


def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def _host_in_job() -> bool:
    """宿主进程是否已在 Job Object 内（决定 Windows 硬禁网是否可用）。"""
    if not IS_WIN:
        return False
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.IsProcessInJob.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int32),
    ]
    kernel32.IsProcessInJob.restype = ctypes.c_int32
    injob = ctypes.c_int32()
    ok = kernel32.IsProcessInJob(kernel32.GetCurrentProcess(), None, ctypes.byref(injob))
    return bool(ok and injob.value)


def _is_root() -> bool:
    """root 不受 RLIMIT_NPROC 约束（geteuid 仅 POSIX；Windows 上恒为 False）。"""
    return getattr(os, "geteuid", lambda: -1)() == 0


@pytest.fixture
def sb(tmp_path: Path) -> Sandbox:
    sandbox = Sandbox(tmp_path / "sb")
    sandbox.ensure_root()
    return sandbox


# ===== S1-S4: 基本执行语义 =====


async def test_basic_execution(sb: Sandbox) -> None:
    r = await sb.run(_py("print('hi')"))
    assert r.exit_code == 0
    assert r.stdout.strip() == "hi"
    assert r.stderr == ""


async def test_nonzero_exit_propagated(sb: Sandbox) -> None:
    r = await sb.run(_py("import sys; sys.exit(3)"))
    assert r.exit_code == 3


async def test_stderr_captured(sb: Sandbox) -> None:
    r = await sb.run(_py("import sys; print('boom', file=sys.stderr)"))
    assert r.exit_code == 0
    assert "boom" in r.stderr


async def test_no_shell_interpretation(sb: Sandbox) -> None:
    # argv 直传不经过 shell：特殊字符必须原样到达子进程
    r = await sb.run(_py("print('a b & c')"))
    assert "a b & c" in r.stdout


# ===== S5-S6: env 语义 =====


async def test_env_injection(sb: Sandbox) -> None:
    r = await sb.run(_py("import os; print(os.environ['FOO'])"), env={"FOO": "bar"})
    assert r.stdout.strip() == "bar"


async def test_env_inheritance(sb: Sandbox) -> None:
    r = await sb.run(_py("import os; print(len(os.environ) > 0)"))
    assert r.stdout.strip() == "True"


# ===== S7-S8: cwd 隔离 =====


async def test_cwd_resolves_within_root(sb: Sandbox) -> None:
    (sb.root / "sub").mkdir()
    r = await sb.run(_py("import os; print(os.getcwd())"), cwd=Path("sub"))
    assert Path(r.stdout.strip()) == (sb.root / "sub").resolve()


async def test_cwd_escape_rejected(sb: Sandbox) -> None:
    with pytest.raises(ToolExecutionError):
        await sb.run(_py("pass"), cwd=Path("../outside"))


# ===== S9-S10: 超时 =====


async def test_timeout_returns_124(sb: Sandbox) -> None:
    r = await sb.run(_py("import time; time.sleep(10)"), timeout=0.5)
    assert r.exit_code == 124
    assert "timeout" in r.stderr


async def test_default_timeout_applied(tmp_path: Path) -> None:
    sb2 = Sandbox(tmp_path / "sb2", default_timeout=0.5)
    sb2.ensure_root()
    r = await sb2.run(_py("import time; time.sleep(10)"))
    assert r.exit_code == 124


# ===== S11-S13: 禁网（默认 network=False） =====


async def test_network_block_injects_blackhole_proxy(sb: Sandbox) -> None:
    r = await sb.run(
        _py(
            "import os; print(repr(os.environ['HTTP_PROXY'])); "
            "print(repr(os.environ.get('NO_PROXY')))"
        )
    )
    lines = r.stdout.strip().splitlines()
    assert lines[0] == "'http://127.0.0.1:9'"
    assert lines[1] == "''"


async def test_network_allowed_respects_caller_env(tmp_path: Path) -> None:
    sb_net = Sandbox(tmp_path / "sb", network=True)
    sb_net.ensure_root()
    r = await sb_net.run(
        _py("import os; print(os.environ.get('HTTP_PROXY', '<unset>'))"),
        env={"HTTP_PROXY": "http://proxy.example:8080"},
    )
    assert r.stdout.strip() == "http://proxy.example:8080"


async def test_network_block_override_impossible(sb: Sandbox) -> None:
    r = await sb.run(
        _py("import os; print(os.environ['HTTP_PROXY'])"),
        env={"HTTP_PROXY": "http://evil-proxy:8080"},
    )
    assert r.stdout.strip() == "http://127.0.0.1:9"


@pytest.mark.skipif(not IS_WIN, reason="Windows Job Object 分支")
async def test_hard_block_matches_host_job_context(tmp_path: Path) -> None:
    """硬禁网可用性 = 宿主进程不在 Job 内（Windows 嵌套层级限制，两种上下文都确定性成立）。"""
    sb = Sandbox(tmp_path / "sb")
    sb.ensure_root()
    r = await sb.run(_py("print('hi')"))
    assert r.exit_code == 0
    assert sb.hard_block_applied is (not _host_in_job())


@pytest.mark.skipif(
    not IS_WIN or _host_in_job(),
    reason="NetRateControl 硬禁网仅在宿主无 Job 时可用（如用户自己的终端）",
)
async def test_hard_block_blocks_raw_socket(tmp_path: Path) -> None:
    """宿主无 Job 时：NetRateControl 硬禁网必须阻断裸 socket 出站连接（绕过代理 env）。"""
    import socket

    try:
        with socket.create_connection(("192.168.1.147", 5432), timeout=3):
            pass
    except OSError as e:
        pytest.skip(f"VM 192.168.1.147:5432 不可达，无法验证禁网生效: {e}")

    sb = Sandbox(tmp_path / "sb")
    sb.ensure_root()
    code = (
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('192.168.1.147', 5432), timeout=5)\n"
        "    print('CONNECTED')\n"
        "except Exception as e:\n"
        "    print('FAIL', type(e).__name__)\n"
    )
    r = await sb.run(_py(code), timeout=30)
    assert sb.hard_block_applied is True
    assert "CONNECTED" not in r.stdout
    assert "FAIL" in r.stdout


# ===== S14: 生命周期 =====


def test_ensure_root_and_cleanup(tmp_path: Path) -> None:
    sb = Sandbox(tmp_path / "nested" / "sb")
    sb.ensure_root()
    assert sb.root.is_dir()
    sb.cleanup()
    assert not sb.root.exists()


# ===== S15-S17: Windows Job Object 资源限制（开发机） =====


@pytest.mark.skipif(not IS_WIN, reason="Windows Job Object 分支")
async def test_memory_limit_win32(tmp_path: Path) -> None:
    sb = Sandbox(tmp_path / "sb", limits=SandboxLimits(max_memory_mb=128))
    sb.ensure_root()
    r = await sb.run(_py("x = bytearray(300 * 1024 * 1024)"), timeout=30)
    assert r.exit_code != 0


@pytest.mark.skipif(not IS_WIN, reason="Windows Job Object 分支")
async def test_cpu_limit_win32(tmp_path: Path) -> None:
    sb = Sandbox(tmp_path / "sb", limits=SandboxLimits(max_cpu_seconds=1))
    sb.ensure_root()
    r = await sb.run(_py("while True: pass"), timeout=15)
    assert r.exit_code != 0


@pytest.mark.skipif(not IS_WIN, reason="Windows Job Object 分支")
async def test_process_limit_win32(tmp_path: Path) -> None:
    sb = Sandbox(tmp_path / "sb", limits=SandboxLimits(max_processes=1))
    sb.ensure_root()
    r = await sb.run(
        _py("import subprocess, sys; subprocess.run([sys.executable, '-c', 'pass'], check=True)"),
        timeout=30,
    )
    assert r.exit_code != 0


# ===== S18-S20: Linux rlimit 资源限制（VM 192.168.1.147） =====


@pytest.mark.skipif(not IS_LINUX, reason="Linux rlimit 分支")
async def test_memory_limit_linux(tmp_path: Path) -> None:
    sb = Sandbox(tmp_path / "sb", limits=SandboxLimits(max_memory_mb=128))
    sb.ensure_root()
    r = await sb.run(_py("x = bytearray(300 * 1024 * 1024)"), timeout=30)
    assert r.exit_code != 0


@pytest.mark.skipif(not IS_LINUX, reason="Linux rlimit 分支")
async def test_cpu_limit_linux(tmp_path: Path) -> None:
    sb = Sandbox(tmp_path / "sb", limits=SandboxLimits(max_cpu_seconds=1))
    sb.ensure_root()
    r = await sb.run(_py("while True: pass"), timeout=15)
    assert r.exit_code != 0


@pytest.mark.skipif(
    not IS_LINUX or _is_root(),
    reason="Linux rlimit；root 不受 RLIMIT_NPROC 约束",
)
async def test_process_limit_linux(tmp_path: Path) -> None:
    sb = Sandbox(tmp_path / "sb", limits=SandboxLimits(max_processes=2))
    sb.ensure_root()
    r = await sb.run(
        _py("import subprocess, sys; subprocess.run([sys.executable, '-c', 'pass'], check=True)"),
        timeout=30,
    )
    assert r.exit_code != 0


# ===== S21: 沙箱根未创建 =====


async def test_run_before_ensure_root(tmp_path: Path) -> None:
    sb = Sandbox(tmp_path / "not_created")
    r = await sb.run(_py("print('hi')"))
    assert r.exit_code != 0
