"""Task 16 本地 subprocess 沙箱（MVP 级隔离，双平台）。

实现 15 定义的 ShellRunner 协议（协议已扩展 cwd，见 15 设计变更章节）：

- Windows：Job Object（资源限制 + KILL_ON_JOB_CLOSE 树杀 + NetRateControl 硬禁网，条件生效）
- Linux：setrlimit + start_new_session/killpg 树杀 + 代理黑洞禁网（MVP 边界）

失败语义红线（沿用 base.py）：
- 业务性失败（命令 exit≠0、spawn 失败）→ ShellResult 原样透传，LLM 可重试
- 环境故障（路径逃逸、Job Object 装配失败）→ raise ToolExecutionError

平台说明：mypy 会按运行平台消除 sys.platform 字面量比较的不可达分支，
故 Windows-only（ctypes Job Object）与 POSIX-only（os.killpg）代码零跨平台误报。

Windows 硬禁网（NetRateControl）条件生效：宿主进程已在 Job Object 内时（如
CI/测试运行器），新建 Job 会嵌套进层级，而 MSDN 规定网络速率控制在一个嵌套
层级中只能设置一次 → Set 失败（ERROR_INVALID_PARAMETER）。此时降级为仅
代理黑洞禁网并记警告，通过 hard_block_applied 属性可观测（不静默）。
"""

import asyncio
import ctypes
import logging
import os
import shutil
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.tools.base import ShellResult, ToolExecutionError

# 超时统一归一（GNU timeout 惯例）；资源超限退出码平台相关，不归一化
_TIMEOUT_EXIT_CODE = 124
# 代理黑洞（discard 端口）：尊重代理变量的 HTTP 客户端立即 connection refused
_BLACKHOLE_PROXY = "http://127.0.0.1:9"
_PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
_NO_PROXY_KEYS = ("NO_PROXY", "no_proxy")

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SandboxLimits:
    """资源限制。任一维度 <=0 表示该维度不限制。"""

    max_memory_mb: int = 512
    max_cpu_seconds: float = 300
    max_processes: int = 16


# ===== Windows Job Object（ctypes） =====
# mypy 平台消除：Linux 上此分支不可达；Windows 上 else 分支不可达。

if sys.platform == "win32":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),  # 100ns 单位
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _JOBOBJECT_NET_RATE_CONTROL_INFORMATION(ctypes.Structure):
        """官方布局：MaxBandwidth(u64) + ControlFlags(u32) + DscpTag(BYTE)。"""

        _fields_ = [
            ("MaxBandwidth", ctypes.c_uint64),
            ("ControlFlags", ctypes.c_uint32),
            ("DscpTag", ctypes.c_ubyte),
        ]

    _kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    _kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    _kernel32.SetInformationJobObject.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int32,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    _kernel32.SetInformationJobObject.restype = ctypes.c_int32
    _kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    _kernel32.AssignProcessToJobObject.restype = ctypes.c_int32
    _kernel32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    _kernel32.TerminateJobObject.restype = ctypes.c_int32
    _kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    _kernel32.CloseHandle.restype = ctypes.c_int32
    _kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int32, ctypes.c_uint32]
    _kernel32.OpenProcess.restype = ctypes.c_void_p
else:
    _kernel32 = None


def _create_win32_job(limits: SandboxLimits | None, network: bool) -> tuple[int, bool]:
    """创建 Job Object：恒开 KILL_ON_JOB_CLOSE；按 limits 设资源限制；按 network 设 NetRateControl。

    返回 (handle, hard_block)：hard_block=True 表示 NetRateControl 硬禁网已生效。
    宿主进程已在 Job 内时（嵌套层级限制，MSDN 规定每层级仅可设置一次），
    Set 失败 → 记警告并返回 hard_block=False（调用方回退代理黑洞禁网）。
    """
    assert sys.platform == "win32" and _kernel32 is not None
    handle = _kernel32.CreateJobObjectW(None, None)
    if not handle:
        raise ToolExecutionError(f"CreateJobObjectW failed (err={ctypes.get_last_error()})")

    flags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    if limits is not None:
        if limits.max_memory_mb > 0:
            flags |= 0x00000100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
            info.ProcessMemoryLimit = limits.max_memory_mb * 1024 * 1024
        if limits.max_cpu_seconds > 0:
            flags |= 0x00000002  # JOB_OBJECT_LIMIT_PROCESS_TIME
            info.BasicLimitInformation.PerProcessUserTimeLimit = int(
                limits.max_cpu_seconds * 10_000_000
            )
        if limits.max_processes > 0:
            flags |= 0x00000008  # JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            info.BasicLimitInformation.ActiveProcessLimit = limits.max_processes
    info.BasicLimitInformation.LimitFlags = flags
    if not _kernel32.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
        raise ToolExecutionError(
            f"SetInformationJobObject(ExtendedLimit) failed (err={ctypes.get_last_error()})"
        )

    hard_block = False
    if not network:
        rate = _JOBOBJECT_NET_RATE_CONTROL_INFORMATION()
        # JOB_OBJECT_NET_RATE_CONTROL_ENABLE | JOB_OBJECT_NET_RATE_CONTROL_MAX_BANDWIDTH
        rate.ControlFlags = 0x00000001 | 0x00000080
        rate.MaxBandwidth = 0
        if _kernel32.SetInformationJobObject(
            handle, 32, ctypes.byref(rate), ctypes.sizeof(rate)
        ):
            hard_block = True
        else:
            # 宿主进程已在 Job 内 → 新建 Job 嵌套进层级，Windows 规定每层级
            # 仅可设置一次网络速率控制 → 降级为仅代理黑洞禁网（可观测，不静默）
            err = ctypes.get_last_error()
            _logger.warning(
                "sandbox hard network block unavailable (err=%s), "
                "falling back to proxy blackhole only",
                err,
            )
    return handle, hard_block


def _assign_pid_to_job(pid: int, job: int) -> None:
    assert sys.platform == "win32" and _kernel32 is not None
    process_handle = _kernel32.OpenProcess(0x0100 | 0x0001, False, pid)  # SET_QUOTA | TERMINATE
    if not process_handle:
        err = ctypes.get_last_error()
        # ERROR_INVALID_PARAMETER(87) 常见于子进程已退出（spawn 与 assign 竞态窗口）
        raise ToolExecutionError(f"OpenProcess failed (pid={pid}, err={err})")
    try:
        if not _kernel32.AssignProcessToJobObject(job, process_handle):
            err = ctypes.get_last_error()
            raise ToolExecutionError(f"AssignProcessToJobObject failed (pid={pid}, err={err})")
    finally:
        _kernel32.CloseHandle(process_handle)


def _terminate_job(job: int) -> None:
    assert sys.platform == "win32" and _kernel32 is not None
    _kernel32.TerminateJobObject(job, 1)


def _close_job_handle(job: int) -> None:
    assert sys.platform == "win32" and _kernel32 is not None
    _kernel32.CloseHandle(job)  # KILL_ON_JOB_CLOSE：残留进程随 handle 关闭被杀


# ===== POSIX rlimit =====
# mypy 平台消除：Windows 上此分支不可达，resource 属性（typeshed 按平台门控）零跨平台误报。

if sys.platform != "win32":

    def _apply_rlimits(limits: SandboxLimits) -> None:
        """preexec_fn 内执行：限制仅作用于子进程（fork 后 exec 前），POSIX-only。"""
        import resource

        if limits.max_memory_mb > 0:
            mem = limits.max_memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        if limits.max_cpu_seconds > 0:
            cpu = int(limits.max_cpu_seconds)
            resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        if limits.max_processes > 0:
            resource.setrlimit(
                resource.RLIMIT_NPROC, (limits.max_processes, limits.max_processes)
            )


# ===== Sandbox =====


class Sandbox:
    """本地 subprocess 沙箱：实现 ShellRunner 协议（鸭子类型，不显式继承）。

    - root：沙箱根（宿主绝对路径），由调用方决定；ensure_root/cleanup 由调用方驱动
    - limits=None 时仍保留超时/目录隔离/禁网/树杀能力
    - network=False（默认）时注入代理黑洞；Windows 另有 Job Object 硬禁网
    """

    def __init__(
        self,
        root: Path,
        *,
        limits: SandboxLimits | None = None,
        network: bool = False,
        default_timeout: float | None = 300,
    ) -> None:
        self._root = root
        self._limits = limits
        self._network = network
        self._default_timeout = default_timeout
        # Windows 硬禁网是否生效：None=未知/非 Windows，True/False=最近一次 run 的结果
        self.hard_block_applied: bool | None = None

    @property
    def root(self) -> Path:
        return self._root

    def ensure_root(self) -> None:
        """幂等创建沙箱根目录。"""
        self._root.mkdir(parents=True, exist_ok=True)

    def cleanup(self) -> None:
        """递归删除沙箱根。安全护栏：根至少 2 层深，防误删盘符根/家目录。"""
        resolved = self._root.resolve()
        if len(resolved.parts) < 3:
            raise ToolExecutionError(f"refusing to clean up shallow sandbox root: {resolved}")
        if resolved.is_dir():
            shutil.rmtree(resolved)

    async def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
        on_stdout: Callable[[str], Awaitable[None]] | None = None,
    ) -> ShellResult:
        if not argv:
            raise ToolExecutionError("empty argv")
        full_cwd = self._resolve_cwd(cwd)
        merged_env = self._merge_env(env)
        eff_timeout = self._default_timeout if timeout is None else timeout
        if sys.platform == "win32":
            job, hard_block = _create_win32_job(self._limits, self._network)
            self.hard_block_applied = hard_block
        else:
            job = None
        try:
            try:
                proc = await self._spawn(argv, full_cwd, merged_env)
                if job is not None:
                    try:
                        _assign_pid_to_job(proc.pid, job)
                    except ToolExecutionError:
                        if proc.returncode is None:
                            raise  # 进程仍存活却未入 Job → 环境故障，不静默
                        # 子进程已退出（竞态窗口）：无害，忽略
            except OSError as e:
                # spawn 失败（cwd 不存在等）→ 业务失败，LLM 可换参数重试
                return ShellResult(exit_code=127, stdout="", stderr=f"sandbox spawn failed: {e}")
            assert proc.stdout is not None and proc.stderr is not None
            read_out = asyncio.create_task(self._read_lines(proc.stdout, on_stdout))
            read_err = asyncio.create_task(proc.stderr.read())
            wait_proc = asyncio.create_task(proc.wait())
            done, _ = await asyncio.wait({wait_proc}, timeout=eff_timeout)
            if wait_proc not in done:
                await self._kill_tree(proc, job)
                await wait_proc
                stdout_b = await self._drain(read_out)
                stderr_b = await self._drain(read_err)
                return ShellResult(
                    exit_code=_TIMEOUT_EXIT_CODE,
                    stdout=stdout_b.decode("utf-8", errors="replace"),
                    stderr=stderr_b.decode("utf-8", errors="replace")
                    + f"\nsandbox timeout after {eff_timeout}s",
                )
            stdout_b = await read_out
            stderr_b = await read_err
            return ShellResult(
                exit_code=wait_proc.result(),
                stdout=stdout_b.decode("utf-8", errors="replace"),
                stderr=stderr_b.decode("utf-8", errors="replace"),
            )
        finally:
            if job is not None:
                _close_job_handle(job)

    def _resolve_cwd(self, cwd: Path | None) -> Path:
        root = self._root.resolve()
        if cwd is None:
            return root
        if cwd.is_absolute():
            raise ToolExecutionError(f"cwd must be relative to sandbox root: {cwd}")
        full = (root / cwd).resolve()
        if not full.is_relative_to(root):
            raise ToolExecutionError(f"cwd escapes sandbox root: {cwd}")
        return full

    def _merge_env(self, env: dict[str, str] | None) -> dict[str, str]:
        merged = dict(os.environ)
        if env is not None:
            merged.update(env)
        if not self._network:
            # 沙箱安全注入不可被调用方覆盖
            for key in _PROXY_KEYS:
                merged[key] = _BLACKHOLE_PROXY
            for key in _NO_PROXY_KEYS:
                merged[key] = ""
        return merged

    async def _spawn(
        self,
        argv: Sequence[str],
        full_cwd: Path,
        merged_env: dict[str, str],
    ) -> asyncio.subprocess.Process:
        kwargs: dict[str, Any] = {}
        if sys.platform != "win32":
            kwargs["start_new_session"] = True  # 子进程自立会话，killpg 杀全树
            if self._limits is not None:
                kwargs["preexec_fn"] = lambda: _apply_rlimits(self._limits)
        return await asyncio.create_subprocess_exec(
            *argv,
            cwd=full_cwd,
            env=merged_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **kwargs,
        )

    async def _kill_tree(self, proc: asyncio.subprocess.Process, job: int | None) -> None:
        if sys.platform == "win32":
            if job is not None:
                _terminate_job(job)  # Job 内全树死
            if proc.returncode is None:
                proc.kill()
        else:
            import signal

            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                if proc.returncode is None:
                    proc.kill()
        await proc.wait()

    @staticmethod
    async def _read_lines(
        stream: asyncio.StreamReader,
        on_stdout: Callable[[str], Awaitable[None]] | None,
    ) -> bytes:
        """行级读取：每行 decode 后先回调 on_stdout 再收集；返回全量字节（16/17 契约不变）。

        回调异常吞掉记 warning：流式是观察通道非控制通道（Q10）。
        超时 kill 后残余行仍在循环内继续回调。
        """
        collected: list[bytes] = []
        while True:
            line = await stream.readline()
            if not line:
                return b"".join(collected)
            if on_stdout is not None:
                try:
                    await on_stdout(line.decode("utf-8", errors="replace"))
                except Exception:
                    _logger.warning("on_stdout callback failed", exc_info=True)
            collected.append(line)

    @staticmethod
    async def _drain(task: asyncio.Task[bytes]) -> bytes:
        """超时 kill 后取回管道残余数据；进程没死透时 2s 兜底放弃。"""
        try:
            return await asyncio.wait_for(task, 2)
        except TimeoutError:
            return b""
