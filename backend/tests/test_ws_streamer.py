"""ws_streamer 单元测试。"""

from app.core.plan_schemas import (
    GlossaryNode,
    HeadingNode,
    ParagraphNode,
    PlanDocument,
    StepNode,
)
from app.core.ws_streamer import stream_plan


async def _collect(plan: PlanDocument, session_id: str = "sid-1") -> list[dict]:
    events: list[dict] = []
    async for event in stream_plan(plan, session_id):
        events.append(event)
    return events


# ===== T1: 事件序列首尾 =====


async def test_stream_plan_emits_start_nodes_done() -> None:
    plan = PlanDocument(
        title="T",
        summary="S",
        nodes=[
            HeadingNode(level=1, text="H"),
            StepNode(title="s1", description="d1", tool="shell"),
        ],
    )
    events = await _collect(plan)
    assert events[0]["type"] == "plan.start"
    assert events[-1]["type"] == "plan.done"
    assert all(e["type"] == "plan.node" for e in events[1:-1])


# ===== T2: node index 递增 =====


async def test_stream_plan_node_index_sequential() -> None:
    plan = PlanDocument(
        title="T",
        summary="S",
        nodes=[
            ParagraphNode(text="a"),
            ParagraphNode(text="b"),
            ParagraphNode(text="c"),
        ],
    )
    events = await _collect(plan)
    node_events = [e for e in events if e["type"] == "plan.node"]
    assert [e["index"] for e in node_events] == [0, 1, 2]


# ===== T3: 空 nodes 仍发 start + done =====


async def test_stream_plan_empty_nodes() -> None:
    plan = PlanDocument(title="T", summary="S", nodes=[])
    events = await _collect(plan)
    assert len(events) == 2
    assert events[0]["type"] == "plan.start"
    assert events[1]["type"] == "plan.done"
    assert events[1]["total_nodes"] == 0


# ===== T4: node 内容与输入一致 =====


async def test_stream_plan_node_content_matches_input() -> None:
    nodes = [
        HeadingNode(level=2, text="H2"),
        GlossaryNode(term="API", definition="Application Programming Interface"),
    ]
    plan = PlanDocument(title="T", summary="S", nodes=nodes)
    events = await _collect(plan)
    node_events = [e for e in events if e["type"] == "plan.node"]
    for i, ne in enumerate(node_events):
        assert ne["node"] == nodes[i].model_dump()
