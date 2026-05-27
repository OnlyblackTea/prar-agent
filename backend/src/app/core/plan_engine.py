"""Plan 引擎：Planner → assign_ids → Critic → apply_critic."""

from pathlib import Path

from app.core.logging import get_logger
from app.core.plan_schemas import CriticResult, PlanDocument, PlanNode
from app.llm.router import LLMRouter
from app.llm.types import ResolvedAdapter

_log = get_logger("plan_engine")

_DEFAULT_TOOLS = ["shell", "fs.read", "fs.write"]


# ===== 内部工具函数 =====


def _load_prompt(filename: str) -> str:
    """从 llm/prompts/ 加载 prompt 模板文件。"""
    prompts_dir = Path(__file__).resolve().parent.parent / "llm" / "prompts"
    target = (prompts_dir / filename).resolve()
    if not target.is_relative_to(prompts_dir):
        raise ValueError(f"Invalid prompt filename: {filename}")
    return target.read_text(encoding="utf-8")


def _assign_ids(plan: PlanDocument) -> PlanDocument:
    """框架后处理：为 decision/glossary/step 分配确定性 ID。"""
    counters: dict[str, int] = {"decision": 0, "glossary": 0, "step": 0}
    prefixes = {"decision": "dec", "glossary": "gls", "step": "step"}
    new_nodes: list[PlanNode] = []
    for node in plan.nodes:
        if node.type in counters:
            counters[node.type] += 1
            updated = node.model_copy(
                update={"id": f"{prefixes[node.type]}_{counters[node.type]:03d}"}
            )
            new_nodes.append(updated)
        else:
            new_nodes.append(node)
    return plan.model_copy(update={"nodes": new_nodes})


def _apply_critic(plan: PlanDocument, critic: CriticResult) -> PlanDocument:
    """应用 Critic 修正：按 node_index 倒序处理避免下标偏移。"""
    nodes = list(plan.nodes)
    for action in sorted(critic.actions, key=lambda a: a.node_index, reverse=True):
        idx = action.node_index
        if idx >= len(nodes):
            _log.warning(
                "Critic action skipped: node_index %d out of bounds (len=%d)",
                idx,
                len(nodes),
            )
            continue
        if action.action == "remove":
            nodes.pop(idx)
        elif action.action == "replace" and action.replacement is not None:
            nodes[idx] = action.replacement
        elif action.action == "insert_after" and action.replacement is not None:
            nodes.insert(idx + 1, action.replacement)
        else:
            _log.warning("Critic action skipped: unknown action %r", action.action)
    # 重新分配 ID（critic 可能改变了节点顺序/数量）
    result = plan.model_copy(update={"nodes": nodes})
    return _assign_ids(result)


# ===== PlanEngine 类 =====


class PlanEngine:
    def __init__(self, router: LLMRouter) -> None:
        self._router = router
        self._planner_prompt = _load_prompt("planner.md")
        self._critic_prompt = _load_prompt("critic.md")

    async def _call_planner(
        self,
        *,
        init_request: str,
        adapter: ResolvedAdapter,
        ltm_recall: list[str],
        available_tools: list[str],
    ) -> PlanDocument:
        user_prompt = self._planner_prompt.format(
            init_request=init_request,
            ltm_recall="\n".join(ltm_recall) if ltm_recall else "无",
            available_tools=", ".join(available_tools),
        )
        response = await self._router.complete_structured(
            adapter=adapter,
            system="你是一个严谨的项目规划师。",
            user=user_prompt,
            schema=PlanDocument,
        )
        return response.parsed

    async def _call_critic(
        self,
        *,
        plan: PlanDocument,
        adapter: ResolvedAdapter,
    ) -> CriticResult:
        plan_json = plan.model_dump_json(indent=2)
        user_prompt = self._critic_prompt.format(plan_json=plan_json)
        response = await self._router.complete_structured(
            adapter=adapter,
            system="你是一个计划评审专家。",
            user=user_prompt,
            schema=CriticResult,
        )
        return response.parsed

    async def generate(
        self,
        *,
        init_request: str,
        adapter: ResolvedAdapter,
        ltm_recall: list[str] | None = None,
        available_tools: list[str] | None = None,
    ) -> PlanDocument:
        """生成 Plan：Planner → assign_ids → Critic → apply_critic。"""
        # 1. Planner LLM call
        plan = await self._call_planner(
            init_request=init_request,
            adapter=adapter,
            ltm_recall=ltm_recall or [],
            available_tools=available_tools or _DEFAULT_TOOLS,
        )

        # 2. 框架后处理：分配节点 ID
        plan = _assign_ids(plan)

        # 3. Critic LLM call
        critic_result = await self._call_critic(plan=plan, adapter=adapter)

        # 4. 合并修正
        plan = _apply_critic(plan, critic_result)

        return plan
