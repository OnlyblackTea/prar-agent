# 18. Action Dispatcher + ReAct loop

## 目标

- **一句话**：实现 `core/action_dispatcher.py`——ACTING 阶段执行 plan 的 Step 序列（think → call_tool → observe 微循环）；工具失败时由 LLM 观察并产出修正调用，最多重试到上限后 fail-fast。本任务不接状态机、不改 WS/前端（19 流式事件、21 前端面板）、不落 DB（零模型改动）。

- **验收标准**（缺一不可）：

  1. `cd backend && uv run pytest -q` 全绿（现有 234 + 本任务新增 ~20 用例）
  2. `uv run ruff check src tests` / `uv run mypy src tests` 零警告零错误（mypy strict）
  3. Linux VM（192.168.1.147）上 `test_action_dispatcher.py` 真实跑通（真实 Sandbox + 真实内置工具）
  4. 单测 100% mock LLM（FakeActor 注入，零真调 API——成本纪律）；prompt 文件化于 `llm/prompts/actor.md`

## 输入 / 输出

- **输入**：`PlanDocument`（取 StepNode 节点，含 tool / tool_args / rerunnable）+ `session_id` + `plan_version` + `ResolvedAdapter`（LLM 修正轮用）
- **输出**：`list[StepExecution]`（per-step：ok / attempts / 最终观察 / artifacts / thoughts）

失败语义（沿用 base.py 双轨）：

- 工具业务失败（ToolResult.ok=False）→ 进 LLM 修正轮；修正轮耗尽 → step failed（记录，不抛）
- 环境故障（ToolExecutionError / 沙箱装配失败）→ 向上抛，dispatcher 停机
- plan 契约错误（step.tool 未注册）→ step failed（记录 reason，不抛）；LLM 提出未注册工具 → 本次 attempt 失败计数

## 接口设计

### 执行模型（核心）

```
对每个 StepNode（按序，fail-fast）：
  1. workdir = steps/{step_id}（沙箱视角，dispatcher 创建）
  2. 首轮：直接执行 step.tool + step.tool_args（plan 驱动，不经 LLM）
  3. ok → step 完成；!ok → 修正轮：
     每轮：actor.decide(step, 最近观察) → ActorAction
       - done=True → step 结束（ok 取最后一轮工具结果）
       - done=False → 校验 tool 已注册 + tool_args 符合 args_schema → execute → observe
  4. 修正轮上限 2 轮（初次 + 2 次修正）；耗尽 → step failed
  5. step failed → 立即停止后续 step（fail-fast），返回已执行记录
```

### 数据结构（pydantic，遵循 API 契约规范）

```python
class ActorAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thought: str = Field(description="对当前观察的思考与下一步意图")
    done: bool = Field(description="True=step 完成/放弃，不再调用工具")
    tool: str = ""
    tool_args: dict[str, Any] = Field(default_factory=dict)


class StepExecution(BaseModel):
    step_id: str
    ok: bool
    attempts: int
    output: str  # 最终观察（最后一次工具结果或失败原因）
    artifacts: list[Path] = Field(default_factory=list)
    thoughts: list[str] = Field(default_factory=list)
    failure_reason: str | None = None  # ok=False 时说明原因
```

### 核心类

```python
class ActorProtocol(Protocol):
    """LLM 修正轮决策入口。生产实现 LLMActor；测试注入 FakeActor（成本纪律）。"""

    async def decide(self, *, step: StepNode, observations: list[str]) -> ActorAction: ...


class ActionDispatcher:
    """ACTING 执行引擎：plan Step 序列 + ReAct 修正微循环。"""

    def __init__(
        self,
        registry: ToolRegistry,
        actor: ActorProtocol,
        *,
        sandbox_base: Path = Path("sandbox/runs"),  # 相对 backend 工作目录，构造时 resolve()
        limits: SandboxLimits | None = None,       # None=默认（内存512MB/CPU300s/进程不限）
    ) -> None: ...

    async def execute_plan(
        self, plan: PlanDocument, *, session_id: UUID, plan_version: int,
    ) -> list[StepExecution]: ...
```

- **沙箱生命周期**：`execute_plan` 内构造 `Sandbox(sandbox_base/{session_id})`（ensure_root；**不 cleanup**——会话级生命周期留给 21/26 收尾）；network=False（禁网）；每个 step 的 `ExecContext.workdir = Path(f"steps/{step_id}")`，执行前 mkdir
- **limits 默认**：`SandboxLimits(max_memory_mb=512, max_cpu_seconds=300, max_processes=0)`——进程数不限制（RLIMIT_NPROC 按用户全量进程数计，17 教训），内存/CPU 保留
- **rerunnable 规则**：修正轮中 LLM 提出**与上一轮完全相同的 (tool, tool_args)** 且该工具 `rerunnable=False` → 拒绝（本轮 attempt 失败计数 + 记录 reason），防同参副作用重放；rerunnable=True 或参数有变化则允许
- **args 校验**：LLM 提出的 tool_args 用 `args_schema.model_validate` 校验，失败 → 本轮 attempt 失败计数（`ToolValidationError` 语义：LLM 输入错误，业务可重试）；首轮（plan 提供的 tool_args）校验失败 → step failed（plan 契约错误，不浪费 LLM 轮）
- **ToolNotFoundError**（plan 或 LLM 提出未注册工具）→ 同上：plan 错 → step failed；LLM 错 → attempt 失败计数
- **git_commit**：恒 None（Task 20 填）

### LLMActor（生产实现）

```python
class LLMActor:
    """用现有 LLMRouter.complete_structured 做修正决策（零 router 改动）。

    原生 function-calling 是多轮消息 + tools 参数的较大改造（router 现仅单轮），
    MVP 用结构化输出驱动 ReAct：每轮 system=actor.md prompt，user=step 描述+观察 JSON。
    """

    def __init__(self, router: LLMRouter, adapter: ResolvedAdapter) -> None: ...

    async def decide(self, *, step: StepNode, observations: list[str]) -> ActorAction: ...
```

- `llm/prompts/actor.md`：ReAct 决策 prompt（文件化，不写死在 .py），含三工具 schema 说明 + 决策格式要求 + 成本提醒（优先 done）
- `ActorAction` 走 `complete_structured`（自动重试/schema 兜底）

### 默认工厂

```python
def create_default_dispatcher(
    router: LLMRouter, adapter: ResolvedAdapter,
) -> ActionDispatcher:
    """组装：ToolRegistry + builtin_tools() + LLMActor + 默认沙箱参数。"""
```

- Task 17 的 `builtin_tools()` 在此消费（Q8 约定）；19 的流式装配、API 层接入留后续任务

## 文件清单

| 文件 | 操作 | 说明 |
| --- | --- | --- |
| `backend/src/app/core/action_dispatcher.py` | 新建 | ActorAction / StepExecution / ActorProtocol / ActionDispatcher / LLMActor / 工厂 |
| `backend/src/app/llm/prompts/actor.md` | 新建 | ReAct 决策 prompt（文件化） |
| `backend/tests/test_action_dispatcher.py` | 新建 | ~20 用例（FakeActor，零真调 LLM） |

零现有文件改动；零新增依赖。

## 实施步骤

1. **TDD 红**：写 `test_action_dispatcher.py`（FakeActor 注入）
2. **绿**：实现 `action_dispatcher.py` + `actor.md`
3. **Windows 质量门**：`uv run pytest -q` + `ruff check` + `mypy` 三绿
4. **Linux VM 验证**：同步源码，`test_action_dispatcher.py` 真实跑通（真实 Sandbox + 真实工具）
5. **文档收尾 + commit**：本设计补「实施记录」章节，commit 交付主人 GPG 签名

## 测试清单（test_action_dispatcher.py）

| ID | 用例 | 断言要点 |
| --- | --- | --- |
| T1 | 成功路径（fs.write） | attempts=1、ok=True、artifacts 收集、thoughts 空 |
| T2 | 失败→修正成功（shell exit≠0 → FakeActor 换参数成功） | attempts=2、thoughts 记录 1 条、ok=True |
| T3 | 修正轮耗尽 | FakeActor 恒失败 → ok=False、attempts=3、failure_reason 非空 |
| T4 | FakeActor done=True 放弃 | ok=False、不再调用工具 |
| T5 | rerunnable 规则 | FakeActor 提同参且工具 rerunnable=False → 拒绝（attempt 计数、reason 记录）；参数变化 → 允许 |
| T6 | plan tool 未注册 | step failed（reason 含 unknown tool），后续 step 不执行（fail-fast） |
| T7 | LLM 提出未注册工具 | attempt 失败计数，不死循环 |
| T8 | LLM tool_args 不符合 schema | attempt 失败计数 |
| T9 | plan tool_args 不符合 schema | step failed（不经 LLM 轮） |
| T10 | fail-fast | step1 failed → step2 不执行，返回 1 条记录 |
| T11 | workdir 隔离 | 每个 step 的 workdir 创建于沙箱根下，step 间文件可见 |
| T12 | 空 plan（无 Step 节点） | 返回空列表 |
| T13 | 沙箱参数传递 | 构造的 Sandbox root/network=False/limits 断言 |
| T14 | 集成闭环 | 真实 Sandbox + 真实工具 + FakeActor：fs.write 成功 + shell 失败修正（cat 修正）成功 |

（FakeActor 是 scripted 决策序列：预设每轮返回的 ActorAction；单测零真调 LLM API。）

## 风险与未决

| ID | 风险 | 对策 |
| --- | --- | --- |
| R1 | 修正轮 token 成本（每轮重传 prompt + 观察） | 上限 2 轮 + user 只含当前 step + 最近观察（不传全历史）+ prompt 内提示优先 done |
| R2 | LLM 修正轮死循环（反复提无效参数） | 修正轮上限硬顶 + rerunnable 同参拒绝规则 |
| R3 | shell 工具副作用不可重放 | rerunnable=False 同参拒绝；参数变化的新调用视为新操作 |
| R4 | 沙箱根相对路径依赖进程 cwd | 构造时 `resolve()` 固化；文档注明启动须在 backend/ 目录 |
| R5 | step failed 后剩余 step 状态不明 | fail-fast + failure_reason 记录；19/21 流式事件据此展示 |
| R6 | 修正轮 LLM 异常（LLMError） | 向上抛（环境故障），dispatcher 停机，不静默 |

### 已决策（默认值，主人不反对就这么走）

| ID | 决策点 | 决策 | 备选 |
| --- | --- | --- | --- |
| Q1 | 执行模型 | **plan 驱动确定性首轮 + 失败 LLM 修正微循环** | 纯 ReAct（每 step 都问 LLM，成本高）；纯确定性（无自愈） |
| Q2 | 修正轮上限 | **2 轮**（初次 + 2 次修正） | 1 轮 / 3 轮 |
| Q3 | LLM 修正实现 | **现有 complete_structured + ActorAction**（零 router 改动） | 原生 function calling（router 多轮改造，成本大） |
| Q4 | step 失败后 | **fail-fast 停止**（人工介入 ACTION_REVIEW） | 继续后续 step |
| Q5 | 沙箱 limits | **内存 512MB / CPU 300s / 进程数 0（不限）** | 进程数 16（17 教训：NPROC 按用户全量计） |
| Q6 | 状态机/DB 接入 | **18 不接**（纯执行引擎，返回记录；API 层接入随 19/21） | 18 内建 phase 转移 |
| Q7 | 沙箱清理 | **18 不 cleanup**（会话级生命周期，21/26 收尾） | step 结束即删 |
| Q8 | rerunnable 消费 | **同参重放拒绝规则**（rerunnable=False 且参数未变 → 拒绝） | 18 完全不消费 rerunnable |
| Q9 | StepExecution 类型 | **pydantic**（19 流式事件将复用） | dataclass |

如以上 9 项主人无异议，请直接回复 `APPROVED`，我立即按本设计落代码并 commit。

---

## 实施记录（2026-08-27）

交付：`core/action_dispatcher.py`（289 行）+ `llm/prompts/actor.md` + `tests/test_action_dispatcher.py`（17 用例）。零现有文件改动、零新增依赖。

### 验收数据

- Windows：pytest 251 passed / 4 skipped；ruff 零问题；mypy 70 文件零问题
- Linux VM（192.168.1.147）：`test_action_dispatcher.py` 17 passed（真实 Sandbox + 真实内置工具 + /bin/sh）

### 平台/行为发现

1. **Windows cmd 把 `/` 开头的路径段当选项开关**：`type ../step_001/a.txt` 失败（exit=1），因 `dir ..` 成功而 `dir ../step_001` 失败才定位。cmd 内路径必须全反斜杠（`..\step_001\a.txt`）。测试 T11 的跨 step 可见性命令平台分支由此确立。
2. **rerunnable 同参拒绝与「轮次耗尽」测试的语义冲突**：shell 工具 `rerunnable=False`，T3 初版用与首轮相同参数的失败命令会被同参拒绝拦截（attempts 不增），导致测不到耗尽。T3 改用尾随空格等不同参数的失败命令绕开拒绝。
3. **拒绝原因回灌观察**：修正轮校验拒绝（未知工具 / args 不符 / 同参重放）时，拒绝原因以 `[rejected]` 前缀追加进 observations 传给下一轮 decide——LLM 据此避免重提，测试断言第二轮观察含 [rejected] 标记。
4. **ActorAction.done 必填**：pydantic 无默认值即必填，测试 helper `_act` 用 `setdefault("done", False)`。
