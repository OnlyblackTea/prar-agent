# 12. Review Merger (M2 评论 → 新 plan 版本)

> **状态**：DRAFT，待 APPROVED
> **依赖**：Task 11（Comment CRUD + AnchorMark 已落地）、Task 07（PlanEngine 的 `_apply_critic` / `_assign_ids` 工具函数复用）、Task 04.1（LLMRouter）
> **被依赖**：Task 13（diff 视图，读 plan\_version 1/2 对比）、Task 14（anchor 回源算法，跨版本评论位置）
> **commit 范围**：拆 2 个 commit（详见 §11）

***

## 0. M2 总体定位（提醒）

ROADMAP §41-50 M2 目标：**用户评论 → 真正改 plan → 看到 v1 → v2 diff**。Task 11 落地了"评论持久化"，**Task 12 是把评论变成新 plan 版本** —— M2 的核心增量。

| NN     | 状态     | 任务                                                   |
| ------ | ------ | ---------------------------------------------------- |
| 11     | ✅      | AnchorMark + CommentThread + Comment CRUD            |
| **12** | 🔵 本任务 | **Review Merger + `merger.md` prompt + POST /merge** |
| 13     | ⏳      | Plan 版本管理 + diff 视图                                  |
| 14     | ⏳      | Anchor 回源 + 悬空评论                                     |

***

## 1. 目标 — Task 12 一句话

打通 **点击"Apply Reviews"按钮 → 后端调 LLM 综合所有 unresolved comments + 当前 plan vN → 生成 plan vN+1 + 落库 + 标记 comments resolved → 前端切到 v2**。

### 1.1 验收 demo（人工 E2E）

1. Task 11 demo 跑完，session 在 `plan_review`，plan v1 + 3 条评论已存
2. 点击 `CommentThreadPanel` 顶部新增的 "Apply Reviews"
3. 等待（同步阻塞，\~5–15s LLM 调用）
4. 验收：

   * DB `plans` 表新增 v2 行（同 session\_id），document 与 v1 不同

   * `sessions.current_plan_version` = 2

   * 3 条评论中被 accept/partial 的 → `resolved=true`；reject 的 → `resolved=false`（仍可见）

   * 前端 `PlanDocEditor` 渲染 v2，`AnchorMark` 全部消失（v1 锚点对 v2 已无意义；Task 14 才解决跨版本回源）

   * 侧边栏 `CommentThreadPanel` 评论列表清空（v2 还没评论）

   * HTTP 响应 body 含 `MergerResult.actions`，每条带 `comment_id / decision / reason / patch`，前端 toast 或抽屉展示总评 + 每条决策
5. 回归：无 unresolved comments 时点 "Apply Reviews" → 400 `no_comments_to_merge`，按钮 disabled
6. 回归：phase ≠ `plan_review` → 409 `phase_not_review`

***

## 2. 现状缺口

| #  | 现状                                                                                                  | Task 12 要补                                                                |
| -- | --------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| P1 | `backend/src/app/llm/prompts/` 下只有 planner.md / critic.md / README.md（README 第 9 行已预留 merger.md 命名） | 新增 `merger.md`                                                            |
| P2 | 无 Merger Pydantic schemas                                                                           | 新增 `core/merger_schemas.py` (`MergerAction` / `MergerResult`)             |
| P3 | 无 ReviewMerger 类                                                                                    | 新增 `core/review_merger.py`，复用 `plan_engine._apply_critic` / `_assign_ids` |
| P4 | 无 merge 触发入口                                                                                        | 新增 `POST /api/sessions/{sid}/merge`                                       |
| P5 | `SessionService` 无 merge 编排                                                                         | 加 `merge_plan` 方法                                                         |
| P6 | `CommentService` 无批量标 resolved                                                                      | 加 `mark_resolved` 方法                                                      |
| P7 | 前端无"Apply Reviews"入口                                                                                | 加按钮 + handler + reducer action                                            |
| P8 | shared schema 未含 Merger 类型                                                                          | `backend/src/app/shared/schemas.py` + 重生成 `schema.json`                   |

### 2.1 输入 / 输出（WORKFLOW §2.2 汇总）

**上游产物（输入）**：

* Task 11：Comment CRUD + AnchorMark + `CommentService.list_by_version`

* Task 07：`plan_engine._apply_critic` / `_assign_ids` / `_load_prompt`、`CriticAction` / `CriticResult` schema

* Task 04.1：`LLMRouter.complete_structured` + `AdapterService`

* Task 08/10：`ws_plan.get_router` 单例约定、前端 reducer/fetch 模式

**本任务交付物（输出）**：下方 §2.2 文件清单全部落地 + 14 个新后端测试 + 1 个新前端测试全绿 + §1.1 人工 E2E 通过。

### 2.2 文件清单（WORKFLOW §2.2 汇总）

**新增**：

| 文件                                            | 作用                                     |
| --------------------------------------------- | -------------------------------------- |
| `backend/src/app/core/merger_schemas.py`      | `MergerAction` / `MergerResult` schema |
| `backend/src/app/core/review_merger.py`       | `ReviewMerger` LLM 编排                  |
| `backend/src/app/llm/prompts/merger.md`       | merger prompt 模板                       |
| `backend/tests/test_review_merger.py`         | unit \~5 cases（mock router）            |
| `backend/tests/test_session_merge_service.py` | service integration \~6 cases          |
| `backend/tests/test_merge_api.py`             | API integration \~3 cases              |
| `frontend/src/api/merge.ts`                   | `mergeReviews` fetch wrapper           |

**修改**：

| 文件                                               | 改动                                            |
| ------------------------------------------------ | --------------------------------------------- |
| `backend/src/app/services/session_service.py`    | 新增 `merge_plan`                               |
| `backend/src/app/services/comment_service.py`    | 新增 `list_unresolved` / `mark_resolved`        |
| `backend/src/app/api/sessions.py`                | 新增 `POST /{session_id}/merge`                 |
| `backend/src/app/shared/schemas.py`              | 注册 `MergerAction` / `MergerResult`            |
| `shared/schema.json`                             | `make gen-schema` 重生成                         |
| `frontend/src/state/sessionReducer.ts`（+test）    | `MERGE_COMPLETED` action                      |
| `frontend/src/components/CommentThreadPanel.tsx` | Apply Reviews 按钮 + resolved 灰显                |
| `frontend/src/App.tsx`                           | `handleApplyReviews` 装配                       |
| `frontend/src/App.css`                           | `.apply-reviews-btn` / `.comment-resolved` 样式 |

### 2.3 实施步骤（WORKFLOW §2.2 汇总，每步可独立验证）

1. **12a-1**：`merger_schemas.py` + `merger.md` prompt → import 通过
2. **12a-2**：`review_merger.py` + `test_review_merger.py` → 5 unit cases 绿
3. **12a-3**：`comment_service` 两方法 + `session_service.merge_plan` + `test_session_merge_service.py` → 6 cases 绿
4. **12a-4**：`POST /merge` endpoint + `test_merge_api.py` + shared schema 注册 + `make gen-schema` → 3 cases 绿
5. **12a-5**：`ruff check` + `pytest -m "not smoke"` 全量绿 → **commit 1**（含本设计文档）
6. **12b-1**：`api/merge.ts` + reducer `MERGE_COMPLETED` + test → vitest 绿
7. **12b-2**：`CommentThreadPanel` 按钮 + `App.tsx` 装配 + CSS
8. **12b-3**：`vitest run` + `tsc --noEmit` 全绿 → **commit 2**
9. **手工 E2E**：§1.1 主流程 + §10.3 全 reject 冒烟

***

## 3. 架构鸟瞰

```
┌────────────────────────────────────────────────────────────────────┐
│                         Browser (前端)                             │
│  ┌────────────────────────────────────────────────────────┐        │
│  │  CommentThreadPanel                                    │        │
│  │   ┌─────────────────────┐                              │        │
│  │   │ [Apply Reviews]  ◀──┼── disabled if no unresolved  │        │
│  │   └──────────┬──────────┘                              │        │
│  │              │ click                                   │        │
│  └──────────────┼─────────────────────────────────────────┘        │
└─────────────────┼──────────────────────────────────────────────────┘
                  │ POST /api/sessions/{sid}/merge
┌─────────────────▼──────────────────────────────────────────────────┐
│                         Backend (FastAPI)                          │
│  api/sessions.py POST /merge ─▶ SessionService.merge_plan          │
│                                       │                            │
│   ┌───────────────────────────────────┴────────────────────┐       │
│   │ 1. 校验 phase==plan_review                              │       │
│   │ 2. 拉 plan v{N} + unresolved comments(plan_version=N)  │       │
│   │ 3. 调 ReviewMerger.merge(plan, comments, adapter)      │       │
│   │      │  format(merger.md, plan_json, comments_json)    │       │
│   │      │  router.complete_structured(schema=MergerResult)│       │
│   │      ▼                                                 │       │
│   │    MergerResult.actions: [MergerAction × N]            │       │
│   │       (decision=accept/reject/partial, patch?)         │       │
│   │ 4. 提取 patch != None 的 CriticAction → _apply_critic  │       │
│   │ 5. _assign_ids → plan v{N+1}                           │       │
│   │ 6. INSERT plans(version=N+1, document=plan_v{N+1})     │       │
│   │ 7. UPDATE sessions.current_plan_version=N+1            │       │
│   │ 8. UPDATE comments.resolved=true                       │       │
│   │      WHERE id IN [accept ∪ partial 的 comment_ids]     │       │
│   └────────────────────────────────────────────────────────┘       │
│                                       │                            │
│                                       ▼                            │
│         { merger_result, new_plan_version: N+1 }                   │
└────────────────────────────────────────────────────────────────────┘
```

**Phase 转移**：**全程不动 prejhase**（保持 `plan_review`）。理由：merge 是 plan\_review 内部的"plan 版本演进"事件，不是状态机意义上的阶段转换。Phase 切换仍由用户显式操作（如 "advance to acting"）触发。已确认 [state\_machine.py:23](../../backend/src/app/core/state_machine.py#L23) 的 `PLAN_REVIEW → PLANNING` 转移本 task 不使用，留给用户主动"撤回到规划"操作（未来 task）。

***

## 4. 后端设计 (Task 12a)

### 4.1 Pydantic schemas — 新增 `app/core/merger_schemas.py`

```python
"""Review Merger LLM structured output schema。"""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.plan_schemas import CriticAction


class MergerAction(BaseModel):
    """对单条用户评论的处理结果。"""

    comment_id: UUID = Field(description="对应 comments.id")
    decision: Literal["accept", "reject", "partial"]
    reason: str = Field(min_length=1, description="一句话解释为何此决策")
    # decision == reject 时 patch 必须 None；
    # decision ∈ {accept, partial} 时 patch 应包含具体的 plan 修改动作
    patch: CriticAction | None = None


class MergerResult(BaseModel):
    """ReviewMerger LLM 输出。"""

    actions: list[MergerAction] = Field(default_factory=list)
    overall_comment: str = Field(default="", description="对整体修订的总评")
```

**关键决策**：

* **复用 `CriticAction`** 而非新定义 `MergerPatch` —— 两者语义相同（"对 plan 节点的 remove/replace/insert\_after 操作"），无须重复

* `decision = reject` 时 `patch` 必为 None；service 层校验，不放进 Pydantic 约束（避免 discriminated union 复杂度）

### 4.2 ReviewMerger — 新增 `app/core/review_merger.py`

```python
"""Review Merger：comments + plan vN → plan v{N+1} 的 LLM 编排。"""
from pathlib import Path

from app.core.logging import get_logger
from app.core.merger_schemas import MergerAction, MergerResult
from app.core.plan_engine import _apply_critic, _load_prompt
from app.core.plan_schemas import CriticResult, PlanDocument
from app.db.models import Comment
from app.llm.router import LLMRouter
from app.llm.types import ResolvedAdapter

_log = get_logger("review_merger")


class ReviewMerger:
    """评论合并器。复用 PlanEngine 的 _apply_critic / _assign_ids。"""

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
    import json
    payload = [
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
```

**关键设计**：

* `merge()` 返回 `tuple[PlanDocument, MergerResult]`：前者落库，后者返回给前端展示决策

* comments 空 → 不调 LLM 直接返（service 层会先校验，这里再防一道）

* 所有 reject 的 action（patch=None）自动跳过应用，但仍在 MergerResult 中可见

* **复用 `_apply_critic`**：把 patches 包成 `CriticResult` 交给现成函数，零代码重复

### 4.3 Prompt 模板 — 新增 `backend/src/app/llm/prompts/merger.md`

```markdown
你是一个计划修订专家。给你一份当前计划文档和一组用户评论，逐条评估评论后产生修订指令。

## 硬约束
- 对每条评论必须给出 decision ∈ {accept, reject, partial}
- 给出一句话 reason
- decision = accept / partial 时，必须给出 patch（CriticAction：remove / replace / insert_after）
- decision = reject 时，patch 字段必须为 null
- patch.node_index 是评论锚点所在节点的 0-based 下标，不要超界
- 不要生成节点 ID（id 字段留空字符串，框架会自动分配）
- 只动评论指明的节点，不要顺手改无关节点
- 如果评论自相矛盾或不可执行 → reject + 解释原因

## 输入

- **当前计划 v{N}**：
{plan_json}

- **用户评论列表**：
{comments_json}

## 输出
严格按 MergerResult JSON Schema 输出。
```

**关键决策**：

* prompt 复用 critic.md 的 "硬约束 → 输入 → 输出" 三段式结构，**降低 LLM 跨 prompt 行为差异**

* 不显式列 PlanNode 字段约束（structured output schema 自带校验），prompt 只说"动作和理由"

* 显式禁止"顺手改无关节点"——M2 范围内 LLM 行为收敛优先于灵活性

### 4.4 SessionService.merge\_plan — 修改 `app/services/session_service.py`

```python
async def merge_plan(
    self,
    *,
    session_id: UUID,
    router: LLMRouter,
) -> tuple[PlanDocument, MergerResult, int]:
    """编排：拉评论 → 调 Merger → 落新 plan version → 标 resolved。

    返回 (new_plan, merger_result, new_plan_version)。
    """
    session = await self._db.get(Session, session_id)
    if session is None:
        raise SessionNotFoundError(str(session_id))
    if session.phase != "plan_review":
        raise ValueError("phase_not_review")

    # 拉当前 plan + unresolved comments
    current_version = session.current_plan_version
    plan = await self._get_plan(session_id, current_version)
    comment_service = CommentService(self._db)
    comments = await comment_service.list_unresolved(
        session_id=session_id, plan_version=current_version,
    )
    if not comments:
        raise ValueError("no_comments_to_merge")

    # 调 ReviewMerger（需要 adapter）
    adapter_service = AdapterService(self._db)
    db_adapter = await adapter_service.get(session.adapter_id)
    adapter = adapter_service.resolve(db_adapter)
    merger = ReviewMerger(router)
    new_plan_doc, merger_result = await merger.merge(
        plan=PlanDocument.model_validate(plan.document),
        comments=comments,
        adapter=adapter,
    )

    # 落新 plan version
    new_version = current_version + 1
    new_plan = Plan(
        session_id=session_id,
        version=new_version,
        document=new_plan_doc.model_dump(mode="json"),
    )
    self._db.add(new_plan)
    session.current_plan_version = new_version

    # 标 resolved（仅 accept/partial）
    accepted_ids = [
        a.comment_id for a in merger_result.actions
        if a.decision in ("accept", "partial")
    ]
    if accepted_ids:
        await comment_service.mark_resolved(accepted_ids)

    await self._db.flush()
    _log.info(
        "plan_merged",
        session_id=str(session_id),
        from_version=current_version,
        to_version=new_version,
        actions_count=len(merger_result.actions),
        accepted_count=len(accepted_ids),
    )
    return new_plan_doc, merger_result, new_version
```

**关键决策**：

* `merge_plan` 接 `router: LLMRouter` 作为参数（而非 `__init__` 依赖），避免 SessionService 与 router 强耦合 —— 由 endpoint 注入

* **所有 DB 改动在同一 session 内 flush**，由 endpoint 外层 commit 保证原子性

* merge 进行中**不动 phase**，保持 `plan_review`

* 即使 merger\_result 全是 reject（无 patch 应用），仍然 INSERT 新 plan version —— 因为 plan 内容确实没变，但语义上「这一轮 review 已结束」，新版本号利于后续审计

> **修正**：上一条不对。若全 reject，应**不落新 plan 版本**，让用户继续在 v1 上加评论。否则会产生"空版本"垃圾。修订实现：仅当 `accepted_ids` 非空时落新版本；全 reject 时直接返回 `(plan, merger_result, current_version)`，前端 toast 提示"全部评论被驳回，plan 未改动"，用户决定要不要继续编辑评论。

### 4.5 CommentService 增量 — 修改 `app/services/comment_service.py`

新增 2 个方法：

```python
async def list_unresolved(
    self, *, session_id: UUID, plan_version: int,
) -> list[Comment]:
    """按 session + plan_version 拉 resolved=false 的评论。"""
    stmt = (
        select(Comment)
        .where(Comment.session_id == session_id)
        .where(Comment.plan_version == plan_version)
        .where(Comment.resolved.is_(False))
        .order_by(Comment.created_at.asc())
    )
    result = await self._db.execute(stmt)
    return list(result.scalars().all())

async def mark_resolved(self, comment_ids: list[UUID]) -> None:
    """批量标 resolved=true。"""
    if not comment_ids:
        return
    stmt = (
        update(Comment)
        .where(Comment.id.in_(comment_ids))
        .values(resolved=True)
    )
    await self._db.execute(stmt)
```

### 4.6 API endpoint — 修改 `app/api/sessions.py`

```python
class MergeResponse(BaseModel):
    plan_version: int
    plan: PlanDocument  # 新版本完整 doc，前端直接 dispatch 替换
    merger_result: MergerResult
    plan_changed: bool   # accepted_ids 非空时为 True


def get_router_dep() -> LLMRouter:
    """复用 ws_plan.get_router 的 lru_cache 单例（M1-10a-fixup 已建立）。"""
    from app.api.ws_plan import get_router
    return get_router()


@router.post("/{session_id}/merge", response_model=MergeResponse)
async def merge_plan_endpoint(
    session_id: UUID,
    service: SessionService = Depends(get_session_service),
    llm_router: LLMRouter = Depends(get_router_dep),
) -> MergeResponse:
    try:
        new_plan_doc, merger_result, new_version = await service.merge_plan(
            session_id=session_id, router=llm_router,
        )
    except SessionNotFoundError as e:
        raise HTTPException(404, "session_not_found") from e
    except ValueError as e:
        msg = str(e)
        status = 409 if msg == "phase_not_review" else 400
        raise HTTPException(status, msg) from e
    # LLM 错误向上冒：transport / structured_output → 500
    return MergeResponse(
        plan_version=new_version,
        plan=new_plan_doc,
        merger_result=merger_result,
        plan_changed=any(
            a.decision in ("accept", "partial") for a in merger_result.actions
        ),
    )
```

**错误码约定**（沿用 Task 11）：

* 404 `session_not_found`

* 409 `phase_not_review`

* 400 `no_comments_to_merge`

* 500 LLM transport/structured\_output 错误（透传 Task 04.1 已建的 `LLMError` 体系）

### 4.7 shared schema 注册

`backend/src/app/shared/schemas.py` 新增（import 路径 `app.shared.schemas`，与 gen\_schema.py 一致）：

```python
from app.core.merger_schemas import MergerAction, MergerResult
...
SHARED_SCHEMAS = [..., MergerAction, MergerResult]
```

跑 `make gen-schema` 重生成 `shared/schema.json`。

### 4.8 测试矩阵

`backend/tests/test_review_merger.py`（unit, 100% mock router, \~5 cases）:

| # | case                                                | 期望                                   |
| - | --------------------------------------------------- | ------------------------------------ |
| 1 | comments 空 → 不调 LLM 直接返                             | router.complete\_structured **未被调用** |
| 2 | LLM 返全 accept → 所有 patch 应用 + 新 plan 节点变化           | ✓                                    |
| 3 | LLM 返全 reject → 返回原 plan + MergerResult.actions 内容齐 | new\_plan == plan                    |
| 4 | LLM 返非法 patch（node\_index 越界）→ 跳过该 action 不崩        | `_apply_critic` 已处理（M1-07 行为复用）      |
| 5 | 混合 accept/reject/partial                            | 应用 patch ∈ accept ∪ partial 的        |

`backend/tests/test_session_merge_service.py`（integration, real AsyncSession + mock router, \~6 cases）:

| # | case                                                                         | 期望                                                  |
| - | ---------------------------------------------------------------------------- | --------------------------------------------------- |
| 1 | 正常 merge：v1 + 2 unresolved → v2 + accepted 标 resolved                        | DB 状态对齐                                             |
| 2 | 无 unresolved comments → `ValueError("no_comments_to_merge")`                 | <br />                                              |
| 3 | phase=acting → `ValueError("phase_not_review")`                              | <br />                                              |
| 4 | 全 reject → 不落新 plan version, current\_plan\_version 不变, comments.resolved 不变 | <br />                                              |
| 5 | session 不存在 → `SessionNotFoundError`                                         | <br />                                              |
| 6 | merger\_result 含 accepted\_id 不在 comments 中（LLM 编错 id）                       | 安静跳过（mark\_resolved with non-existent UUID 是 no-op） |

`backend/tests/test_merge_api.py`（API integration, \~3 cases）:

| # | case                              | 期望 HTTP                                                                   |
| - | --------------------------------- | ------------------------------------------------------------------------- |
| 1 | POST /merge 正常                    | 200 + body 含 plan\_version=2 / plan / merger\_result / plan\_changed=true |
| 2 | POST /merge phase=acting          | 409 detail "phase\_not\_review"                                           |
| 3 | POST /merge 无 unresolved comments | 400 detail "no\_comments\_to\_merge"                                      |

***

## 5. 前端设计 (Task 12b)

### 5.1 API client — 新增 `frontend/src/api/merge.ts`

```ts
import type { MergerResult, PlanDocument } from '@/types/shared'

export interface MergeResponse {
  plan_version: number
  plan: PlanDocument
  merger_result: MergerResult
  plan_changed: boolean
}

export async function mergeReviews(sessionId: string): Promise<MergeResponse> {
  const res = await fetch(`/api/sessions/${sessionId}/merge`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail ?? `merge_failed_${res.status}`)
  }
  return (await res.json()) as MergeResponse
}
```

### 5.2 sessionReducer 扩展 — 修改 `frontend/src/state/sessionReducer.ts`

新增 1 个 action：

```ts
| { type: 'MERGE_COMPLETED'; planVersion: number; plan: PlanDocument }
```

reducer 逻辑（仅 review 状态下生效）：

```ts
case 'MERGE_COMPLETED':
  if (state.status !== 'review') return state
  return {
    ...state,
    planVersion: action.planVersion,
    plan: action.plan,
    comments: [],  // v{N+1} 的评论列表为空，等 useEffect 自动 listComments(v{N+1}) 补
  }
```

**注意**：mergeReviews 成功后，`useEffect(... [state.status, state.planVersion ...])` 会自动重跑 listComments 拉 v{N+1} 的 comments（应该为空，因为还没人评论 v2）。**不要在 reducer 里手动 dispatch LOAD\_COMMENTS**。

### 5.3 CommentThreadPanel — 修改 `frontend/src/components/CommentThreadPanel.tsx`

在 `<aside>` 内、`<h3>Comments</h3>` 下面，**评论列表之上**插入"Apply Reviews"按钮：

```tsx
{onApplyReviews && (
  <button
    className="apply-reviews-btn"
    onClick={onApplyReviews}
    disabled={mergeBusy || unresolvedCount === 0}
  >
    {mergeBusy ? 'Merging...' : `Apply Reviews (${unresolvedCount})`}
  </button>
)}
```

新增 props：

```ts
interface CommentThreadPanelProps {
  ...
  onApplyReviews?: () => void
  mergeBusy?: boolean
  unresolvedCount?: number
}
```

UI 行为：

* `unresolvedCount === 0` → 灰按钮，不可点（前端先校验，省 1 次 400 round-trip）

* `mergeBusy === true` → 文案改 "Merging..."，按钮 disabled

* 评论列表项：**resolved=true 的评论灰显**（CSS `.comment-resolved` + opacity 0.5）

### 5.4 App.tsx 装配 — 修改 `frontend/src/App.tsx`

新增 state：

```ts
const [mergeBusy, setMergeBusy] = useState(false)
```

新增 handler：

```ts
const handleApplyReviews = useCallback(async () => {
  if (state.status !== 'review' || mergeBusy) return
  setMergeBusy(true)
  try {
    const result = await mergeReviews(state.sessionId)
    const accepted = result.merger_result.actions
      .filter(a => a.decision === 'accept' || a.decision === 'partial').length
    const rejected = result.merger_result.actions
      .filter(a => a.decision === 'reject').length
    if (!result.plan_changed) {
      // 全 reject：不落新版本（决策 §13-2.A），弹窗提醒，comments 保持 unresolved
      alert(`All ${rejected} comments rejected, plan unchanged`)
      return
    }
    dispatch({
      type: 'MERGE_COMPLETED',
      planVersion: result.plan_version,
      plan: result.plan,
    })
    // 简单提示；TODO Task 13 用抽屉展示完整 merger_result（决策 §13-1.A）
    alert(`Plan v${result.plan_version}: ${accepted} accepted, ${rejected} rejected`)
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'merge_failed'
    dispatch({ type: 'WS_ERROR', code: 'merge_failed', message: msg })
  } finally {
    setMergeBusy(false)
  }
}, [state, mergeBusy])
```

把 unresolvedCount 与 mergeBusy 传给 CommentThreadPanel：

```tsx
<CommentThreadPanel
  ...
  onApplyReviews={handleApplyReviews}
  mergeBusy={mergeBusy}
  unresolvedCount={comments.filter(c => !c.resolved).length}
/>
```

**alert 是临时方案**——Task 13 加 diff 抽屉时替换为更精细的 UI。

### 5.5 anchor mark 在新 plan 上的处理

merge 完成后：

* `MERGE_COMPLETED` 替换 `state.plan` → Tiptap 编辑器重新渲染整个 doc（StarterKit 默认行为）

* `state.comments` 被清空 → existing AnchorMark 的回放循环（App.tsx 修过的 useEffect）不会再 apply v1 的 mark

* 等到 `useEffect [state.planVersion]` 重跑 listComments(v2) → 拉到空列表 → 无 mark 应用

**已知缺陷**：v1 的 reject comments 在 v2 视图中**不可见**（前端从 `listComments?plan_version=2` 拉，自然拿不到 v1 的）。Task 13 加版本切换 UI 后，用户可点回 v1 看 reject 评论。Task 11 demo 验收的"评论位置不丢"指的是同版本刷新场景，Task 12 不破坏这个性质。

***

## 6. 数据流（端到端）

```
[plan_review phase, plan v1, 3 unresolved comments]
        ↓ 用户点击 "Apply Reviews (3)"
[App.handleApplyReviews]
   setMergeBusy(true)
        ↓
[POST /api/sessions/{sid}/merge]
[SessionService.merge_plan]
   ├ 校验 phase==plan_review ✓
   ├ 拉 plan v1 + unresolved comments (n=3) ✓
   ├ ReviewMerger.merge
   │    ├ 渲染 merger.md (plan_json + comments_json)
   │    ├ router.complete_structured(schema=MergerResult)
   │    └ 提取 patches → _apply_critic → new_plan_doc
   ├ INSERT plans (version=2, document=new_plan_doc)  ─┐
   ├ UPDATE sessions current_plan_version=2            │ 同事务
   └ UPDATE comments resolved=true WHERE id IN accepted ┘
        ↓ 200 { plan_version: 2, plan, merger_result, plan_changed: true }
[App]
   dispatch MERGE_COMPLETED → reducer 替换 plan + planVersion=2 + comments=[]
   useEffect 见 planVersion 变 → listComments(v2) → 空
   PlanDocEditor 用新 doc 重渲染 → 旧 AnchorMark 全消失
   alert("Plan v2: 2 accepted, 1 rejected")
   setMergeBusy(false)
```

***

## 7. 边界情况

| 情况                                              | 处理                                                                                                                                           |
| ----------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| 用户在 merge 进行中提交新评论                              | 后端 POST /comments 仍然成功（plan\_version=1，phase 仍是 plan\_review）。merge 完成后 current\_plan\_version=2，这条 v1 评论留在 DB 但 v2 视图看不到（v1 视图能看，需 Task 13） |
| LLM 输出 comment\_id 不在请求 comments 列表中            | mark\_resolved 的 UPDATE 自然 no-op；不报错；structlog 不记录该字段无用                                                                                      |
| LLM 输出 0 个 action                               | merger\_result.actions=\[]，patches=\[]，new\_plan==plan；service 层判定全 reject 逻辑（accepted\_ids 空 → 不落新版本）                                       |
| 全 reject 但 LLM 给的 reason 是 "评论无意义"等             | service 层不落新 plan version，返回 plan\_changed=false；前端 alert "全部驳回"                                                                             |
| LLM 调用超时                                        | LLMTransportError 透传 → 500；前端 ErrorBanner 展示；用户可重试（之前的 comments 仍 unresolved）                                                                |
| Plan 节点被 merge 删光                               | 允许；M2 不防护；UI 渲染空 doc；用户感知到 plan 没了，可在 v2 上重新评论或不操作                                                                                           |
| LLM 输出 patch.node\_index 越界                     | `_apply_critic` 已 log + skip（M1-07 行为复用）                                                                                                     |
| 同一 comment 在 MergerResult.actions 里出现两次         | LLM 错乱；前者后者都尝试应用 patch；最后一次 mark\_resolved 生效；UNIQUE 约束无；M2 不防护                                                                              |
| current\_plan\_version=1 但 plans 表无 v1 行（数据不一致） | `_get_plan` raise `invalid_plan_version` → 400；理论上不会发生（save\_plan 与 current\_plan\_version+1 同事务）                                            |

***

## 8. 关键设计决策

| 决策                               | 选择                                                                             | 理由                                                          |
| -------------------------------- | ------------------------------------------------------------------------------ | ----------------------------------------------------------- |
| 触发机制                             | **HTTP POST /merge**（同步阻塞）                                                     | M2 验证可行性优先，不在乎 5-15s 延迟；流式留给 Task 13/M3                     |
| Comments 范围                      | **当前 plan\_version 的全部 unresolved**                                            | 简单；用户选择性 merge 留给 Task 13/14                                |
| Merger 输出形态                      | **新 PlanDocument + MergerResult（决策记录）**                                        | 前端 plan 直接替换 + UI 展示每条决策                                    |
| comments 标 resolved 规则           | **仅 accept/partial 标 resolved=true**，reject 留 `resolved=false`                 | 用户能在 v2 视图（或 Task 13 版本切回 v1）看到驳回理由；resolved 字段语义是"已被采纳并落库" |
| Phase 转移                         | **全程不动**（保持 plan\_review）                                                      | merge 是 plan\_review 内部事件；phase 切换由用户显式触发                   |
| 全 reject 时是否落新 plan version      | **不落**                                                                         | 防止生成"无变化垃圾版本"；用户感知"驳回不计代"                                   |
| 复用 CriticAction vs 新 MergerPatch | **复用 CriticAction**                                                            | 语义同；零代码重复                                                   |
| 复用 `_apply_critic` vs 新 apply 函数 | **复用**                                                                         | 同上                                                          |
| LLMRouter 注入方式                   | **endpoint 注入 + Depends(get\_router\_dep)**，复用 ws\_plan 的 lru\_cache 单例        | 避免 SessionService 强耦合 router；与 M1-10a-fixup 立的单例约定一致        |
| 错误处理粒度                           | **service ValueError → 400/409 / SessionNotFoundError → 404 / LLMError → 500** | 沿用 Task 11 模式                                               |
| 跨版本评论可见性                         | **v2 视图不显示 v1 评论**（含 reject）                                                   | Task 13 加版本切换 UI 后解决                                        |
| Merge 历史持久化                      | **不存**                                                                         | M2 简化；Task 13 视需求决定是否加 `merger_results` 表                   |

***

## 9. 复用清单

| 用途                                               | 已有           | 路径                                                                                |
| ------------------------------------------------ | ------------ | --------------------------------------------------------------------------------- |
| `_apply_critic` / `_assign_ids` / `_load_prompt` | M1-07        | [plan\_engine.py](../../backend/src/app/core/plan_engine.py)                      |
| `CriticAction` schema                            | M1-07        | [plan\_schemas.py:65](../../backend/src/app/core/plan_schemas.py#L65)             |
| `LLMRouter.complete_structured`                  | M1-04        | [llm/router.py](../../backend/src/app/llm/router.py)                              |
| `ws_plan.get_router` 单例                          | M1-10a-fixup | [ws\_plan.py:25-28](../../backend/src/app/api/ws_plan.py#L25-L28)                 |
| AdapterService                                   | M1-04.1      | [services/adapter\_service.py](../../backend/src/app/services/adapter_service.py) |
| `CommentService` (`list_by_version`)             | M2-11a       | [services/comment\_service.py](../../backend/src/app/services/comment_service.py) |
| HTTPException + `from e` 错误码模式                   | M1-10        | [api/sessions.py](../../backend/src/app/api/sessions.py)                          |
| 前端 reducer / fetch wrapper 模式                    | M2-11b       | [api/comments.ts](../../frontend/src/api/comments.ts)                             |
| structlog logger                                 | M1-05        | [core/logging.py](../../backend/src/app/core/logging.py)                          |

***

## 10. 测试 & 验收

### 10.1 后端

```bash
cd backend && uv run ruff check src tests
cd backend && uv run pytest -m "not smoke"
# 期望：M2-11 之后 148 → Task 12 新增 ~14 → 162 全绿
```

### 10.2 前端

```bash
cd frontend && pnpm --config.verify-deps-before-run=false exec vitest run
cd frontend && pnpm --config.verify-deps-before-run=false exec tsc --noEmit
# 期望：reducer.test.ts 新增 1 case MERGE_COMPLETED → 21 → 22 全绿
```

### 10.3 手工 E2E（§1.1 完整流程）

附加冒烟项：

* 全 reject 路径：故意提"plan 已经完美无须改动" 3 条 → Apply Reviews → 期望 alert "0 accepted, 3 rejected" + 无新 plan version

* 评论被 reject 后能看到驳回理由：M2-12 范围内 reject comments 仍 unresolved，下次刷新页面仍在列表里（CSS 可加 reject 标识，作为 polish 项）

***

## 11. Commit 拆分

| commit | 范围                                               | 标题                                                                       |
| ------ | ------------------------------------------------ | ------------------------------------------------------------------------ |
| 1      | Task 12a 后端 + 本设计文档                              | `feat(backend): ReviewMerger + merger.md + POST /merge + tests (M2-12a)` |
| 2      | Task 12b 前端（merge.ts + reducer + Panel + App 装配） | `feat(frontend): Apply Reviews button + MERGE_COMPLETED action (M2-12b)` |

设计文档与 commit 1 同入。

***

## 12. M2 后续展望

* **Task 13 Plan 版本管理 + diff 视图**：版本切换 API（`GET /api/sessions/{sid}/plans/{version}`）+ 节点级 diff 组件；本 task 留下的"v2 视图看不到 v1 reject 评论"由版本切换解决

* **Task 14 Anchor 鲁棒性**：v1 的 anchor\_id 在 v2 上 fuzzy match 回源；本 task 简化为"merge 后 anchor 全消"

* **post-MVP**：merger 改进 — 用户选择性 merge（挑某些 comments 合并）/ merge 历史持久化（`merger_results` 表）/ WS 流式 merge 进度

***

## 13. 待主人决策（开工前）

> **决策结果（2026-08-10 主人拍板）**：三题全选 **A**。实施以此为准。

1. **alert vs 抽屉展示 merger\_result** → **A. 接受**：本 task 用 alert 临时方案，完整 diff/决策展示抽屉留给 Task 13（控制 task 边界）。

2. **全 reject 时落不落新版本** → **A. 不落**（文档已论证）。补充主人要求：**前端需弹窗提醒用户感知**——全 reject 时 alert `All N comments rejected, plan unchanged`（已落入 §5.4 handler），避免"等了 10 秒啥也没变"的困惑。

3. **alert 文案语言** → **A. 保持英文**（前端整体英文 UI 风格）。
