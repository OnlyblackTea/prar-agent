"""Review Merger：comments + plan vN → plan v{N+1} 的 LLM 编排。"""
import json
from uuid import UUID

from app.core.logging import get_logger
from app.core.merger_schemas import MergerResult
from app.core.plan_engine import _apply_critic, _load_prompt
from app.core.plan_schemas import CriticResult, PlanDocument
from app.db.models import Comment
from app.llm.router import LLMRouter
from app.llm.types import ResolvedAdapter

_log = get_logger("review_merger")


class ReviewMerger:
    """评论合并器。复用 PlanEngine 的 _apply_critic（内含 _assign_ids）。"""

    def __init__(self, router: LLMRouter) -> None:
        self._router = router
        self._prompt = _load_prompt("merger.md")

    async def merge(
        self,
        *,
        plan: PlanDocument,
        comments: list[Comment],
        adapter: ResolvedAdapter,
    ) -> tuple[PlanDocument, MergerResult]:
        """调 LLM 综合评论，返回 (新 plan, merger 决策记录)。

        comments 为空时返回 (plan, MergerResult(actions=[]))，不调 LLM。
        """
        if not comments:
            return plan, MergerResult()

        plan_json = plan.model_dump_json(indent=2)
        comments_json = _comments_to_prompt_json(comments)
        user_prompt = self._prompt.format(
            plan_json=plan_json,
            comments_json=comments_json,
        )
        response = await self._router.complete_structured(
            adapter=adapter,
            system="你是一个计划修订专家。",
            user=user_prompt,
            schema=MergerResult,
        )
        merger_result: MergerResult = response.parsed

        # 提取所有有效 patch，构造 CriticResult 复用 _apply_critic
        patches = [a.patch for a in merger_result.actions if a.patch is not None]
        if not patches:
            return plan, merger_result

        critic_result = CriticResult(actions=patches, overall_comment="")
        new_plan = _apply_critic(plan, critic_result)
        return new_plan, merger_result


def _comments_to_prompt_json(comments: list[Comment]) -> str:
    """把 Comment ORM 列表 dump 成 JSON 字符串给 LLM。

    只保留 LLM 需要的字段，去掉 session_id / plan_version / resolved 这些内部字段。
    """
    payload: list[dict[str, str | UUID]] = [
        {
            "comment_id": str(c.id),
            "anchor_id": c.anchor_id,
            "quote": c.quote,
            "quote_context": c.quote_context,
            "body": c.body,
        }
        for c in comments
    ]
    return json.dumps(payload, ensure_ascii=False, indent=2)
