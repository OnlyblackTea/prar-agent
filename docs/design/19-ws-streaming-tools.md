# 19. 工具输出流式管道（tool.stdout 真流式 + /act WS 端点）

## 目标

- **一句话**：打通工具输出的**真流式**管道——Sandbox stdout 行级回调 → ShellTool 流式装配 → dispatcher 事件 sink → WS 事件帧，新建 `/api/ws/sessions/{session_id}/act` 端点，前端（Task 21）实时看到 stdout 流。

- **验收标准**（缺一不可）：

  1. `cd backend && uv run pytest -q` 全绿（现有 251 + 本任务新增 ~20 用例）
  2. `uv run ruff check src tests` / `uv run mypy src tests` 零警告零错误（mypy strict）
  3. Linux VM（192.168.1.147）上流式相关用例真实跑通（真实 Sandbox 行级回调）
  4. 单测 100% mock LLM / mock DB（端点测试 patch dispatcher 工厂，零真调 API）

- **ROADMAP 偏差说明**：ROADMAP 19 原文写「复用 M1 的 SSE 管道」，但 08 已决策单一 WebSocket 管道且已实施；本任务沿用 WS（与 08 的 plan 流式同构），不引入 SSE。

## 现状

| # | 现状 | 问题 |
| --- | --- | --- |
| P1 | `Sandbox.run` 全量读 stdout（`read()`）后返回 | 长命令（npm install 等）前端全程黑屏，无实时反馈 |
| P2 | `ShellTool` 不流式（17 明示"流式管道是 Task 19 的交付"） | `ExecContext.emit_stdout` 已预留但无人装配 |
| P3 | 无执行入口 | ACTING 的执行没有 WS 端点，21 前端无从连接 |
| P4 | `ws_streamer` 只有 plan 事件 | 无 step / tool 事件 |

## 输入 / 输出

- **输入**：WS 客户端连 `/act` 发 `{"type": "execute"}`（session 已 `advance-to-acting`，plan 在 DB，adapter 取 session）
- **输出**：事件序列 `step.start → (tool.stdout × N → tool.exit) × step → step.done × step → plan.done`；失败推 `error` 并关闭

## 接口设计

### 事件集（与 08 的 `plan.*` 命名空间不冲突）

| 事件 | 载荷 | 来源 |
| --- | --- | --- |
| `step.start` | index / step_id / title / tool / tool_args | dispatcher sink |
| `tool.stdout` | step_id / chunk | Sandbox on_stdout → ShellTool → sink |
| `tool.exit` | step_id / exit_code / ok | ShellTool 结束时 emit_event → sink |
| `step.done` | StepExecution 全字段（step_id / ok / attempts / output / artifacts / thoughts / failure_reason） | dispatcher sink |
| `plan.done` | total_steps / all_ok | ws_act 端点 |
| `error` | code / message（沿用 08 错误码风格） | ws_act 端点 |

### 1. Sandbox：on_stdout 行级回调（真流式）

```python
async def run(
    self, argv, *,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    on_stdout: Callable[[str], Awaitable[None]] | None = None,
) -> ShellResult: ...
```

- stdout 读取从 `read()` 全量改为 `readline()` 循环：每行 decode（`errors="replace"`）后**先回调 on_stdout，再追加内部收集**；ShellResult.stdout 仍返回全量文本（16/17 契约不变，向后兼容）
- 超时路径：kill 树后 drain 残余行，**残余行同样回调**（drain 期间回调仍在循环内）
- **回调异常不中断执行**：流式是观察通道非控制通道，回调抛异常 → 记 warning 吞掉
- 向后兼容：新关键字参数默认 None，现有 16/17 测试零改动

### 2. base.py：协议扩展（15 设计变更流程，原地更新）

```python
class ShellRunner(Protocol):
    async def run(self, argv, *, timeout=None, env=None, cwd=None,
                  on_stdout: Callable[[str], Awaitable[None]] | None = None) -> ShellResult: ...


class EventEmitter(Protocol):
    """工具结束事件（如 shell exit）。Task 19 由 dispatcher 装配。"""

    async def emit(self, event: dict[str, Any]) -> None: ...


@dataclass(frozen=True, slots=True)
class ExecContext:
    ...
    emit_stdout: StdoutEmitter | None = None
    emit_event: EventEmitter | None = None   # 新增
```

### 3. ShellTool：流式装配

```python
r = await ctx.run_shell.run(argv, timeout=args.timeout, cwd=ctx.workdir,
                            on_stdout=self._stdout_cb(ctx))
# _stdout_cb：ctx.emit_stdout 为 None → None；否则 lambda chunk: self._emit(ctx, chunk)
# 结束事件（emit_event 未装配时安全跳过）：
if ctx.emit_event is not None:
    await ctx.emit_event.emit({"type": "tool.exit", "exit_code": r.exit_code,
                               "ok": r.exit_code == 0})
```

### 4. dispatcher：ActEventSink + step 适配器

```python
class ActEventSink(Protocol):  # 定义在 action_dispatcher.py
    async def step_start(self, *, index: int, step: StepNode) -> None: ...
    async def tool_stdout(self, *, step_id: str, chunk: str) -> None: ...
    async def tool_exit(self, *, step_id: str, exit_code: int, ok: bool) -> None: ...
    async def step_done(self, *, record: StepExecution) -> None: ...


async def execute_plan(self, plan, *, session_id, plan_version,
                       sink: ActEventSink | None = None) -> list[StepExecution]:
```

- 每个 step：`sink.step_start(index=i, step=step)` → 构造 ctx（`emit_stdout=_StdoutAdapter(sink, step_id)`、`emit_event=_EventAdapter(sink, step_id)`）→ 执行 → `sink.step_done(record=record)`
- 适配器是 dispatcher 内部闭包类：把工具侧单参 `emit(chunk/event)` 绑定 step_id 后转发 sink（工具侧协议零改动）
- sink=None → 全部跳过（向后兼容，18 的 17 用例零改动）

### 5. api/ws_act.py：WSActSink + /act 端点（新建）

```python
class WSActSink:
    """ActEventSink 的 WS 实现：send_json 事件帧。放 api 层保持 core 零 fastapi 依赖。"""

    def __init__(self, websocket: WebSocket) -> None: ...
    async def step_start(...): await self._ws.send_json({"type": "step.start", ...})
    async def tool_stdout(...): await self._ws.send_json({"type": "tool.stdout", ...})
    async def tool_exit(...): await self._ws.send_json({"type": "tool.exit", ...})
    async def step_done(...): await self._ws.send_json({"type": "step.done", **record.model_dump(mode="json")})


class ExecuteMessage(BaseModel):
    type: str = Field(pattern="^execute$")


@router.websocket("/sessions/{session_id}/act")
async def act_websocket(websocket: WebSocket, session_id: uuid.UUID) -> None:
```

- 流程：accept → request_id → 收 execute 消息 → `SessionService.get`（404 → error）→ **phase 必须是 acting**（非法 → error `illegal_phase`；前置 `advance-to-acting` HTTP 端点已建，18 的 API 层接入在本任务补上执行后转移）→ `get_current_plan` → adapter resolve（session.adapter_id）→ `create_default_dispatcher(get_router(), adapter)` → `execute_plan(..., sink=WSActSink(websocket))` → `transition(ACTING, ACTION_REVIEW)` + commit → `plan.done` 事件
- 错误处理沿用 ws_plan 模式：error 事件 + `_close_quietly`

### 6. 依赖方向（无环）

```
api/ws_act.py ──→ core/action_dispatcher.py（ActEventSink + StepExecution）
                          └── tools/（base / sandbox / builtin，不变向 core）
core 层零 fastapi 依赖；ws_streamer.py 本任务零改动（plan 流式不受影响）
```

## 文件清单

| 文件 | 操作 | 说明 |
| --- | --- | --- |
| `backend/src/app/tools/sandbox.py` | 改 | run 加 on_stdout 行级回调 |
| `backend/src/app/tools/base.py` | 改 | ShellRunner 协议加 on_stdout；ExecContext 加 emit_event；新增 EventEmitter |
| `backend/src/app/tools/builtin/shell.py` | 改 | stdout 流式装配 + tool.exit 事件 |
| `backend/src/app/core/action_dispatcher.py` | 改 | ActEventSink 协议 + sink 参数 + 适配器 |
| `backend/src/app/api/ws_act.py` | 新 | WSActSink + /act 端点 |
| `backend/src/app/api/main.py`（或 main.py） | 改 | include ws_act router |
| `backend/tests/test_sandbox.py` | 增 | on_stdout 用例 ~4 |
| `backend/tests/test_builtin_tools.py` | 增 | shell 流式装配用例 ~2 |
| `backend/tests/test_action_dispatcher.py` | 增 | FakeSink 事件序列用例 ~4 |
| `backend/tests/test_ws_act.py` | 新 | 端点集成测试（patch 工厂，~6） |

## 测试清单

| ID | 用例 | 断言要点 |
| --- | --- | --- |
| S1 | sandbox on_stdout 行级回调 | 多行 echo 命令 → 回调序列 == 输出行；ShellResult.stdout 仍全量 |
| S2 | sandbox 无回调向后兼容 | on_stdout=None 行为与 16 契约一致 |
| S3 | sandbox 超时路径残余回调 | sleep 超时 kill 后，已产出行仍被回调 |
| S4 | sandbox 回调异常不中断 | on_stdout 抛异常 → run 正常返回，warning 记录 |
| B1 | shell 流式装配 | FakeRunner 记录 on_stdout 转发 chunk == stdout 行 |
| B2 | shell tool.exit 事件 | FakeRunner + FakeEventEmitter → 收到 exit_code/ok；emit_event None 时跳过 |
| D1 | dispatcher sink 事件序列 | FakeSink 记录 step.start → tool.stdout → tool.exit → step.done 顺序 |
| D2 | dispatcher step_id 绑定 | 多 step 时 stdout 事件 step_id 归属正确 |
| D3 | dispatcher sink=None 零行为 | 无 sink 时事件不产生（18 用例不动即证明） |
| D4 | dispatcher 失败 step 也发 step.done | ok=False 的 record 照常推 |
| W1 | ws_act 非 execute 消息 | error/invalid_message |
| W2 | ws_act session 不存在 | error/session_not_found（patch service） |
| W3 | ws_act phase 非 acting | error/illegal_phase |
| W4 | ws_act 完整事件序列 | patch create_default_dispatcher（scripted dispatcher + records）→ step.start/tool.stdout/step.done/plan.done 序列断言 + phase 转移 |
| W5 | ws_act 执行中环境故障 | ToolExecutionError → error/internal |

## 风险与未决

| ID | 风险 | 对策 |
| --- | --- | --- |
| R1 | Sandbox readline 循环与 wait 超时的并发结构重构出错 | 保持「wait_proc 超时 + drain」骨架，仅把 read() 换成行循环 task；双平台全量回归（251 用例兜底） |
| R2 | on_stdout 回调慢导致管道背压 | 回调是 await 的（发送 WS），慢客户端可能拖慢命令执行——MVP 接受（本地单客户端）；不做队列解耦（YAGNI） |
| R3 | tool.exit 事件只有 shell 用 | 协议通用（dict event），fs 工具未来可发其他事件；不预实现 |
| R4 | act 端点同时只允许一次执行 | 无锁（DB phase==acting 检查是弱保护）；并发执行同一 session 是产品外场景，记 TODO |
| R5 | 执行中断（客户端断开）后沙箱残留 | MVP：dispatcher 继续执行完（执行与连接生命周期解耦）；取消语义是后续任务 |

### 已决策（默认值，主人不反对就这么走）

| ID | 决策点 | 决策 | 备选 |
| --- | --- | --- | --- |
| Q1 | 流式粒度 | **真流式**（Sandbox 行级增量回调） | 假流式（执行完拆分延迟推，08 的 plan 模式） |
| Q2 | stdout 切分 | **行级**（readline） | 固定 chunk（边界碎） |
| Q3 | stderr 流式 | **不流式**（全量收集，错误量小） | stderr 也回调 |
| Q4 | 事件集 | **step.start / tool.stdout / tool.exit / step.done / plan.done** | 只要 tool.stdout/tool.exit（21 需要 step 边界） |
| Q5 | tool.exit 载体 | **ExecContext 加 emit_event（EventEmitter 协议）** | 并入 step.done（21 难取 exit_code 数字） |
| Q6 | 端点 | **新建 /act WS 端点**（execute 消息） | 扩展 /plan 端点（语义混杂） |
| Q7 | adapter 来源 | **session.adapter_id**（execute 消息不带 adapter_id） | 消息带 adapter_id |
| Q8 | 状态机接入 | **act 端点检查 phase==acting；执行完 → ACTION_REVIEW** | dispatcher 内接状态机（18 已否） |
| Q9 | sink 协议位置 | **action_dispatcher.py**（ws_act import 之，单向） | ws_streamer.py（core 会引入 fastapi 依赖） |
| Q10 | 回调异常 | **吞掉记 warning**（观察通道不控制执行） | 传播中断执行 |

如以上 10 项主人无异议，请直接回复 `APPROVED`，我立即按本设计落代码并 commit。

## 实施记录（2026-09-02）

交付：`tools/sandbox.py`（on_stdout 行级回调）+ `tools/base.py`（ShellRunner 协议 + ExecContext.emit_event）+ `tools/builtin_tools.py`（ShellTool 流式装配）+ `core/action_dispatcher.py`（ActEventSink + _StdoutAdapter/_EventAdapter）+ `api/ws_act.py`（WSActSink + /act 端点，新建）+ `main.py`（注册路由）。测试新增/扩展：`test_sandbox.py`（+3 on_stdout）、`test_builtin_tools.py`、`test_action_dispatcher.py`、`test_ws_act.py`（W1–W6）、`test_tools_base.py`。零新增依赖。

### 验收数据

- Windows：pytest 269 passed / 4 skipped；ruff 零问题；mypy 72 文件零问题
- Linux VM（192.168.1.147）：流式相关 5 文件 85 passed / 5 skipped；3 个 Linux rlimit 分支（memory/cpu/process_linux）+ 3 个 on_stdout 流式用例 + Linux 禁网（blackhole proxy）分支全部真实运行通过，5 skipped 均为 Windows-only（Job Object × 3 + hard_block × 2）
- 真 DB 用例（12 个）依赖 VM 内 Docker Postgres：VM 关机时表现为 asyncio proactor `OSError: [WinError 121] 信号灯超时时间已到`（TCP connect 超时），非代码回归

### 平台/行为发现

1. **skipif 双向验证**：Windows 上 4 skipped 是 Linux-only 分支，Linux 上 5 skipped 是 Windows-only 分支。平台分支代码（Job Object / rlimit）必须双平台真跑才能闭环，单平台全绿不构成验收。
2. **mypy 协议强制**：`ShellRunner` 协议加 `on_stdout` 后，测试里的 `_FakeShell` 也必须补同签名参数（含 `cwd`/`on_stdout`），否则 `Cannot override writeable attribute` / 签名不符报错——协议扩展的波及面由 mypy 兜底。
3. **win32 管道残留行**：timeout 击杀进程后 readline 循环仍可能吐出缓冲残行，S6 用例（timeout_residual_lines）固定该行为；回调异常按 Q10 决策吞掉记 warning。
4. **ws_act 校验副作用**：`ExecuteMessage.model_validate(raw)` 仅取校验副作用、丢弃返回值（ruff F841），端点不需要 msg 本身。
