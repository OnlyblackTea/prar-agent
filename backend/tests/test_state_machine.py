import re

import pytest
from sqlalchemy import CheckConstraint, Table

from app.core.state_machine import (
    INITIAL_PHASE,
    TERMINAL_PHASES,
    TRANSITIONS,
    InvalidTransitionError,
    Phase,
    can_transition,
    is_terminal,
    reachable_phases,
    transition,
)
from app.db.models import Session

# ===== 基础 enum 与表结构 =====


def test_phase_enum_has_six_members() -> None:
    assert set(Phase) == {
        Phase.INIT,
        Phase.PLANNING,
        Phase.PLAN_REVIEW,
        Phase.ACTING,
        Phase.ACTION_REVIEW,
        Phase.DONE,
    }


def test_phase_values_are_lowercase_snake_case() -> None:
    pat = re.compile(r"^[a-z][a-z_]*$")
    for p in Phase:
        assert pat.match(p.value), f"phase value {p.value!r} 违反 snake_case"


def test_transitions_table_covers_all_phases() -> None:
    assert set(TRANSITIONS.keys()) == set(Phase)


def test_transitions_targets_are_valid_phases() -> None:
    for targets in TRANSITIONS.values():
        for t in targets:
            assert t in Phase


# ===== 合法/非法转移（参数化）=====

LEGAL_TRANSITIONS = [
    (frm, to) for frm, targets in TRANSITIONS.items() for to in targets
]

ILLEGAL_TRANSITIONS = [
    (frm, to) for frm in Phase for to in Phase if to not in TRANSITIONS[frm]
]


def test_legal_transitions_count_is_eight() -> None:
    """选项 A：含 ACTION_REVIEW -> PLANNING escape 的合法转移共 8 条。"""
    assert len(LEGAL_TRANSITIONS) == 8


def test_illegal_transitions_count_is_twenty_eight() -> None:
    """6x6 - 8 = 28，含 6 个自环。"""
    assert len(ILLEGAL_TRANSITIONS) == 28
    self_loops = [(frm, to) for frm, to in ILLEGAL_TRANSITIONS if frm is to]
    assert len(self_loops) == 6


@pytest.mark.parametrize(("frm", "to"), LEGAL_TRANSITIONS)
def test_legal_transitions_succeed(frm: Phase, to: Phase) -> None:
    assert can_transition(frm, to) is True
    assert transition(frm, to) is to


@pytest.mark.parametrize(("frm", "to"), ILLEGAL_TRANSITIONS)
def test_illegal_transitions_raise(frm: Phase, to: Phase) -> None:
    assert can_transition(frm, to) is False
    with pytest.raises(InvalidTransitionError):
        transition(frm, to)


# ===== 异常细节 =====


def test_invalid_transition_error_message_lists_allowed() -> None:
    with pytest.raises(InvalidTransitionError) as exc_info:
        transition(Phase.INIT, Phase.DONE)
    msg = str(exc_info.value)
    assert "init" in msg
    assert "done" in msg
    assert "['planning']" in msg


def test_invalid_transition_error_attributes() -> None:
    with pytest.raises(InvalidTransitionError) as exc_info:
        transition(Phase.PLANNING, Phase.ACTING)
    err = exc_info.value
    assert err.from_phase is Phase.PLANNING
    assert err.to_phase is Phase.ACTING


# ===== 终态 / 初态 =====


def test_is_terminal() -> None:
    assert is_terminal(Phase.DONE) is True
    for p in (
        Phase.INIT,
        Phase.PLANNING,
        Phase.PLAN_REVIEW,
        Phase.ACTING,
        Phase.ACTION_REVIEW,
    ):
        assert is_terminal(p) is False


def test_initial_phase_is_init() -> None:
    assert INITIAL_PHASE is Phase.INIT


def test_terminal_phases_singleton_done() -> None:
    assert TERMINAL_PHASES == frozenset({Phase.DONE})


# ===== 可达性 =====


def test_reachable_from_init_covers_all() -> None:
    """INIT 出发应能走到所有 6 个 phase（无孤儿/不可达）。"""
    assert reachable_phases(Phase.INIT) == set(Phase)


def test_reachable_from_done_is_singleton() -> None:
    assert reachable_phases(Phase.DONE) == {Phase.DONE}


def test_reachable_from_acting_excludes_init() -> None:
    """ACTING 之后不可能回到 INIT（INIT 只在新 session 时存在）。"""
    assert Phase.INIT not in reachable_phases(Phase.ACTING)


# ===== Drift 守护：DB CHECK ↔ enum 同步 =====


def test_db_check_constraint_matches_enum() -> None:
    """Task 02 中 Session.phase 的 CheckConstraint 字符串必须含所有 Phase value。

    任一侧增删 phase 而忘改另一侧，本测试立刻红——避免 silent failure。
    """
    table = Session.__table__
    assert isinstance(table, Table)
    check_constraints = [
        c for c in table.constraints if isinstance(c, CheckConstraint)
    ]
    phase_check = next(
        (c for c in check_constraints if c.name == "ck_sessions_phase_valid"),
        None,
    )
    assert phase_check is not None, "Session 表应有 ck_sessions_phase_valid 约束"
    sql = str(phase_check.sqltext)
    for p in Phase:
        assert f"'{p.value}'" in sql, (
            f"DB CheckConstraint 缺少 phase value {p.value!r}—— 与 Phase enum 漂移"
        )
