from enum import StrEnum

from app.core.logging import get_logger

_log = get_logger("state_machine")


class Phase(StrEnum):
    """PRAR 工作流的 6 个 phase。值与 DB Session.phase CheckConstraint 必须一致。"""

    INIT = "init"
    PLANNING = "planning"
    PLAN_REVIEW = "plan_review"
    ACTING = "acting"
    ACTION_REVIEW = "action_review"
    DONE = "done"


# 转移合法性表（硬编码，LLM 无权决定阶段切换）
TRANSITIONS: dict[Phase, frozenset[Phase]] = {
    Phase.INIT:          frozenset({Phase.PLANNING}),
    Phase.PLANNING:      frozenset({Phase.PLAN_REVIEW}),
    Phase.PLAN_REVIEW:   frozenset({Phase.PLANNING, Phase.ACTING}),
    Phase.ACTING:        frozenset({Phase.ACTION_REVIEW}),
    Phase.ACTION_REVIEW: frozenset({Phase.ACTING, Phase.PLANNING, Phase.DONE}),
    Phase.DONE:          frozenset(),  # terminal
}

INITIAL_PHASE: Phase = Phase.INIT
TERMINAL_PHASES: frozenset[Phase] = frozenset({Phase.DONE})


class InvalidTransitionError(Exception):
    """非法 phase 转移；message 含原 phase / 目标 phase / 当前合法选项。"""

    def __init__(self, from_phase: Phase, to_phase: Phase) -> None:
        allowed = sorted(p.value for p in TRANSITIONS[from_phase])
        super().__init__(
            f"Illegal transition: {from_phase.value} -> {to_phase.value} "
            f"(allowed from {from_phase.value}: {allowed})"
        )
        self.from_phase = from_phase
        self.to_phase = to_phase


def can_transition(current: Phase, target: Phase) -> bool:
    """纯检查：current -> target 是否合法。"""
    return target in TRANSITIONS[current]


def transition(current: Phase, target: Phase, *, session_id: str | None = None) -> Phase:
    """合法则返回 target，否则 raise InvalidTransitionError。"""
    if not can_transition(current, target):
        _log.warning(
            "transition_rejected",
            from_phase=current.value,
            to_phase=target.value,
            session_id=session_id,
        )
        raise InvalidTransitionError(current, target)
    _log.info(
        "transition",
        from_phase=current.value,
        to_phase=target.value,
        session_id=session_id,
    )
    return target


def is_terminal(phase: Phase) -> bool:
    return phase in TERMINAL_PHASES


def reachable_phases(start: Phase) -> set[Phase]:
    """从 start 出发，沿合法转移可达的所有 phase（含 start 本身）。"""
    seen: set[Phase] = {start}
    stack: list[Phase] = [start]
    while stack:
        p = stack.pop()
        for nxt in TRANSITIONS[p]:
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen
