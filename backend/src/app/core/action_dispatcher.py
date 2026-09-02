"""Action Dispatcher：ACTING 阶段执行 plan Step 序列 + ReAct 修正微循环。

执行模型（docs/design/18-action-dispatcher.md）：
  - 首轮确定性执行 step.tool + step.tool_args（plan 驱动，不经 LLM）
  - 工具 ok=False → LLM 修正轮（ActorProtocol.decide），最多 2 轮
  - 修正轮校验失败（未知工具 / args 不符 / rerunnable 同参重放）→ 消耗一轮，
    拒绝原因作为观察反馈给下一轮
  - step failed → fail-fast 停止后续 step

失败语义红线（沿用 base.py）：
  - 业务失败（ToolResult.ok=False）→ 记录为 step failed / 进修正轮，不抛
  - 环境故障（ToolExecutionError）→ 向上抛，dispatcher 停机
"""

import json
from collections.abc import Awaitable
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.logging import get_logger
from app.core.plan_schemas import PlanDocument, StepNode
from app.llm.router import LLMRouter
from app.llm.types import ResolvedAdapter
from app.tools.base import ExecContext, ToolNotFoundError
from app.tools.builtin import builtin_tools
from app.tools.registry import ToolRegistry
from app.tools.sandbox import Sandbox, SandboxLimits

_log = get_logger("action_dispatcher")

_DEFAULT_LIMITS = SandboxLimits(max_memory_mb=512, max_cpu_seconds=300, max_processes=0)
_MAX_CORRECTION_ROUNDS = 2
_DEFAULT_SANDBOX_BASE = Path("sandbox/runs")


def _load_prompt(filename: str) -> str:
    """从 llm/prompts/ 加载 prompt 模板文件。"""
    prompts_dir = Path(__file__).resolve().parent.parent / "llm" / "prompts"
    target = (prompts_dir / filename).resolve()
    if not target.is_relative_to(prompts_dir):
        raise ValueError(f"Invalid prompt filename: {filename}")
    return target.read_text(encoding="utf-8")


# ===== 数据模型 =====


class ActorAction(BaseModel):
    """LLM 修正轮决策：放弃或提出下一次工具调用。"""

    model_config = ConfigDict(extra="forbid")

    thought: str = Field(description="对当前观察的思考与下一步意图")
    done: bool = Field(description="True=step 完成/放弃，不再调用工具")
    tool: str = ""
    tool_args: dict[str, Any] = Field(default_factory=dict)


class StepExecution(BaseModel):
    """单个 step 的执行记录（Task 19 流式事件复用）。"""

    step_id: str
    ok: bool
    attempts: int
    output: str  # 最终观察（最后一次工具结果或失败原因）
    artifacts: list[Path] = Field(default_factory=list)
    thoughts: list[str] = Field(default_factory=list)
    failure_reason: str | None = None  # ok=False 时说明原因


class ActorProtocol(Protocol):
    """LLM 修正轮决策入口。生产实现 LLMActor；测试注入 FakeActor（成本纪律）。"""

    async def decide(self, *, step: StepNode, observations: list[str]) -> ActorAction: ...


class ActEventSink(Protocol):
    """Task 19 流式事件接收端。sink=None 时全部跳过（18 调用方零改动）。"""

    async def step_start(self, *, index: int, step: StepNode) -> None: ...
    async def tool_stdout(self, *, step_id: str, chunk: str) -> None: ...
    async def tool_exit(self, *, step_id: str, exit_code: int, ok: bool) -> None: ...
    async def step_done(self, *, record: StepExecution) -> None: ...


class _StdoutAdapter:
    """工具侧 emit(chunk) 绑定 step_id 转发 sink；异常吞掉（观察通道不控制执行）。"""

    def __init__(self, sink: ActEventSink, step_id: str) -> None:
        self._sink = sink
        self._step_id = step_id

    async def emit(self, chunk: str) -> None:
        try:
            await self._sink.tool_stdout(step_id=self._step_id, chunk=chunk)
        except Exception:
            _log.warning("tool.stdout sink callback failed", exc_info=True)


class _EventAdapter:
    """工具侧 emit(event) 绑定 step_id 转发 sink；非 tool.exit 事件记 warning 忽略。"""

    def __init__(self, sink: ActEventSink, step_id: str) -> None:
        self._sink = sink
        self._step_id = step_id

    async def emit(self, event: dict[str, Any]) -> None:
        if event.get("type") != "tool.exit":
            _log.warning("unexpected tool event ignored: %s", event.get("type"))
            return
        try:
            await self._sink.tool_exit(
                step_id=self._step_id,
                exit_code=event["exit_code"],
                ok=event["ok"],
            )
        except Exception:
            _log.warning("tool.exit sink callback failed", exc_info=True)


# ===== Dispatcher =====


class ActionDispatcher:
    """ACTING 执行引擎：plan Step 序列 + ReAct 修正微循环。

    沙箱生命周期：execute_plan 内构造 Sandbox(sandbox_base/{session_id})，
    network=False 禁网，不 cleanup（会话级生命周期留给后续任务）。
    """

    def __init__(
        self,
        registry: ToolRegistry,
        actor: ActorProtocol,
        *,
        sandbox_base: Path = _DEFAULT_SANDBOX_BASE,
        limits: SandboxLimits | None = None,
    ) -> None:
        self.registry = registry
        self._actor = actor
        self._sandbox_base = sandbox_base.resolve()
        self._limits = limits or _DEFAULT_LIMITS

    async def execute_plan(
        self,
        plan: PlanDocument,
        *,
        session_id: UUID,
        plan_version: int,
        sink: ActEventSink | None = None,
    ) -> list[StepExecution]:
        """按序执行 plan 的所有 Step 节点；step failed → fail-fast。

        sink 装配后推 step.start / tool.stdout / tool.exit / step.done 事件；
        事件回调异常只记 warning，不中断执行（观察通道不控制执行）。
        """
        steps = [n for n in plan.nodes if isinstance(n, StepNode)]
        sandbox = Sandbox(
            self._sandbox_base / str(session_id),
            limits=self._limits,
            network=False,
        )
        sandbox.ensure_root()
        records: list[StepExecution] = []
        for i, step in enumerate(steps):
            step_id = step.id or f"step_{i:03d}"
            if sink is not None:
                await self._emit_safely(sink.step_start(index=i, step=step))
            workdir = Path("steps") / step_id
            (sandbox.root / workdir).mkdir(parents=True, exist_ok=True)
            ctx = ExecContext(
                session_id=session_id,
                plan_version=plan_version,
                step_id=step_id,
                workdir=workdir,
                run_shell=sandbox,
                emit_stdout=_StdoutAdapter(sink, step_id) if sink is not None else None,
                emit_event=_EventAdapter(sink, step_id) if sink is not None else None,
            )
            record = await self._execute_step(step, ctx)
            if sink is not None:
                await self._emit_safely(sink.step_done(record=record))
            records.append(record)
            if not record.ok:
                break
        return records

    @staticmethod
    async def _emit_safely(coro: Awaitable[None]) -> None:
        """sink 回调异常吞掉记 warning：连接断开等不中断计划执行。"""
        try:
            await coro
        except Exception:
            _log.warning("sink callback failed", exc_info=True)

    async def _execute_step(self, step: StepNode, ctx: ExecContext) -> StepExecution:
        # 首轮（plan 驱动）：契约错误（未知工具 / 参数不符）→ step failed，不浪费 LLM 轮
        try:
            tool = self.registry.get(step.tool)
        except ToolNotFoundError:
            return self._failed(ctx.step_id, 0, "", f"unknown tool: {step.tool!r}")
        try:
            args = tool.args_schema.model_validate(step.tool_args)
        except ValidationError as e:
            return self._failed(
                ctx.step_id, 0, "", f"invalid plan tool_args for {step.tool!r}: {e}",
            )
        # ToolExecutionError 环境故障 → 向上抛，dispatcher 停机
        result = await tool.execute(args, ctx)
        attempts = 1
        last_result = result
        thoughts: list[str] = []
        observations: list[str] = [result.output]
        prev_call: tuple[str, dict[str, Any]] = (step.tool, step.tool_args)
        if result.ok:
            return StepExecution(
                step_id=ctx.step_id,
                ok=True,
                attempts=attempts,
                output=result.output,
                artifacts=list(result.artifacts),
            )

        # 修正轮（ReAct）：拒绝原因作为观察反馈给 LLM 下一轮
        for _ in range(_MAX_CORRECTION_ROUNDS):
            action = await self._actor.decide(step=step, observations=observations)
            thoughts.append(action.thought)
            if action.done:
                return StepExecution(
                    step_id=ctx.step_id,
                    ok=False,
                    attempts=attempts,
                    output=last_result.output,
                    artifacts=list(last_result.artifacts),
                    thoughts=thoughts,
                    failure_reason="actor gave up",
                )
            rejection = self._check_action(action, prev_call)
            if rejection is not None:
                observations.append(f"[rejected] {rejection}")
                continue
            tool = self.registry.get(action.tool)
            args = tool.args_schema.model_validate(action.tool_args)
            result = await tool.execute(args, ctx)
            attempts += 1
            last_result = result
            prev_call = (action.tool, action.tool_args)
            observations.append(result.output)
            if result.ok:
                return StepExecution(
                    step_id=ctx.step_id,
                    ok=True,
                    attempts=attempts,
                    output=result.output,
                    artifacts=list(result.artifacts),
                    thoughts=thoughts,
                )

        return StepExecution(
            step_id=ctx.step_id,
            ok=False,
            attempts=attempts,
            output=last_result.output,
            artifacts=list(last_result.artifacts),
            thoughts=thoughts,
            failure_reason="correction rounds exhausted",
        )

    def _check_action(
        self,
        action: ActorAction,
        prev_call: tuple[str, dict[str, Any]],
    ) -> str | None:
        """校验 LLM 提出的调用；不合法返回拒绝原因，合法返回 None。"""
        try:
            tool = self.registry.get(action.tool)
        except ToolNotFoundError:
            return f"unknown tool: {action.tool!r}"
        if (
            action.tool == prev_call[0]
            and action.tool_args == prev_call[1]
            and not tool.rerunnable
        ):
            return f"tool {action.tool!r} is not rerunnable with identical args"
        try:
            tool.args_schema.model_validate(action.tool_args)
        except ValidationError as e:
            return f"invalid tool_args for {action.tool!r}: {e}"
        return None

    @staticmethod
    def _failed(
        step_id: str,
        attempts: int,
        output: str,
        reason: str,
    ) -> StepExecution:
        return StepExecution(
            step_id=step_id,
            ok=False,
            attempts=attempts,
            output=output,
            failure_reason=reason,
        )


# ===== LLM Actor =====


class LLMActor:
    """修正轮决策：用 LLMRouter.complete_structured 结构化输出 ActorAction。

    原生 function-calling 需 router 多轮改造（成本大），MVP 用单轮结构化输出
    驱动 ReAct（docs/design/18-action-dispatcher.md Q3）。
    """

    def __init__(self, router: LLMRouter, adapter: ResolvedAdapter) -> None:
        self._router = router
        self._adapter = adapter
        self._actor_prompt = _load_prompt("actor.md")

    async def decide(self, *, step: StepNode, observations: list[str]) -> ActorAction:
        user = self._actor_prompt.format(
            step_title=step.title,
            step_description=step.description,
            step_tool=step.tool,
            step_tool_args=json.dumps(step.tool_args, ensure_ascii=False),
            observations="\n\n".join(observations),
        )
        response = await self._router.complete_structured(
            adapter=self._adapter,
            system="你是一个严谨的行动执行者。",
            user=user,
            schema=ActorAction,
        )
        return response.parsed


# ===== 默认工厂 =====


def create_default_dispatcher(
    router: LLMRouter,
    adapter: ResolvedAdapter,
) -> ActionDispatcher:
    """组装：ToolRegistry + builtin_tools() + LLMActor + 默认沙箱参数。"""
    registry = ToolRegistry()
    for t in builtin_tools():
        registry.register(t)
    return ActionDispatcher(registry, LLMActor(router, adapter))
