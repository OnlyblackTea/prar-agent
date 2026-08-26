"""Tool 抽象基类 / 执行上下文 / 结果模型 / 协议 / 异常层级。

本模块只定义契约，不实现任何真实工具：
  - Task 16 sandbox 实现 ShellRunner
  - Task 17 内置工具继承 Tool
  - Task 19 ws_streamer 实现 StdoutEmitter

失败语义红线：
  - 业务性失败（命令 exit≠0、文件不存在）→ 返回 ToolResult(ok=False)，
    让 LLM 观察 output 后换参数重试
  - 环境故障（沙箱起不来、超时机制失效）→ raise ToolExecutionError，
    由 dispatcher 停机/告警，不让 LLM 无限重试

注意：PEP 695 泛型类（class Tool[ArgsT]）的类体注解在定义时求值，
故 ExecContext / ToolResult 等被 Tool 注解引用的类型必须定义在 Tool 之前。
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Protocol
from uuid import UUID

from pydantic import BaseModel, Field


@dataclass(frozen=True, slots=True)
class ShellResult:
    exit_code: int
    stdout: str
    stderr: str


class ShellRunner(Protocol):
    """shell 命令执行入口。Task 16 sandbox 实现此协议（rlimit/超时/目录隔离/禁网）。"""

    async def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> ShellResult: ...


class StdoutEmitter(Protocol):
    """工具 stdout 流式回调。Task 19 由 ws_streamer 实现，dispatcher 装配进 ctx。"""

    async def emit(self, chunk: str) -> None: ...


@dataclass(frozen=True, slots=True)
class ExecContext:
    """工具执行上下文，由 dispatcher（Task 18）统一构造。

    - workdir：本 step 在沙箱内的隔离工作目录（沙箱视角的相对根）
    - run_shell：shell 工具的执行入口（必填，fs 类工具不用它但 ctx 统一携带）
    - emit_stdout：流式回调，未装配时为 None（Tool._emit 已处理 no-op）
    """

    session_id: UUID
    plan_version: int
    step_id: str
    workdir: Path
    run_shell: ShellRunner
    emit_stdout: StdoutEmitter | None = None


class ToolResult(BaseModel):
    """工具执行结果。output 供 LLM 观察；artifacts 供 Task 20 checkpoint 收集。"""

    ok: bool
    output: str
    # 相对 workdir 的相对路径，绝不存沙箱绝对路径
    artifacts: list[Path] = Field(default_factory=list)
    git_commit: str | None = None  # Task 20 填；15 恒为 None


class Tool[ArgsT: BaseModel](ABC):
    """所有工具的抽象基类。

    子类约定：
      1. 声明三个 ClassVar：name / description / args_schema
      2. args_schema 必须 model_config = ConfigDict(extra="forbid")
         （Task 04 已确立：OpenAI strict mode 不接受 additionalProperties）
      3. 实现 async def execute(self, args: ArgsT, ctx: ExecContext) -> ToolResult
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


class ToolError(Exception):
    """所有工具层异常基类。"""


class ToolNotFoundError(ToolError, KeyError):
    """registry.get 未命中。继承 KeyError 便于 dispatcher 按映射语义处理。"""


class ToolValidationError(ToolError):
    """args 不符合 args_schema（dispatcher 校验时抛，Task 18 用）。"""


class ToolExecutionError(ToolError):
    """工具内部环境故障（沙箱起不来、超时机制失效等），非业务性失败。"""
