# 23. 长期记忆三层（memory/long_term.py）+ session DONE 时 episodic 写入

## 目标

- 一句话目标：交付长期记忆三层模块 `app/memory/long_term.py`，并打通 session 完成（ACTION_REVIEW → DONE）时自动写 episodic 记忆的完整链路。
- 验收标准：
  1. `POST /api/sessions/{session_id}/complete` 使 `action_review` session 转 `done`，同事务落 1 条 `kind="episodic"` 记忆（`source_session` 关联，embedding 由 M3-22 服务生成）。
  2. episodic 内容为确定性模板（零 LLM 调用）：含需求、plan 标题/摘要、已答决策、步骤清单与执行结果（ok/fail + git commit）。
  3. 非法 phase → 409；embedding 失败 → 502 且 session **不**转 done（可重试，零部分写入）。
  4. 三绿（pytest + ruff + mypy）+ 真实 VM DB 验证 complete 链路。

## 输入 / 输出

- 上游产物：
  - M3-22（任务 22）：`MemoryService`（store/search）+ `EmbeddingService` + memories 表（kind 约束已含三层值）。
  - M3-18/19/20（任务 18/19/20）：`ActionDispatcher.execute_plan` 返回 `StepExecution` 记录（含 ok / git_commit）；`ws_act` 完成 acting 后把 session 置 `action_review`。
  - M1-03（任务 03）：状态机 `ACTION_REVIEW → DONE` 转移已合法，但**无任何代码路径触发**（本任务补上）。
- 本任务交付物清单：
  1. `app/memory/long_term.py`：三层语义 + episodic 写路径（semantic/procedural 写路径留给任务 24 Consolidator，不在本任务写死代码）。
  2. `SessionService.complete()`：DONE 转移 + episodic 写入编排（单事务）。
  3. `POST /api/sessions/{session_id}/complete` 端点。
  4. `ws_act` 在 acting 结束时把 run 摘要持久化到 `session.metadata_json["last_run"]`（episodic 内容的执行结果数据源）。

## 接口设计

### 三层语义（概念契约）

| 层 | 含义 | 写入方 | 读取方 |
|---|---|---|---|
| episodic | 单次会话的经历：需求、决策、步骤与结果 | 本任务：session DONE 钩子 | 任务 24 Consolidator 原料；任务 25 LTM recall |
| semantic | 跨会话提炼的知识/结论 | 任务 24 Consolidator | 任务 25 LTM recall |
| procedural | 可复用的方法/流程 | 任务 24 Consolidator | 任务 25 LTM recall |

三层共用 memories 表，以 `kind` 区分（M3-22 已建约束与 HNSW 索引）。本任务只实现 episodic 写路径；semantic/procedural 不写空壳方法（禁止"将来可能用到"的扩展点）。

### 新模块 `app/memory/long_term.py`

```python
@dataclass(frozen=True, slots=True)
class StepOutcome:
    step_id: str
    ok: bool
    git_commit: str | None = None

@dataclass(frozen=True, slots=True)
class RunSummary:
    plan_version: int
    all_ok: bool
    steps: list[StepOutcome]

def build_episodic_content(
    *,
    init_request: str,
    plan_version: int,
    plan: PlanDocument,
    run: RunSummary | None,
) -> str
# 确定性模板，纯函数（无 IO），Consolidator 可解析。

class LongTermMemory:
    def __init__(self, store: MemoryService) -> None: ...

    async def record_episodic(
        self,
        *,
        session_id: UUID,
        init_request: str,
        plan_version: int,
        plan: PlanDocument,
        run: RunSummary | None,
    ) -> Memory:
        # content = build_episodic_content(...)
        # return await self._store.store(kind="episodic", content=content,
        #                                importance=0.5, source_session=session_id)
```

episodic 内容模板（D2）：

```
需求：{init_request}
计划 v{plan_version}：《{plan.title}》— {plan.summary}
决策：
- {question} = {answer}          # 仅已答决策；全未答则整段省略
步骤：
1. {title}（工具 {tool}）
执行结果：                        # run is None 则整段省略
1. {step_id}：成功（commit {git_commit}）   # ok=True
2. {step_id}：失败                          # ok=False
```

### SessionService 扩展

```python
async def complete(
    self, *, session_id: UUID, long_term: LongTermMemory,
) -> models.Session:
    # 1. get(session_id) → SessionNotFoundError
    # 2. phase != action_review → transition() 抛 InvalidTransitionError（含 done 二次调用）
    # 3. get_current_plan → 无 plan 抛 ValueError("plan_not_found")
    # 4. run = 解析 s.metadata_json.get("last_run")（无则 None，容忍旧数据）
    # 5. await long_term.record_episodic(...)   # EmbeddingError 上抛，未落任何行
    # 6. transition(action_review, done) → s.phase = done → flush（commit 由 get_db 收尾）
```

事务语义（D4）：memory 行插入与 phase 切换在同一 DB 事务；embedding 在插入前完成，失败即抛（上抛 EmbeddingError 系），无部分写入、session 保持 action_review 可重试。**不静默 catch**。

### API 端点

```
POST /api/sessions/{session_id}/complete
→ 200 SessionResponse（复用现有模型，无新 shared schema）
错误映射：
  404 session_not_found          (SessionNotFoundError)
  409 illegal_phase_transition   (InvalidTransitionError)
  404 plan_not_found             (ValueError "plan_not_found")
  502 embedding_failed           (EmbeddingTransportError)
  502 embedding_dimension_mismatch (EmbeddingDimensionError)
```

依赖注入（D6，沿用 merge_plan 的 router 参数模式）：

```python
async def get_long_term(db: AsyncSession = Depends(get_db)) -> LongTermMemory:
    return LongTermMemory(MemoryService(db, get_embedding_service()))
```

### ws_act 变更（D3）

acting 结束（phase 切 action_review 前）持久化 run 摘要：

```python
session.metadata_json = {
    **session.metadata_json,
    "last_run": {
        "plan_version": plan_row.version,
        "all_ok": all(r.ok for r in records),
        "steps": [
            {"step_id": r.step_id, "ok": r.ok, "git_commit": r.git_commit}
            for r in records
        ],
    },
}
```

理由：`StepExecution` 目前不落库（WS 关闭即失），episodic 记忆与任务 26/27（rerun）都需要 step 级 ok/fail + commit 定位。合并语义（不覆盖 metadata_json 其他键）。

### 数据流

```
[acting 完成] ws_act: metadata_json["last_run"] = 摘要 ─┐
[用户点"完成"] POST /complete                           │
  → SessionService.complete                            │
      ├─ 校验 phase=action_review                      │
      ├─ 拉 current plan                               │
      ├─ build_episodic_content（纯函数，模板）          │
      ├─ record_episodic → MemoryService.store          │
      │     └─ EmbeddingService.embed_one（真调用）      │
      ├─ transition(ACTION_REVIEW, DONE)               │
      └─ phase=done（同事务 commit）                    │
```

## 文件清单

**新增**

| 文件 | 作用 |
|---|---|
| `docs/design/23-long-term-memory.md` | 本设计 |
| `backend/src/app/memory/__init__.py` | 包声明，导出 LongTermMemory |
| `backend/src/app/memory/long_term.py` | StepOutcome / RunSummary / build_episodic_content / LongTermMemory |
| `backend/tests/test_long_term.py` | 内容模板纯函数 + record_episodic 单测（mock MemoryService） |
| `backend/tests/test_session_complete_service.py` | complete() 服务层测试（真 DB + fake LongTermMemory） |
| `backend/tests/test_sessions_complete_api.py` | complete 端点 API 测试（TestClient + overrides） |

**修改**

| 文件 | 变更 |
|---|---|
| `backend/src/app/services/session_service.py` | + `complete()` |
| `backend/src/app/api/sessions.py` | + complete 端点 + `get_long_term` 依赖 + 异常映射 |
| `backend/src/app/api/ws_act.py` | acting 结束持久化 `last_run` 摘要（~6 行） |
| `backend/tests/test_ws_act.py` | + 断言 `last_run` 落库 |

无 DB migration（memories 表 M3-22 已就绪）；无 shared/schema.json 变化（复用 SessionResponse）。

## 实施步骤

1. 写设计文档（本文件）。
2. TDD 红：先写 3 个新测试文件 + ws_act 扩展断言，跑收集期确认红灯（ModuleNotFoundError / 断言失败）。
3. 实现 `app/memory/long_term.py`（builder + record_episodic）。
4. 实现 `SessionService.complete` + API 端点 + `get_long_term` 依赖。
5. 改 `ws_act` 持久化 `last_run`。
6. Windows 三绿：`uv run pytest -m "not smoke"` + `uv run ruff check .` + `uv run mypy src`（每步独立验证）。
7. 真实验证：VM DB（192.168.1.147）跑 complete 全链路脚本（真实 embedding → 真实 memories 行 + phase done），跑完清理。
8. 实施记录 + 单 commit（GPG 签名）。

## 测试清单

**test_long_term.py**（单元，100% mock）
- L1 builder：全字段（已答+未答决策混排 → 只含已答）；run=None → 无"执行结果"段；无决策/无步骤的极简 plan 不产出空段。
- L2 builder：成功 step 带 commit hash、失败 step 无 commit。
- L3 record_episodic：断言 store 收到 kind="episodic"、content 与 builder 输出一致、source_session=session_id、importance=0.5。
- L4 record_episodic：store 抛 EmbeddingTransportError → 原样上抛（不 catch）。

**test_session_complete_service.py**（真 DB + fake LongTermMemory，rollback 隔离）
- C1 正常：action_review session → complete 后 phase="done"；record_episodic 被 await 一次且收到 session_id/init_request/plan_version/plan。
- C2 metadata_json 有 last_run → run 正确解析传入。
- C3 非法 phase（acting）→ InvalidTransitionError。
- C4 二次 complete（已 done）→ InvalidTransitionError。
- C5 session 不存在 → SessionNotFoundError。
- C6 record_episodic 抛 EmbeddingTransportError → 上抛，且 session.phase 仍为 action_review（回滚后验证）。

**test_sessions_complete_api.py**（TestClient + dependency_overrides）
- A1 200：响应 phase="done"。
- A2 409 illegal_phase_transition。
- A3 404 session_not_found。
- A4 502 embedding_failed。
- A5 502 embedding_dimension_mismatch。

**test_ws_act.py 扩展**
- W1 acting 成功后 `metadata_json["last_run"]` 含 plan_version / all_ok / steps（ok + git_commit）。

## 风险与未决

| 风险 | 对策 |
|---|---|
| episodic 模板质量决定任务 25 LTM recall 效果 | 模板含决策答案 + 步骤结果，语义密度足够；任务 25 可调 recall 检索参数，不回溯改模板格式 |
| complete 时 plan 缺失（异常路径） | 防御性 ValueError("plan_not_found") → 404 |
| last_run 解析失败（旧数据/脏数据） | 解析宽容：缺失/类型不符 → run=None，episodic 仍写入（不含执行结果段） |
| 单用户 MVP 无 user_id | 沿用 M3-22：user_id=None |
| embedding 端点挂 → complete 全链路不可用 | 502 + session 保持 action_review 可重试；日志走 embedding logger（M3-22 已建） |

## 实施记录

### 2026-09-03 实施完成

- **TDD 红**：3 个新测试文件（test_long_term / test_session_complete_service / test_sessions_complete_api）+ test_ws_act 扩展先写后绿，收集期报 `ModuleNotFoundError: app.memory.long_term` 确认红灯。
- **实现**：`memory/long_term.py`（StepOutcome / RunSummary / build_episodic_content 纯函数 / LongTermMemory.record_episodic，零 LLM 调用）、`SessionService.complete()`（transition 门禁 → 拉 plan → 解析 last_run → record_episodic → 切 phase，同事务）、`_parse_run_summary`（宽容解析旧数据）、`POST /api/sessions/{id}/complete` + `get_long_term` 依赖 + 5 类异常映射、ws_act acting 结束持久化 `metadata_json["last_run"]`。
- **三绿**：pytest 335 passed / 4 skipped / 1 deselected（smoke）；ruff 全绿；mypy 52 个源码文件零错误。
- **W292 排查**：ruff 报 `sandbox/runs/31cbd24d.../hello.py` 缺尾换行——该目录是 M3-20/21 冒烟遗留的 checkpoint 仓库（内嵌 `.git`），git 视其为 embedded repository，不套用根 `.gitignore`（`sandbox/runs/`），ruff 的 respect-gitignore 同样失效。按根因处理：删除该遗留冒烟产物目录（仅 .git 骨架 + hello.py），不做 ruff 配置豁免。
- **真实验证（VM DB 192.168.1.147 全链路）**：临时脚本 seed 一个 `action_review` session（含 last_run 摘要）+ Plan v2 → 走与端点完全相同的 `SessionService.complete(LongTermMemory(MemoryService(db, get_embedding_service())))` 代码路径（真实阿里云 Bailian embedding 真调用）→ 断言：session.phase="done"、memories 新增 1 行 kind="episodic"、content 与模板逐字节一致（需求/计划/已答决策/步骤/执行结果五段）、embedding 1536 维、importance=0.5、source_session 关联 → 清理 memories/plan/session 后行数归零。脚本按"任务步骤非交付物"约定跑完即删。
- **成本纪律**：episodic 写入零 LLM 调用（确定性模板），仅 1 次 embedding 真调用；semantic/procedural 未写空壳方法，留给任务 24 Consolidator。
- **无 DB migration**（memories 表 M3-22 已就绪）；**无 shared schema 变化**（复用 SessionResponse）。
