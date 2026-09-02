"""长期记忆三层：episodic 确定性写入（零 LLM）。

semantic/procedural 写路径属任务 24 Consolidator，本模块不写空壳方法。
"""

from dataclasses import dataclass
from uuid import UUID

from app.core.plan_schemas import DecisionNode, PlanDocument, StepNode
from app.db.models import Memory
from app.services.memory_service import MemoryService


@dataclass(frozen=True, slots=True)
class StepOutcome:
    """单个 step 的执行结果摘要（数据源：ws_act 落库的 last_run）。"""

    step_id: str
    ok: bool
    git_commit: str | None = None


@dataclass(frozen=True, slots=True)
class RunSummary:
    """一次 acting 的紧凑摘要（持久化于 session.metadata_json["last_run"]）。"""

    plan_version: int
    all_ok: bool
    steps: list[StepOutcome]


def build_episodic_content(
    *,
    init_request: str,
    plan_version: int,
    plan: PlanDocument,
    run: RunSummary | None,
) -> str:
    """episodic 内容确定性模板（纯函数，无 IO），Consolidator 可解析。"""
    lines: list[str] = [f"需求：{init_request}"]
    header = f"计划 v{plan_version}：《{plan.title}》"
    if plan.summary:
        header += f"— {plan.summary}"
    lines.append(header)

    answered: list[DecisionNode] = []
    steps: list[StepNode] = []
    for node in plan.nodes:
        if isinstance(node, DecisionNode):
            if node.answer is not None:
                answered.append(node)
        elif isinstance(node, StepNode):
            steps.append(node)

    if answered:
        lines.append("决策：")
        lines.extend(f"- {d.question} = {d.answer}" for d in answered)

    if steps:
        lines.append("步骤：")
        lines.extend(
            f"{i}. {s.title}（工具 {s.tool}）" for i, s in enumerate(steps, 1)
        )

    if run is not None and run.steps:
        lines.append("执行结果：")
        for i, r in enumerate(run.steps, 1):
            if r.ok:
                line = f"{i}. {r.step_id}：成功"
                if r.git_commit:
                    line += f"（commit {r.git_commit}）"
            else:
                line = f"{i}. {r.step_id}：失败"
            lines.append(line)

    return "\n".join(lines)


class LongTermMemory:
    """三层记忆写入口；embedding 失败由 MemoryService 上抛，调用方决定事务命运。"""

    def __init__(self, store: MemoryService) -> None:
        self._store = store

    async def record_episodic(
        self,
        *,
        session_id: UUID,
        init_request: str,
        plan_version: int,
        plan: PlanDocument,
        run: RunSummary | None,
    ) -> Memory:
        content = build_episodic_content(
            init_request=init_request,
            plan_version=plan_version,
            plan=plan,
            run=run,
        )
        return await self._store.store(
            kind="episodic",
            content=content,
            importance=0.5,
            source_session=session_id,
        )
