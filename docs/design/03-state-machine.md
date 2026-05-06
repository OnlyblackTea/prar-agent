# 03. 状态机 `core/state_machine.py` + 单元测试

## 目标

- **一句话**：把 PRAR 4 阶段循环（实际 6 个 phase）的转移合法性表落成纯 Python 模块，100% 行/分支覆盖。
- **验收标准**（缺一不可）：
  1. `cd backend && make test` 全绿（旧 9 + 本任务新增）
  2. `cd backend && make lint && make typecheck` 仍零警告/零错误
  3. `cd backend && make coverage-state-machine` 报告 `core/state_machine.py` 行覆盖与分支覆盖**均 100%**
  4. 通过 drift 测试：`Session` 模型的 CheckConstraint 字符串包含所有 6 个 phase enum value（catch Task 02 ↔ 03 漂移）

## 输入 / 输出

**前置任务**：
- Task 01（后端骨架）— ✅ 已完成
- Task 02（DB 模型，含 `Session.phase` String 列 + CheckConstraint）— ✅ 已完成

**交付物清单**：
- `src/app/core/__init__.py` (空标记)
- `src/app/core/state_machine.py`：`Phase` enum + `TRANSITIONS` 表 + 5 个公开函数 + 1 个自定义异常
- `tests/test_state_machine.py`：覆盖合法/非法/终态/可达性/drift 共 7 类测试
- `pyproject.toml` 增 `pytest-cov` dev 依赖
- `Makefile` 增 1 个 `coverage-state-machine` target

**不交付**（留给后续 task）：
- DB 持久化 phase（→ Task 02 已落 `Session.phase` 列；Task 07/18+ 写入）
- API 路由触发转移（→ Task 07 起的业务路由）
- LLM 无权决定 phase 切换（DESIGN.md §5 硬约束）
- 事件 / Pub-Sub 系统 → YAGNI，需要时再加
- 状态机持久化中间件 / DB 集成 → 状态机本模块**纯函数 + 纯数据**，无副作用

## 接口设计

### 目录结构（增量）

```
backend/
└── src/app/
    └── core/
        ├── __init__.py           # 新增（空）
        └── state_machine.py      # 新增
└── tests/
    └── test_state_machine.py     # 新增
└── pyproject.toml                # 修改（+pytest-cov dev dep）
└── Makefile                      # 修改（+1 coverage target）
```

### `core/state_machine.py` — 完整契约

```python
from enum import StrEnum


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
            f"Illegal transition: {from_phase.value} → {to_phase.value} "
            f"(allowed from {from_phase.value}: {allowed})"
        )
        self.from_phase = from_phase
        self.to_phase = to_phase


def can_transition(current: Phase, target: Phase) -> bool:
    """纯检查：current → target 是否合法。"""
    return target in TRANSITIONS[current]


def transition(current: Phase, target: Phase) -> Phase:
    """合法则返回 target，否则 raise InvalidTransitionError。"""
    if not can_transition(current, target):
        raise InvalidTransitionError(current, target)
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
```

### 转移矩阵可视化（reference）

| from \\ to | INIT | PLANNING | PLAN_REVIEW | ACTING | ACTION_REVIEW | DONE |
|------------|------|----------|-------------|--------|---------------|------|
| INIT       | ❌   | ✅       | ❌          | ❌     | ❌            | ❌   |
| PLANNING   | ❌   | ❌       | ✅          | ❌     | ❌            | ❌   |
| PLAN_REVIEW| ❌   | ✅       | ❌          | ✅     | ❌            | ❌   |
| ACTING     | ❌   | ❌       | ❌          | ❌     | ✅            | ❌   |
| ACTION_REVIEW| ❌ | ✅       | ❌          | ✅     | ❌            | ✅   |
| DONE       | ❌   | ❌       | ❌          | ❌     | ❌            | ❌   |

合法转移共 **8** 条；非法转移 **6×6 − 8 = 28** 条（含 6 个自环）。

### Drift 守护（catch Task 02 ↔ 03 不一致）

Task 02 的 `Session` 模型有：
```python
CheckConstraint(
    "phase IN ('init','planning','plan_review','acting','action_review','done')",
    name="phase_valid",
)
```

本任务测试模块加一条：从 `Session.__table__` 取 `ck_sessions_phase_valid` 约束的 `sqltext`，断言其字符串包含所有 6 个 `Phase` 值。Task 02 增删 phase 而忘改 enum、或反之，立刻红。

### `Makefile` 增量

| target | 命令 | 用途 |
|--------|------|------|
| `make coverage-state-machine` | `uv run pytest --cov=app.core.state_machine --cov-branch --cov-report=term-missing tests/test_state_machine.py` | 验证 state_machine.py 行+分支 100% 覆盖 |

### `pyproject.toml` dev 依赖增量

```toml
"pytest-cov>=5.0.0",
```

## 文件清单

| 路径 | 类型 | 说明 |
|------|------|------|
| `backend/src/app/core/__init__.py` | 新增 | 空，包标记 |
| `backend/src/app/core/state_machine.py` | 新增 | Phase enum + TRANSITIONS + 5 公开函数 + 1 异常 |
| `backend/tests/test_state_machine.py` | 新增 | 7 类共 ~12 用例（部分 parametrize） |
| `backend/pyproject.toml` | 修改 | dev 组 +pytest-cov |
| `backend/Makefile` | 修改 | +1 coverage-state-machine target |

5 文件改动（3 新增 + 2 修改）。

## 实施步骤

1. **建目录**：`mkdir -p backend/src/app/core`
2. **`pyproject.toml` 加 pytest-cov dev dep + `uv sync`**
3. **写 `core/__init__.py`** 与 `core/state_machine.py`
4. **写 `tests/test_state_machine.py`**（7 类测试，见下）
5. **`make test`** 全绿
6. **`make lint && make typecheck`** 零警告
7. **`make coverage-state-machine`**：100% 行 + 分支
8. **`Makefile` 加 coverage-state-machine target**

## 测试清单

### 用例分组（无需 DB）

| # | 测试 | 断言 |
|---|------|------|
| T1 | `test_phase_enum_has_six_members` | `set(Phase) == {INIT, PLANNING, PLAN_REVIEW, ACTING, ACTION_REVIEW, DONE}` |
| T2 | `test_phase_values_are_lowercase_snake_case` | 每个 `phase.value` 匹配 `^[a-z_]+$`（catch typo） |
| T3 | `test_transitions_table_covers_all_phases` | `set(TRANSITIONS.keys()) == set(Phase)` |
| T4 | `test_transitions_targets_are_valid_phases` | 所有 `TRANSITIONS.values()` 中的 phase 都在 `Phase` 内 |
| T5 (parametrize) | `test_legal_transitions_succeed[from→to]` | 参数化所有 8 条合法转移：`can_transition` True、`transition` 返回 target、不抛 |
| T6 (parametrize) | `test_illegal_transitions_raise[from→to]` | 参数化 28 条非法转移（含 6 个自环）：`can_transition` False、`transition` 抛 `InvalidTransitionError` |
| T7 | `test_invalid_transition_error_message_lists_allowed` | exception message 含原 phase / 目标 / sorted allowed list |
| T8 | `test_invalid_transition_error_attributes` | exception 的 `.from_phase` 与 `.to_phase` 属性正确 |
| T9 | `test_is_terminal` | `is_terminal(DONE) is True`；其余 5 个均 False |
| T10 | `test_initial_phase_is_init` | `INITIAL_PHASE == Phase.INIT` |
| T11 | `test_terminal_phases_singleton_done` | `TERMINAL_PHASES == frozenset({Phase.DONE})` |
| T12 | `test_reachable_from_init_covers_all` | `reachable_phases(Phase.INIT) == set(Phase)`（无孤儿/不可达） |
| T13 | `test_reachable_from_done_is_singleton` | `reachable_phases(Phase.DONE) == {Phase.DONE}` |
| T14 | `test_reachable_from_acting_excludes_init` | `Phase.INIT not in reachable_phases(Phase.ACTING)` |
| T15 | `test_db_check_constraint_matches_enum` | drift 守护：从 `Session.__table__` 取 `ck_sessions_phase_valid` 的 sqltext，断言含所有 `phase.value` |

### 边缘情况

- DONE → 任何（含 DONE 自身）：T6 含
- INIT → INIT 自环：T6 含
- 全 6 个自环禁止：T6 含
- 不可达 phase：T12/T14 直接证明

### 集成测试入口

```bash
cd backend && make test                       # 跑全部
cd backend && make coverage-state-machine     # 验证 100%
```

## 风险与未决

### 已识别风险

| 风险 | 缓解 |
|------|------|
| 未来需要中途插入新 phase | 6 个 phase 已通过 ROADMAP/DESIGN 冻结；新增需 update DESIGN + Task 02 migration + 本 task 同步，drift 测试会拦 |
| 自环禁止可能让"重新生成 plan"等需求别扭 | 设计里 PLANNING → PLAN_REVIEW → PLANNING 已表达"重做"语义；不需要 PLANNING → PLANNING 自环 |
| 并发：两个请求同时触发 transition | 本模块**无状态**，纯函数；并发控制属于 caller (DB 行锁 / OCC，由 Task 03 之外处理) |
| 测试里把 Phase 枚举当 str 用 | StrEnum 自带 str 兼容；`Phase.INIT == "init"` 为 True；测试不依赖此特性以避免脆弱断言 |

### 已决策（默认值，主人不反对就这么走）

| # | 项目 | 决策 | 反对就告诉我 |
|---|------|------|-------------|
| Q1 | Enum 类型 | **`StrEnum`**（Python 3.11+；序列化天然友好） | "用普通 Enum" |
| Q2 | TRANSITIONS values 类型 | **`frozenset[Phase]`**（不可变 + 快 in 检查） | "用 list" / "用 set" |
| Q3 | 自环（X → X）允许吗 | **全部禁止**（含 DONE → DONE） | "允许某些自环" |
| Q4 | 加 `pytest-cov` 验 100% | **是**（CLAUDE.md 要求关键模块 100%） | "不加，靠 review" |
| Q5 | Phase enum 位置 | **`app/core/state_machine.py`**（不另开 `app/types/`） | "另开 app/types/ 或 app/domain/" |
| Q6 | 加 drift 测试（DB CHECK ↔ enum 同步） | **是**（catch 默认 silent failure 类的 bug） | "不加" |
| Q7 | event-driven API（如 `submit_init_request()` 触发 INIT→PLANNING） | **不加，YAGNI**（仅暴露 `transition(from, to)`，业务层决定语义） | "要 event API" |

如以上 7 项主人无异议，请直接回复 `APPROVED`，我立即按本设计落代码并 commit。

## 已做的设计决策（记录依据）

| 决策 | 理由 |
|------|------|
| 状态机模块**无状态**（纯函数 + 纯数据） | 当前 phase 由 caller 持有（DB / 内存 dict），状态机只回答"这一步合法吗" |
| `Phase` 用 `StrEnum` 而非 `Enum` | 序列化到 DB / JSON 时直接 `phase.value`；与 DB CHECK 字符串契约对齐 |
| `TRANSITIONS` values 用 `frozenset` | 防止意外修改；O(1) `in` 查询；hash 友好 |
| `InvalidTransitionError` 含 `from_phase` / `to_phase` 属性 | caller catch 后能精确回报，不只 log message |
| 提供 `reachable_phases()` | 测试可达性（catch 不可达 phase 写错）；未来 UI 可据此画状态图 |
| 不提供 `transition_with_callback()` 之类副作用 hook | YAGNI，加副作用就要管线程安全/事务，违背"无状态" |
| `Phase.DONE` 在 `TRANSITIONS` 里值为 `frozenset()`（空集而非 raise） | 让 `can_transition(DONE, X)` 返回 False 而不抛；调用代码更简洁 |
| 不在状态机里 import `app.db.models` | 反向依赖（db→core 才对）；drift 测试在 tests/ 层做（属于"应用层"集成验证） |

---

**Q1-Q7 已锁定默认，主人审阅整体方案，回 `APPROVED` 即开始落代码 + 跑测试 + commit（设计与代码同 commit）。**
