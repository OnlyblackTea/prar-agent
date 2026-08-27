# 15. Tool ABC + Registry（M3 基石）

## 目标

* **一句话**：定义 Tool 抽象基类（ABC）+ `ToolRegistry` 注册表，产出标准的 function-calling JSON Schema，让后续内置工具（17）、Action Dispatcher（18）都建立在同一套契约上。

* **验收标准**（缺一不可）：

  1. `cd backend && make test` 全绿（旧 66 + 本任务新增 \~18 用例）
  2. `cd backend && make lint && make typecheck` 零警告/零错误（mypy strict）
  3. 本任务**不注册任何真实工具**——内置工具（shell / fs.read / fs.write）是 Task 17 的交付

     1. 本任务**不接状态机 / 不碰 LLM router / 不改前端**——纯后端新模块，零现有文件改动

## 输入 / 输出

**前置任务**：

* Task 01（后端骨架 + Settings）— ✅

* Task 03（state machine，Phase.ACTING 语义）— ✅（不直接依赖）

* Task 04/04.1（LLM router，pydantic 结构化输出惯例）— ✅（不直接依赖）

* DESIGN.md §6.3 Tool 抽象概型 — ✅（本任务展开细化）

**交付物清单**：

* `backend/src/app/tools/__init__.py`：公开符号导出

* `backend/src/app/tools/base.py`：`Tool[ArgsT]` ABC + `ToolResult` + `ExecContext` + `ShellRunner`/`StdoutEmitter` 协议 + `ShellResult` + 异常层级

* `backend/src/app/tools/registry.py`：`ToolRegistry` + `ToolSpec`

* `backend/tests/test_tools_base.py`：ABC/数据类/协议/异常的 \~10 用例

* `backend/tests/test_tools_registry.py`：注册表的 \~8 用例

**不交付**（留给后续 task）：

* 沙箱实现（→ Task 16 `tools/sandbox.py`，实现 `ShellRunner` 协议）

* 内置工具（→ Task 17 `tools/builtin/`，继承 `Tool`）

* ReAct 调度（→ Task 18 `core/action_dispatcher.py`，用 `registry.get` / `registry.to_specs`）

* 工具输出流式管道（→ Task 19，ws\_streamer 实现 `StdoutEmitter` 协议）

* git checkpoint（→ Task 20，填 `ToolResult.git_commit`）

## 接口设计

### 目录结构（增量）

```
backend/src/app/tools/
    __init__.py        # 新增：导出 Tool / ToolResult / ExecContext / ShellRunner /
                       #       StdoutEmitter / ShellResult / ToolRegistry / ToolSpec / 异常
    base.py            # 新增（~130 行）
    registry.py        # 新增（~70 行）
backend/tests/
    test_tools_base.py      # 新增
    test_tools_registry.py  # 新增
```

4 新增 + 0 修改。无新依赖（pydantic v2 已有）。

### `base.py`：Tool ABC

```python
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar
from uuid import UUID

from pydantic import BaseModel


class Tool[ArgsT: BaseModel](ABC):
    """所有工具的抽象基类。

    子类约定：
      1. 声明三个 ClassVar：name / description / args_schema
      2. args_schema 必须 `model_config = ConfigDict(extra="forbid")`
         （Task 04 已确立：OpenAI strict mode 不接受 additionalProperties）
      3. 实现 `async def execute(self, args: ArgsT, ctx: ExecContext) -> ToolResult`
      4. 可预期失败（命令 exit≠0、文件不存在）→ 返回 ToolResult(ok=False)，
         让 LLM 观察后修正；环境故障（沙箱起不来）→ raise ToolExecutionError
    """

    name: ClassVar[str]
    description: ClassVar[str]
    args_schema: ClassVar[type[BaseModel]]
    rerunnable: ClassVar[bool] = True

    @property
    def json_schema(self) -> dict[str, Any]:
        """function-calling 用的 JSON Schema（pydantic model_json_schema 原生输出）。"""
        return self.args_schema.model_json_schema()

    @abstractmethod
    async def execute(self, args: ArgsT, ctx: ExecContext) -> ToolResult:
        """执行工具。args 已由 dispatcher 用 args_schema 校验为强类型实例。"""
        ...

    async def _emit(self, ctx: ExecContext, chunk: str) -> None:
        """流式输出辅助：emit_stdout 未装配时安全 no-op（Task 19 装配真实实现）。"""
        if ctx.emit_stdout is not None:
            await ctx.emit_stdout.emit(chunk)
```

> 泛型 `Tool[ArgsT]` 用 PEP 695 语法（Python 3.12+，与 `LLMRouter.complete_structured[T]` 同风格），让子类 `execute` 拿到精确的 args 类型，mypy strict 下不违反 LSP。

### `base.py`：ToolResult

```python
class ToolResult(BaseModel):
    """工具执行结果。output 供 LLM 观察；artifacts 供 Task 20 checkpoint 收集。"""

    ok: bool
    output: str
    artifacts: list[Path] = []          # 相对 workdir 的相对路径，绝不存沙箱绝对路径
    git_commit: str | None = None       # Task 20 填；15 恒为 None
```

### `base.py`：ExecContext + 协议

```python
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ShellResult:
    exit_code: int
    stdout: str
    stderr: str


class ShellRunner(Protocol):
    """shell 命令执行入口。Task 16 sandbox 实现此协议（rlimit/超时/目录隔离/禁网）。

    cwd 为沙箱视角的相对路径（相对沙箱根）；None 表示沙箱根。
    （2026-08-27 由 Task 16 触发扩展，见文末设计变更章节）
    """

    async def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> ShellResult: ...


class StdoutEmitter(Protocol):
    """工具 stdout 流式回调。Task 19 由 ws_streamer 实现，dispatcher 装配进 ctx。"""

    async def emit(self, chunk: str) -> None: ...


@dataclass(frozen=True, slots=True)
class ExecContext:
    """工具执行上下文，由 dispatcher（Task 18）统一构造。

    - workdir：本 step 在沙箱内的隔离工作目录（沙箱视角的相对根）
    - run_shell：shell 工具的执行入口（必填，fs 类工具不用它但 ctx 统一携带）
    - emit_stdout：流式回调，未装配时为 None（`Tool._emit` 已处理 no-op）
    """

    session_id: UUID
    plan_version: int
    step_id: str
    workdir: Path
    run_shell: ShellRunner
    emit_stdout: StdoutEmitter | None = None
```

### `base.py`：异常层级

```python
class ToolError(Exception):
    """所有工具层异常基类。"""


class ToolNotFoundError(ToolError, KeyError):
    """registry.get 未命中。继承 KeyError 便于 dispatcher 按映射语义处理。"""


class ToolValidationError(ToolError):
    """args 不符合 args_schema（dispatcher 校验时抛，Task 18 用）。"""


class ToolExecutionError(ToolError):
    """工具内部环境故障（沙箱起不来、超时机制失效等），非业务性失败。"""
```

**语义红线**（写入 base.py docstring）：

* 业务性失败 = `ToolResult(ok=False)`（LLM 观察 output 后可换参数重试）

* 环境故障 = `ToolExecutionError`（dispatcher 层面停机或告警，不让 LLM 无限重试）

### `registry.py`：ToolRegistry + ToolSpec

```python
from dataclasses import dataclass
from typing import Any

from app.tools.base import Tool, ToolNotFoundError


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """喂给 LLM 的 function-calling 描述（标准 JSON Schema）。

    各 provider 的差异格式（OpenAI tools 数组 / Anthropic input_schema）
    由 Task 18/router 转换，本模块只产标准 JSON Schema。
    """

    name: str
    description: str
    parameters: dict[str, Any]


class ToolRegistry:
    """工具注册表：注册/查询/枚举。单实例，dispatcher 持有。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool[Any]] = {}

    def register(self, tool: Tool[Any]) -> None:
        """注册工具；name 重复 → ValueError（启动期配置错误，fail fast）。"""

    def get(self, name: str) -> Tool[Any]:
        """按名查询；未命中 → ToolNotFoundError。"""

    def list_names(self) -> list[str]:
        """按注册序返回全部工具名。"""

    def to_specs(self) -> list[ToolSpec]:
        """按注册序生成 function-calling schema 列表（Task 18 直接喂 LLM）。"""
```

### 关键数据流

```
┌─ 注册期（Task 17）─────────────────────────────┐
│ registry.register(ShellTool())                  │
│ registry.register(FsReadTool())                 │
│ registry.register(FsWriteTool())                │
└─────────────────────────────────────────────────┘

┌─ 执行期（Task 18 dispatcher）──────────────────┐
│ specs = registry.to_specs()          → LLM     │
│ tool  = registry.get(name)                      │
│ args  = tool.args_schema.model_validate(raw)    │
│ ── ToolValidationError 时反馈 LLM 重新出参 ──   │
│ result = await tool.execute(args, ctx)          │
│   ├─ ToolResult(ok=True/False) → observe LLM   │
│   ├─ ToolExecutionError        → dispatcher 停机│
│   └─ ctx.emit_stdout.emit()    → WS tool.stdout │
└─────────────────────────────────────────────────┘
```

## 文件清单

| 路径                                     | 类型 | 说明                                                                                                                              |
| -------------------------------------- | -- | ------------------------------------------------------------------------------------------------------------------------------- |
| `backend/src/app/tools/__init__.py`    | 新增 | 导出 `Tool` / `ToolResult` / `ExecContext` / `ShellRunner` / `StdoutEmitter` / `ShellResult` / `ToolRegistry` / `ToolSpec` + 4 异常 |
| `backend/src/app/tools/base.py`        | 新增 | Tool ABC + 数据类 + 协议 + 异常层级（\~130 行）                                                                                             |
| `backend/src/app/tools/registry.py`    | 新增 | ToolRegistry + ToolSpec（\~70 行）                                                                                                 |
| `backend/tests/test_tools_base.py`     | 新增 | \~10 用例                                                                                                                         |
| `backend/tests/test_tools_registry.py` | 新增 | \~8 用例                                                                                                                          |

## 实施步骤

1. 写 `tests/test_tools_base.py`（TDD 红：FakeTool 最小子类 + 数据类/异常用例）
2. 写 `tests/test_tools_registry.py`（TDD 红）
3. 实现 `base.py`
4. 实现 `registry.py` + `__init__.py`
5. `cd backend && make test` 全绿（66 + 18）
6. `cd backend && make lint && make typecheck` 零警告/零错误
7. commit：设计文档 + 4 文件同 commit，message `feat(backend): Tool ABC + registry (M3-15)`，`Refs: docs/design/15-tool-abc-registry.md`

## 测试清单

### `test_tools_base.py`

| #   | 测试                            | 断言                                                                                                              |
| --- | ----------------------------- | --------------------------------------------------------------------------------------------------------------- |
| T1  | 最小工具子类（FakeTool）实例化           | `name`/`description`/`args_schema` 可读，`rerunnable == True`                                                      |
| T2  | `json_schema` 属性              | 与 `args_schema.model_json_schema()` 一致，`type == "object"`                                                       |
| T3  | `rerunnable=False` 显式声明       | 子类声明 `False` 后被保留                                                                                               |
| T4  | `ToolResult` 构造与默认值           | `ok`/`output` 必填；`artifacts == []`、`git_commit is None` 默认                                                      |
| T5  | `ToolResult.artifacts` 相对路径约定 | 可放 `Path("src/main.py")`，序列化正常                                                                                  |
| T6  | `ExecContext` frozen + 默认     | 构造合法；`emit_stdout` 缺省为 `None`；修改字段抛 FrozenInstanceError                                                         |
| T7  | `_emit` 装配时转发                 | fake emitter 记录 chunk，`_emit` 后收到同一字符串                                                                          |
| T8  | `_emit` 未装配时 no-op            | `ctx.emit_stdout is None` 时 `_emit` 不抛                                                                          |
| T9  | 异常层级                          | `ToolNotFoundError` 同时是 `ToolError` 和 `KeyError` 实例；`ToolValidationError`/`ToolExecutionError` 是 `ToolError` 子类 |
| T10 | 抽象约束                          | 不实现 `execute` 的子类无法实例化（`TypeError`）                                                                             |

### `test_tools_registry.py`

| #  | 测试                | 断言                                                   |
| -- | ----------------- | ---------------------------------------------------- |
| R1 | register + get 往返 | `get("fake")` 返回注册的同一实例                              |
| R2 | 重复 name           | `register` 二次抛 `ValueError`                          |
| R3 | 未命中               | `get("nope")` 抛 `ToolNotFoundError`                  |
| R4 | `list_names` 注册序  | `["fake_a", "fake_b"]` 与注册顺序一致                       |
| R5 | `to_specs` 内容     | name/description 正确；`parameters == tool.json_schema` |
| R6 | `to_specs` 顺序     | 与注册序一致                                               |
| R7 | 空注册表              | `list_names() == []`、`to_specs() == []`              |
| R8 | 泛型工具注册            | `Tool[FakeArgs]` 子类实例可注册，get 后 `json_schema` 可用      |

### 边缘情况

* `args_schema` 缺 `extra="forbid"` → 15 不强制校验（约定写入 docstring）；17 写内置工具时遵守，测试各自断言 `"additionalProperties" not in json_schema`

* `to_specs` 的 `parameters` 是 `model_json_schema()` 原样透传，不裁剪 `title` 等元字段（各家 provider 都能接受完整 JSON Schema；真有兼容问题在 Task 18 加转换层）

* Protocol 不做 `runtime_checkable`（无需 isinstance 检查，鸭子类型足够）

### 集成测试入口

```bash
cd backend && make test          # 66 + 18 全绿
cd backend && make lint          # ruff 零警告
cd backend && make typecheck     # mypy strict 零错误
```

## 风险与未决

### 已识别风险

| 风险                                          | 缓解                                                                                                       |
| ------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `ShellRunner.run` 签名与 Task 16 sandbox 设计不吻合 | 16 若需扩展签名（如加 stdin / 资源限制参数），走 WORKFLOW §5 设计变更流程，15 协议原地更新                                              |
| mypy strict 对 `ClassVar` + 泛型 ABC 的兼容性      | 实施时先跑 typecheck 验证；若 PEP 695 泛型 ABC 遇阻，降级方案：`execute(args: BaseModel)` + 工具内部 `model_validate` 断言        |
| `Tool[Any]` 在 registry 里丢失参数类型精度            | 可接受：dispatcher 拿到 `Tool[Any]` 后用 `args_schema.model_validate` 产出强类型实例再调 `execute`，类型安全由 `args_schema` 兜底 |
| artifacts 存绝对路径泄漏沙箱布局                       | 约定写死「相对 workdir 的相对路径」+ T5 测试固化；17 工具实现遵守                                                                |
| 流式 chunk 粒度未定（字符级 vs 行级）                    | 协议 `chunk: str` 不限粒度；行级由 Task 19 的 shell 工具实现决定，15 不锁死                                                   |

### 已决策（默认值，主人不反对就这么走）

| #  | 项目                  | 决策                                                                          | 反对就告诉我                  |
| -- | ------------------- | --------------------------------------------------------------------------- | ----------------------- |
| Q1 | `execute` 的 args 类型 | **泛型 `Tool[ArgsT]` + 强类型 `ArgsT`**（PEP 695），而非 DESIGN.md 概型的裸 `dict`        | "dict + 工具内自查"          |
| Q2 | ExecContext 装配方式    | **frozen dataclass 一次性装配**，dispatcher 统一构造                                  | "每工具按需自取"               |
| Q3 | 沙箱耦合                | **`ShellRunner` 协议先行**（15 定义签名，16 实现），15 不依赖 16 代码                          | "15 只写 ABC，沙箱协议完全留给 16" |
| Q4 | 流式回调                | **`StdoutEmitter` 协议 + `emit_stdout: None` 默认**，`Tool._emit` 处理 no-op       | "emit 必填"               |
| Q5 | 业务失败 vs 环境故障        | **双轨**：`ToolResult(ok=False)` vs `ToolExecutionError`                       | "全部抛异常"                 |
| Q6 | 重复注册                | **`ValueError`**（启动期配置错误 fail fast）                                         | "ToolError 子类"          |
| Q7 | 工具命名                | **保持 `shell` / `fs.read` / `fs.write`**（与 plan\_engine `_DEFAULT_TOOLS` 一致） | "全下划线"                  |
| Q8 | registry 单例         | **类 + 显式构造**（dispatcher 持有），不提供模块级单例/工厂                                     | "模块级 `get_registry()`"  |

如以上 8 项主人无异议，请直接回复 `APPROVED`，我立即按本设计落代码并 commit。

## 设计变更 (2026-08-27)

> Task 16 落地时触发，走 WORKFLOW §5 流程（本风险表预留路径）。

### `ShellRunner.run` 协议扩展 `cwd`

- **变更**：`ShellRunner.run` 增加 `cwd: Path | None = None`（keyword-only，
  沙箱视角相对路径；None = 沙箱根）。
- **动机**：Task 17 的 shell 工具需把 `ExecContext.workdir`（沙箱视角的
  相对根）传给沙箱，否则 shell 工具无法在计划 step 的隔离工作目录内执行。
- **语义**：相对路径 + 逃逸即 `ToolExecutionError`；不新增 stdin
  （Task 18 若需交互式 stdin 再走 §5 扩展）。
- **落地**：`base.py` 协议原地更新（正文同步新签名）；`test_tools_base.py`
  的 `_FakeShell.run` 同步签名（mypy structural 兼容需要）。
