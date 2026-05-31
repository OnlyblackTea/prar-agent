"""把 PlanDocument 序列化为 WS 事件序列。"""

import asyncio
from collections.abc import AsyncIterator

from app.core.plan_schemas import PlanDocument

CHUNK_DELAY_MS = 50


async def stream_plan(plan: PlanDocument, session_id: str) -> AsyncIterator[dict]:
    """把 PlanDocument 拆成事件序列：plan.start → plan.node × N → plan.done。"""
    yield {
        "type": "plan.start",
        "session_id": session_id,
        "title": plan.title,
        "summary": plan.summary,
    }
    for i, node in enumerate(plan.nodes):
        if i > 0:
            await asyncio.sleep(CHUNK_DELAY_MS / 1000)
        yield {
            "type": "plan.node",
            "index": i,
            "node": node.model_dump(),
        }
    yield {"type": "plan.done", "total_nodes": len(plan.nodes)}
