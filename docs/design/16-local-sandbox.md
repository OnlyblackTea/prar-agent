# 16. 本地 subprocess 沙箱（MVP 级隔离）

> 对应 ROADMAP M3 #16：`tools/sandbox.py`（rlimit + 超时 + 工作目录隔离 + 默认禁网），MVP 级隔离，不依赖 Docker。
> 生产级网络/文件系统隔离 = Docker 沙箱（M5+ #16b），本任务明确不越界。

## 目标

- 一句话目标：实现 `Sandbox` 类，落地 15 定义的 `ShellRunner` 协议，让任意 argv 在受限的本地 subprocess 中执行，**双平台（Windows + Linux）同等支持**。
- 验收标准：
  1. `Sandbox` 满足 `ShellRunner` 协议（mypy 验证 structural 兼容）；
  2. 四条隔离能力齐备：资源限制（内存/CPU/进程数）、超时、工作目录隔离、默认禁网；
  3. Windows 开发机：全套测试全绿（实际 212 passed / 4 skipped），`ruff check` / `mypy strict` 零警告零错误；
  4. Linux（VM 192.168.1.147，SSH 验证）：`test_sandbox.py` 实际 18 passed / 5 skipped，Linux 分支用例全部真实跑通（skip 均为 Windows 分支）；
  5. 零新增依赖（ctypes / resource 等标准库实现）。

## 输入 / 输出

- 上游产物：Task 15 `tools/base.py` 的 `ShellRunner` 协议与 `ShellResult`（commit 9835a96）。
- 本任务交付物：
  - `backend/src/app/tools/sandbox.py`（新增，~250 行）
  - `backend/tests/test_sandbox.py`（新增，~21 用例，含 Linux 分支）
  - `backend/src/app/tools/base.py`（修改：`ShellRunner.run` 协议扩展 `cwd`，见设计变更）
  - `backend/src/app/tools/__init__.py`（修改：导出 `Sandbox` / `SandboxLimits`）
  - `backend/tests/test_tools_base.py`（修改：`_FakeShell.run` 同步协议签名）
  - `docs/design/15-tool-abc-registry.md`（修改：追加设计变更章节）

## 接口设计

### 协议变更（15 风险表预留路径，WORKFLOW §5 流程）

15 的风险表明确预留："`ShellRunner.run` 签名与 Task 16 sandbox 设计不吻合 → 16 若需扩展签名，走 WORKFLOW §5 设计变更流程，15 协议原地更新"。

本任务触发的唯一协议变更：`ShellRunner.run` 增加 `cwd` 参数（沙箱视角相对路径），使 Task 17 的 `shell` 工具能把 `ExecContext.workdir`（沙箱视角的相对根）传递给沙箱：

```python
class ShellRunner(Protocol):
    async def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,   # 新增：沙箱视角相对路径；None = 沙箱根
    ) -> ShellResult: ...
```

- 不新增 stdin：MVP 的 shell 工具 argv 直传非交互执行，Task 18 若需要交互式 stdin 再走 §5 扩展。
- 变更内容同步追加到 `15-tool-abc-registry.md` 的 `## 设计变更 (2026-08-27)` 章节。

### `Sandbox` 类

```python
@dataclass(frozen=True, slots=True)
class SandboxLimits:
    """资源限制。None 语义由 Sandbox 构造参数承担（limits=None 即不设限）。"""

    max_memory_mb: int = 512          # 进程内存上限（RLIMIT_AS / JOB_OBJECT_LIMIT_PROCESS_MEMORY）
    max_cpu_seconds: float = 300      # 用户态+内核态 CPU 时间上限（RLIMIT_CPU / JOB_OBJECT_LIMIT_PROCESS_TIME）
    max_processes: int = 16           # 活跃进程数上限（RLIMIT_NPROC / JOB_OBJECT_LIMIT_ACTIVE_PROCESS）


class Sandbox:
    """本地 subprocess 沙箱：实现 ShellRunner 协议（鸭子类型，不显式继承）。"""

    def __init__(
        self,
        root: Path,                          # 沙箱根（宿主绝对路径），由调用方决定（dispatcher 用 sandbox/runs/<session_id>）
        *,
        limits: SandboxLimits | None = None, # None = 不设资源限制（仍保留超时/目录隔离/禁网）
        network: bool = False,               # 默认禁网
        default_timeout: float | None = 300, # run(timeout=None) 时的兜底超时；None = 无限
    ) -> None: ...

    @property
    def root(self) -> Path: ...              # 沙箱根（宿主绝对路径）

    def ensure_root(self) -> None: ...       # 幂等创建 root 目录（mkdir parents=True）
    def cleanup(self) -> None: ...           # 递归删除 root；含安全护栏（见下）

    async def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float | None = None,        # None → default_timeout
        env: dict[str, str] | None = None,   # None → 继承 os.environ；沙箱注入项不可覆盖
        cwd: Path | None = None,             # 沙箱视角相对路径；None → root；逃逸即 ToolExecutionError
    ) -> ShellResult: ...
```

### run 的执行流水线

```
run(argv, timeout, env, cwd)
  1. 解析 cwd：full = (root / cwd).resolve()；校验 full 仍在 root.resolve() 之内，
     否则 raise ToolExecutionError（环境故障，非业务失败）
  2. env 合并：base = os.environ 拷贝；network=False 时注入禁网黑洞代理（不可被调用方覆盖）；
     调用方 env 再覆盖（沙箱注入键除外）
  3. 平台装配：
     Windows：创建 Job Object（恒开 KILL_ON_JOB_CLOSE），按 limits 设
       JOB_OBJECT_EXTENDED_LIMIT_INFORMATION，按 network 设 NetRateControl（带宽=0）
     POSIX：start_new_session=True + preexec_fn 内 setrlimit
  4. asyncio.create_subprocess_exec(*argv, cwd=full, env=merged,
       stdout=PIPE, stderr=PIPE, **平台钩子)
  5. asyncio.wait_for(proc.communicate(), timeout)
     - 正常 → ShellResult(exit_code, stdout, stderr)
     - TimeoutError → 杀进程树（Windows: TerminateJobObject；POSIX: killpg SIGKILL）
       → ShellResult(exit_code=124, 部分 stdout, stderr + "sandbox timeout after Xs")
```

### 平台策略

| 能力 | Windows（开发机 25H2，本机测试） | Linux（VM 192.168.1.147，SSH 跑测试） |
| --- | --- | --- |
| 内存限制 | Job Object `JOB_OBJECT_LIMIT_PROCESS_MEMORY`（超限=子进程分配失败退出） | `resource.setrlimit(RLIMIT_AS)` |
| CPU 限制 | `JOB_OBJECT_LIMIT_PROCESS_TIME`（超限=系统杀进程） | `RLIMIT_CPU`（SIGXCPU 杀） |
| 进程数限制 | `JOB_OBJECT_LIMIT_ACTIVE_PROCESS`（超限=CreateProcess 失败） | `RLIMIT_NPROC`（fork 失败；root 不受此限） |
| 树杀 | `TerminateJobObject`（Job 内全树死）+ `KILL_ON_JOB_CLOSE` 兜底 | `os.killpg(SIGKILL)`（start_new_session） |
| 禁网 | Job Object `NetRateControl`（出站带宽=0，Win8+；宿主在 Job 内时条件降级，见实施记录）+ 代理黑洞注入 | 仅代理黑洞注入（MVP 边界；候选 `unshare -rn` 走 §5 扩展） |

- **Linux 为主要支持平台**（与 Windows 同等验收标准）：Linux 分支用例在 VM 上真实跑通，不 skip；Windows 开发机跑通全套。
- 任一限制项在当前平台不可用 → **raise `ToolExecutionError`，不静默降级**（禁网是安全承诺）。
- 资源超限的退出码是平台相关的（Windows 为系统特定码），**不归一化**，原样透传；超时统一归一为 `124`（GNU timeout 惯例）+ stderr 注明。

### 禁网实现（默认 `network=False`）

1. **Windows 硬禁（条件生效，见实施记录）**：`SetInformationJobObject(hJob, JobObjectNetRateControlInformation, ...)`，
   `ControlFlags = JOB_OBJECT_NET_RATE_CONTROL_ENABLE | JOB_OBJECT_NET_RATE_CONTROL_MAX_BANDWIDTH`，
   `MaxBandwidth = 0`（所有出站 TCP 流量阻断）。Win8+/Server 2012+ 支持。
   宿主进程已在 Job Object 内时（嵌套层级限制：MSDN 规定每层级网络速率控制仅可设置一次），
   Set 失败（ERROR_INVALID_PARAMETER）→ 记警告 + 回退仅代理黑洞，`hard_block_applied` 属性可观测（不静默降级）。
2. **全平台快速失败**：注入 `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY（及小写）→ http://127.0.0.1:9`（discard 端口）、
   `NO_PROXY/no_proxy → ""`。作用：尊重代理变量的 HTTP 客户端立即 connection refused，
   不硬等的快速失败；硬禁由第 1 层承担。
3. **Linux MVP 边界**：无硬禁网层，仅代理黑洞（Linux 真网络隔离留 M5+ Docker）；
   若后续需要，`unshare -rn`（user namespace 网络隔离）作为候选方案走 §5 扩展。
4. `network=True` 时全部跳过。

### 数据流

```
Tool.execute (Task 17 shell)
  └─ ctx.run_shell.run(argv, timeout=..., cwd=ctx.workdir)   # ShellRunner 协议
       └─ Sandbox.run: 路径校验 → env 合并 → Job Object/rlimit → subprocess → wait_for
            └─ ShellResult(exit_code / stdout / stderr) → ToolResult(output=...)
```

## 文件清单

| 路径 | 类型 | 说明 |
| --- | --- | --- |
| `backend/src/app/tools/sandbox.py` | 新增 | `SandboxLimits` + `Sandbox` + 平台私有 helper（~250 行） |
| `backend/src/app/tools/base.py` | 修改 | `ShellRunner.run` 协议加 `cwd` 参数 |
| `backend/src/app/tools/__init__.py` | 修改 | 导出 `Sandbox` / `SandboxLimits` |
| `backend/tests/test_sandbox.py` | 新增 | ~21 用例（见测试清单，Windows + Linux 分支） |
| `backend/tests/test_tools_base.py` | 修改 | `_FakeShell.run` 同步协议签名（mypy structural 兼容需要） |
| `docs/design/15-tool-abc-registry.md` | 修改 | 追加 `## 设计变更 (2026-08-27)` 记录协议扩展 |

## 实施步骤

1. 写 `tests/test_sandbox.py`（TDD 红：Sandbox 不存在 → `ModuleNotFoundError`）
2. 更新 `base.py` 协议 + `test_tools_base.py` 的 `_FakeShell`（红：测试签名不匹配）
3. 实现 `sandbox.py`：
   a. `SandboxLimits` + `Sandbox.__init__` + `ensure_root`/`cleanup` + `root` property
   b. `run` 主流程（路径校验、env 合并、spawn、wait_for）
   c. Windows Job Object helper（ctypes：CreateJobObjectW / SetInformationJobObject /
      AssignProcessToJobObject / TerminateJobObject / OpenProcess）
   d. POSIX rlimit helper（preexec_fn + setrlimit，模块内条件 import）
   e. `_kill_tree`（Windows: TerminateJobObject + taskkill /T /F 兜底；POSIX: killpg）
4. 更新 `__init__.py` 导出
5. `cd backend && make test` 全绿（193 + ~21，Windows 分支 + Linux 用例在 win32 上 skip）
6. `cd backend && make lint && make typecheck` 零警告/零错误
7. **Linux VM 验证**：SSH 到 192.168.1.147（凭证实施时向主人确认），同步 backend 代码 → 建 Python 3.12 环境（缺失则按纪律提醒主人，不降级）→ 跑 `test_sandbox.py` 全绿（Linux 分支真实执行）
8. 追加 15 文档设计变更章节
9. commit：设计文档 + 代码 + 测试同 commit，message `feat(backend): 本地 subprocess 沙箱 (M3-16)`，`Refs: docs/design/16-local-sandbox.md`

## 测试清单

### `test_sandbox.py`（真实 subprocess 集成测试；命令统一用 `sys.executable -c`，平台无关）

| # | 测试 | 断言 |
| --- | --- | --- |
| S1 | 基本执行 | `python -c "print('hi')"` → exit_code=0、stdout="hi\n"、stderr="" |
| S2 | 非零退出传递 | `python -c "import sys; sys.exit(3)"` → exit_code=3 |
| S3 | stderr 捕获 | 子进程向 stderr 写 → stderr 含内容 |
| S4 | 不经过 shell | argv 含空格/特殊字符（`print('a b & c')`）→ 原样输出（无 shell 注入） |
| S5 | env 注入 | `env={"FOO": "bar"}` → 子进程读到 FOO=bar |
| S6 | env 继承 | `env=None` → 子进程读到继承环境变量 |
| S7 | cwd 生效 | `cwd=Path("sub")` → 子进程 `os.getcwd()` 打印 root/sub 真实路径 |
| S8 | cwd 逃逸拒绝 | `cwd=Path("../outside")` → `ToolExecutionError` |
| S9 | 超时归约 | `time.sleep(10)` + timeout=0.5 → exit_code=124、stderr 含 "timeout" |
| S10 | default_timeout 兜底 | 构造时 default_timeout=0.5，run 不传 timeout → 124 |
| S11 | 禁网 env 注入 | network=False → 子进程读到 HTTP_PROXY 指向 127.0.0.1:9、NO_PROXY="" |
| S12 | 放开网络 | network=True → 无注入（继承原值） |
| S13 | 注入不可覆盖 | network=False 且 env 显式给 HTTP_PROXY → 子进程读到的仍是黑洞值 |
| S14 | ensure_root / cleanup | 创建目录；cleanup 后 root 不存在 |
| S15 | 内存限制（win32） | limits(max_memory_mb=128)，子进程分配 300MB → 非零退出；skipif 非 win32 |
| S16 | CPU 限制（win32） | limits(max_cpu_seconds=1)，子进程死循环 → 非零退出；skipif 非 win32 |
| S17 | 进程数限制（win32） | limits(max_processes=1)，子进程再 spawn 1 个 → 非零退出；skipif 非 win32 |
| S18 | 内存限制（linux） | limits(max_memory_mb=128)，子进程分配 300MB → 非零退出；skipif 非 linux（VM 上真实跑） |
| S19 | CPU 限制（linux） | limits(max_cpu_seconds=1)，死循环 → 非零退出；skipif 非 linux |
| S20 | 进程数限制（linux） | limits(max_processes=2)，spawn 超过上限 → 非零退出；skipif 非 linux 或 root（root 不受 RLIMIT_NPROC 约束） |
| S21 | 沙箱根未创建时 run | 先 run 后 ensure_root 不自动建根 → 命令失败以业务失败返回（非异常） |
| S22 | 硬禁网与宿主 Job 上下文一致（win32） | `hard_block_applied is (宿主不在 Job 内)`（两种上下文均确定性成立）；skipif 非 win32 |
| S23 | 硬禁网阻断裸 socket（win32） | 宿主无 Job 时裸 socket 连接必须失败且 `hard_block_applied is True`；skipif 非 win32 或宿主在 Job 内 |

### 边缘情况

- `argv` 空序列 → `ToolExecutionError`（配置错误，fail fast）。
- `cwd` 为绝对路径 → 按沙箱视角拒绝（`ToolExecutionError`；协议约定相对路径）。
- Job Object `AssignProcessToJobObject` 竞态：asyncio 不支持 CREATE_SUSPENDED，assign 与子进程启动之间存在微窗口——MVP 接受，文档记录（M5+ Docker 消除）。
- stdout/stderr 无大小上限（`communicate()` 全量缓冲）——MVP 接受，风险表记录。
- `cleanup()` 安全护栏：`root.resolve()` 必须至少 2 层深（防 `/` 或盘符根误删），否则 `ToolExecutionError`。
- Linux 进程数限制对 root 无效（root 不受 `RLIMIT_NPROC` 约束）→ S20 在 root 下 skip（测试内处理，非实现缺陷）。

### 集成测试入口

```bash
# Windows 开发机（全套 + 质量门）
cd backend && make test          # 193 + ~21 全绿（Linux 用例在 win32 上 skip）
cd backend && make lint          # ruff 零警告
cd backend && make typecheck     # mypy strict 零错误（含 sandbox.py 与新增测试）

# Linux VM（192.168.1.147，SSH；Linux 分支真实执行）
ssh <user>@192.168.1.147 "cd <sync_path>/backend && <venv>/bin/python -m pytest tests/test_sandbox.py -v"
```

## 风险与未决

### 已识别风险

| 风险 | 缓解 |
| --- | --- |
| `AssignProcessToJobObject` 竞态窗口（子进程可能在 assign 前 spawn 逃逸） | MVP 接受并文档记录；M5+ Docker 沙箱消除 |
| stdout 无限输出撑爆内存 | MVP 接受；Task 19 流式管道落地后改为边读边转发 |
| ctypes 代码在 mypy strict 下 Any 泛滥 | 结构体显式 `_fields_`、函数显式 `argtypes/restype`；个别不可避免处窄化 `# type: ignore[attr-defined]`，不静默吞错 |
| Windows 禁网 API 调用失败 | 条件生效：宿主在 Job 内时降级为代理黑洞 + 警告，`hard_block_applied` 可观测；`ExtendedLimit` 装配失败仍 raise `ToolExecutionError`（见实施记录） |
| preexec_fn 多线程不安全（Python 3.12 POSIX） | 沙箱 spawn 低频操作，MVP 接受；文档记录 |
| VM 上缺 Python 3.12/uv 环境 | 按纪律直接提醒主人补齐，不降级为只跑 Windows |
| VM 的 user namespace 内核配置未知 | 本任务 Linux 无硬禁网（仅代理黑洞），不受影响；`unshare -rn` 候选方案留 §5 |

### 已决策（默认值，主人不反对就这么走）

| # | 项目 | 决策 | 反对就告诉我 |
| --- | --- | --- | --- |
| Q1 | 协议变更 | **`ShellRunner.run` 加 `cwd: Path \| None = None`**（15 风险表预留路径，15 文档追加设计变更章节）；不加 stdin | "协议不动，Sandbox 实例绑定 cwd" |
| Q2 | 超时/资源退出码 | **超时统一 124** + stderr 注明；资源超限退出码平台相关不归一化 | "资源超限也归一并注明" |
| Q3 | 禁网实现 | **Windows Job Object NetRateControl（条件生效）+ 全平台代理黑洞**（MVP 级；真隔离 M5+ Docker） | "仅代理黑洞" |
| Q4 | 平台策略 | **Windows Job Object + Linux rlimit 双平台同等支持（Linux 为主要平台）**；Linux 分支在 VM 192.168.1.147 上真实验证；不可用即 ToolExecutionError | "只做 Windows" |
| Q5 | 默认超时 | **default_timeout=300s**，run(timeout=None) 用之；构造传 None 表示无限 | "默认无限" |
| Q6 | 生命周期 | **ensure_root/cleanup 由调用方（dispatcher）驱动**；16 不锁定生产根路径（约定 `sandbox/runs/<session_id>` 由 18 落实，测试用 tmp_path） | "Sandbox 自动建根/自动清理" |
| Q7 | 超限退出码 | 不归一 | 见 Q2 |

如以上 7 项主人无异议，请直接回复 `APPROVED`，我立即按本设计落代码并 commit。

## 实施记录 (2026-08-27)

### 平台约束发现：NetRateControl 嵌套 Job 层级限制（设计变更）

实施中经本机探测 + MSDN 官方文档确认：

1. `JOBOBJECT_NET_RATE_CONTROL_INFORMATION` 官方布局为
   `MaxBandwidth(u64) + ControlFlags(u32) + DscpTag(BYTE)`（16 字节），
   Info class 编号 `JobObjectNetRateControlInformation = 32`。
2. **MSDN 规定网络速率控制在嵌套 Job 层级中只能设置一次**。宿主进程已在
   Job Object 内时（本机 Qoder 终端即如此，`IsProcessInJob=1`），新建 Job
   会嵌套进层级 → `SetInformationJobObject(NetRateControl)` 必然失败
   （ERROR_INVALID_PARAMETER=87），与结构体布局/参数无关。WMI、schtasks、
   explorer 转发 spawn 的进程均在 Job 内；`CREATE_BREAKAWAY_FROM_JOB` 的
   BREAKAWAY_OK 语义下子进程被放进独立嵌套 Job，仍受限。
3. 用户自己的终端（job-free）→ 硬禁网可用；CI/测试运行器（宿主在 Job 内）
   → 不可用。

→ 设计变更：硬禁网**条件生效**（推翻“调用失败 → ToolExecutionError”）。
Set 成功则内核强制；失败 → 记 warning + 回退代理黑洞层，
`Sandbox.hard_block_applied: bool | None` 可观测（None=未知/非 Windows，
True/False=最近一次 run）。`ExtendedLimit` 装配失败仍 raise
`ToolExecutionError`（资源限制装配是硬承诺，不降级）。

测试策略同步调整：一致性用例断言
`hard_block_applied is (宿主不在 Job 内)`（两种上下文均确定性成立）；
裸 socket 真实生效用例仅在宿主无 Job 时实跑（否则 skipif）。

### mypy strict 平台消除模式

- `resource` 模块属性在 typeshed 中按平台门控：`_apply_rlimits` 整体包进
  模块级 `if sys.platform != "win32":` 条件定义（与 `_kernel32` 块同模式），
  双平台 mypy strict 零 ignore；`os.killpg` 未门控无需 ignore。
- ctypes 结构体类名镜像 Windows API 大写命名 → ruff
  `per-file-ignores`（`src/app/tools/sandbox.py` 豁免 N801）。
- 测试侧 `os.geteuid` 平台门控 → `getattr(os, "geteuid", lambda: -1)() == 0`
  （注意非零 uid 布尔值为 True，必须与 0 显式比较）。

### 实际验收结果

- Windows 开发机：`pytest` 212 passed / 4 skipped；`ruff check` 全过；
  `mypy strict` 64 文件零问题。
- Linux VM 192.168.1.147（uv 0.12.6，Python 3.12.3）：
  `test_sandbox.py` 18 passed / 5 skipped——3 个 Linux rlimit 分支
  （S18-S20）真实跑通，skip 均为 Windows 分支（S15-S17 + S22/S23）。
- 测试用例总数 23（S1-S23），设计时预估 21。
