# 27. Action Review UI + 用户评论 → 触发 rerun / 改 plan（闭环）

- 任务：ROADMAP M4 #27
- 前置：19（/act WS + 流式）、20（GitCheckpoint）、21（Action 输出面板）、23（episodic + `POST /complete`）、26（局部 rerun 机制）
- 状态：已实施（2026-09-04，见文末实施记录）

## 目标

M4 验收第 2 条："某 step 失败后，UI 上点'重跑'能真重跑且 git 干净"。26 号只交付了机制（REST + WS + git revert），没有任何前端入口。27 号把机制接上 UI，并补齐 PRAR 四环里缺失的第四环 **Action Review**：

1. 执行结束后进入可操作的 review 态（不再停在 `acting`）
2. 用户能对单个 step 的执行结果写评论
3. 评论有两条出路：**改 plan**（评论 → merge → 新版本 → 回 Plan Review）或直接 **rerun**（原样回滚重跑）
4. session 能从 UI 走到 `done`（23 号的 `POST /complete` 至今无前端入口）

## 现状调研（2026-09-04）

### 前端完全没有 action_review 概念

- `frontend/src` 全树 grep `action_review` 零命中。
- `sessionReducer.ts` 的 `SessionState` 是 6 态联合：idle / connecting / streaming / review / acting / error。`WS_ACT_PLAN_DONE` 只把 `run.status` 置 `done`，**state 仍是 `acting`**（`sessionReducer.ts:234-239`）。
- `App.tsx:422` 渲染条件 `{state.status === 'acting' && <ActionOutputPanel run={state.run} />}` —— 执行完后面板留着，但没有任何后续动作入口。
- `CommentThreadPanel` 仅在 `isReview`（`state.status === 'review'`）时渲染（`App.tsx:387`）。

### step 评论走不进现有评论管道（两处硬阻塞）

1. **phase 硬绑**：`comment_service.py:37-38` `if session.phase != "plan_review": raise ValueError("phase_not_review")`。
2. **quote 抽不到 step title**：`_extract_text`（`comment_service.py:130-138`）只抽 `text` 与 `question/term/definition/description`，**不含 `title`**。step 节点的 title 因此不在 `_quote_in_plan` 的比对文本里，`quote=step.title` 必然 400 `quote_not_found_in_plan`。

### StepNode 是 atom 节点，anchor mark 那套对它无效

`frontend/src/editor/nodes/StepNode.tsx:29` `atom: true`；title 渲染在 React NodeView 的 `<strong>{title}</strong>`（`:15`），**不在 ProseMirror 文本流里**。因此：

- `applyAnchorMark` 无法对 step 打 mark（没有 text node）
- M2-14 的 `findAnchorRange` fuzzy 回源对 step title 必然失败 → 评论会掉进 dangling 集合，显示"⚠ 原文已变更，锚点无法定位"，属错误提示

结论：step 评论的锚点是 **ActionOutputPanel 里的 StepCard**，不是编辑器文档。

### rerun 机制已就绪，但前端有一个必踩的坑

- 后端：`POST /api/sessions/{id}/rerun`（D4 校验矩阵）写 `metadata_json.pending_rerun_from` + phase → acting；`ws_act.py:103-137` 消费 pending → `rollback_to` + `execute_plan(start_from=...)`。
- 前端坑：`WS_ACT_STEP_START` 按 stepId 去重（`sessionReducer.ts:155` `if (state.run.steps.some((s) => s.stepId === action.step_id)) return state`）。rerun 时后端会**重发同一批 step_id 的 step.start**，若不清空旧 steps，重跑事件会被静默吞掉、面板永不更新。

### 改 plan 必须两跳 phase

`TRANSITIONS[Phase.ACTION_REVIEW] = {ACTING, PLANNING, DONE}`（`state_machine.py:25`），而 `TRANSITIONS[Phase.PLANNING] = {PLAN_REVIEW}`。所以 `ACTION_REVIEW → PLAN_REVIEW` **非法**，`transition()` 会 raise `InvalidTransitionError`（`state_machine.py:51-67`，非静默）。合法路径是 `ACTION_REVIEW → PLANNING → PLAN_REVIEW` 两跳。

另：`ws_plan.py` 全文 grep `phase`/`Phase` 零命中 —— WS `/plan` 不校验 phase，但 `save_plan` 内部 `transition(Phase(s.phase), Phase.PLAN_REVIEW)`（`session_service.py:89-92`）会因非法转移炸掉。所以"重新 generate plan"这条路必须先把 phase 拨到 `planning`，成本高于复用 merge。

### 已有可复用资产

| 资产 | 复用方式 |
|---|---|
| `ReviewMerger`（12 号） | 改 plan 的 LLM 编排，评论 → patch → `_apply_critic` |
| `merge_plan`（`session_service.py:177-247`） | 全 reject 不落版本的语义已实现 |
| `CommentThreadPanel` | props 已足够通用（`onJumpToAnchor` / `danglingIds` / `readonly` 可覆写） |
| `ActionOutputPanel` + `StepCard` | 加按钮即可，渲染逻辑不动 |
| `POST /rerun`、`POST /complete`、`POST /merge` | 三个端点全部已存在，27 号**零新增端点** |
| `MergeResultDrawer` | 改 plan 后的决策展示 |

### 前端验收命令

`npm run lint`（eslint 9）/ `npm run typecheck`（tsc --noEmit）/ `npm run test`（vitest run）。
注意 `frontend/src` 下的 `.js` 文件是**未跟踪的构建产物**（`git ls-files` 计数 0），任何情况下不得 stage。

## 设计决策

### D1 前端新增 `action_review` 与 `done` 两态

```ts
| { status: 'action_review'; sessionId: string; planVersion: number
    plan: PlanDocument; run: ActionRun; comments: CommentResponse[] }
| { status: 'done'; sessionId: string }
```

- `WS_ACT_PLAN_DONE`：`acting` → `action_review`，`run.status='done'`、`run.allOk=all_ok`、`comments=[]`（由 App 的 effect 拉取）。
- `WS_ERROR` 在 `acting` 时的行为**不变**（留在 acting + `run.status='failed'`），此时 UI 只有错误横幅与重置出口 —— 见风险表末行。
- 新增 `SESSION_COMPLETED` → `{ status: 'done' }`；done 态渲染完成横幅 + "开始新会话"（复用 `handleReset`）。

### D2 step 评论：quote = step title，anchor_id = `step:{step_id}`

后端两处最小改动（**零 schema 变更、零迁移**）：

1. `comment_service.create` 的 phase 校验放宽为集合：
   ```python
   if session.phase not in ("plan_review", "action_review"):
       raise ValueError("phase_not_review")
   ```
   错误码字符串保持不变（`api/comments.py` 的 `409 if msg == "phase_not_review"` 映射无需改）。
2. `_extract_text` 的字段元组加 `title`：
   ```python
   for k in ("title", "question", "term", "definition", "description")
   ```
   语义修正而非放宽漏洞 —— title 确实是 plan 的可见内容（`StepView` 渲染它）。

前端约定：

| 字段 | 取值 |
|---|---|
| `anchor_id` | `step:{step_id}`（前缀让 App 区分跳转目标：编辑器 mark vs step 卡片） |
| `quote` | `step.title` |
| `quote_context` | `failureReason ?? stdout 首行 ?? ''`，截断 ≤200（schema 上限） |
| `plan_version` | `state.planVersion`（当前版本） |
| `body` | 用户输入 |

**不打 ProseMirror anchor mark**，`danglingIds` 传空集（避免"锚点无法定位"误导）；`onJumpToAnchor` 覆写为滚动 + 高亮对应 StepCard。`CommentThreadPanel` 的 `pendingSelection.from/to` 对 step 评论无意义（后端不存这两个字段，仅前端本地 `applyAnchorMark` 用），传 0。

### D3 rerun 触发面：StepCard 按钮 + `START_RERUN` 截断

按钮渲染条件（三者同时满足）：`run.status !== 'running'` && 该 step 在 plan 里 `rerunnable === true` && `state.status === 'action_review'`。

`rerunnable` 来自 `PlanNode`（`types/shared.d.ts:21`），但 `ActionStep` 里没有 → App 从 `state.plan.nodes` 构造 `stepId → rerunnable` 映射传入面板。这是既有字段的首次实际消费。

点击流程：

```
POST /rerun {step_id}
  → dispatch START_RERUN { fromStepId }
      status: action_review → acting
      run.steps: 只保留 index < 目标 step 的条目   ← 必须，否则 D1 的去重吞事件
      run.status: 'running'，allOk: null，error: null
  → new ActStreamClient().connect(...)，onOpen 里 sendExecute()
```

`START_RERUN` 在非 `action_review` 态原样返回 state（与其他 case 的守卫风格一致）。

### D4 改 plan：复用 `merge_plan`，仅在有 accept 时两跳 phase

`session_service.merge_plan` 改动：

1. 入口校验放宽：`if s.phase not in ("plan_review", "action_review"): raise ValueError("phase_not_review")`
2. **两跳放在 `accepted_ids` 判空之后**，保证全 reject 时 phase 一动不动：

```python
if not accepted_ids:
    return (原 plan, merger_result, current_version)   # phase 保持 action_review

if s.phase == Phase.ACTION_REVIEW.value:
    transition(Phase.ACTION_REVIEW, Phase.PLANNING, session_id=...)
    transition(Phase.PLANNING, Phase.PLAN_REVIEW, session_id=...)
    s.phase = Phase.PLAN_REVIEW.value
# plan_review 分支：不调 transition，phase 保持 plan_review（现状零改动）
```

顺序关键：`TRANSITIONS[PLAN_REVIEW] = {PLANNING, ACTING}` 不含自身，若无条件做 `transition(s.phase, PLAN_REVIEW)`，plan_review 分支会自炸。

前端：action_review 态的"按评论改 plan"按钮复用 `handleApplyReviews`（`mergeReviews` + `MergeResultDrawer`），成功后 `MERGE_COMPLETED` 把 state 带回 `review`，用户在 Plan Review 里再审再执行。

### D5 完成出口

action_review 态渲染"标记完成"按钮 → `POST /api/sessions/{id}/complete` → `SESSION_COMPLETED` → done 态。

`complete` 会先写 episodic 记忆（23 号契约：embedding 失败即上抛，phase 未改、无部分写入）。502 时前端 dispatch `WS_ERROR`，但因 state 仍是 `action_review`，`WS_ERROR` 的 acting 分支不匹配 → 落 `{status:'error'}`，用户丢失 review 现场。故 done 失败的错误**不进 reducer**，用面板内局部 error 文案展示，保留 action_review 可重试。

### D6 边界与非目标

- 不做"评论注入 rerun 的 LLM 提示"：rerun 语义由 26 号定义为原样回滚重跑；评论的影响走 D4 改 plan 路径。二者是 ROADMAP 原文的"或"关系。
- 不做 step 评论的编辑器 anchor mark / fuzzy 回放（atom 节点无文本流，D2 已说明）。
- 不做执行中途中断 / 取消。
- 不改 `ws_act` 首跑环境故障时 phase 卡 `acting` 的行为（26 号 D5 明确只覆盖 rerun 中断）。
- 不做 DB schema 变更、不加 alembic 迁移。
- 不做 step 评论的 resolved 单独管理（沿用 merge 的 accept/partial → resolved）。

### D7 成本与架构

- 零新增依赖（后端 + 前端）。
- 零新增端点：`/rerun`、`/complete`、`/merge`、`/comments` 全部已存在。
- 新增 LLM 调用仅 1 处且由用户主动触发：点"按评论改 plan"时的 merger 调用。rerun 路径零 LLM（26 号验收数据 B 已证：`_NoLLMActor` 全程未被调用）。
- 架构红线守住：api 层不直接调 LLM 或 git —— `api/sessions.py` → `SessionService.merge_plan` → `ReviewMerger` → `LLMRouter`；rerun 的 git 操作在 `ws_act` → dispatcher → `GitCheckpoint`。

## 风险表

| 风险 | 应对 |
|---|---|
| merge 后 `_assign_ids` 重排节点 id，旧 step_id 失效 | 改 plan 成功即落 `plan_review` 并切回 review 态；rerun 按钮只在 action_review 出现，两态互斥，拿不到旧 id |
| rerun 的 `step.start` 被 stepId 去重吞掉 | D3 强制 `START_RERUN` 截断 steps；reducer 测试 R2 直接断言截断结果 |
| `_extract_text` 加 title 让 plan_review 的脏 quote 混入 | title 本就是 plan 可见文本（StepView 渲染）；且 quote 仍需在 plan 全文中精确子串命中，不是无条件放行 |
| `complete` 时 embedding 不可用 → 502 丢失 review 现场 | D5：错误走面板局部文案而非 reducer，state 保持 action_review，可重试 |
| action_review 下用户仍能在编辑器划词加 plan 评论 | 编辑器 `onRequestAddComment` 传 `undefined`（只读）；step 评论只从 StepCard 入口进 |
| 全 reject 时 phase 被误拨到 planning 卡死 | D4：两跳严格放在 `accepted_ids` 判空之后；测试 MG2 断言 phase 与 version 均不变 |
| WS 首跑环境故障后 phase 卡 acting，UI 无出路 | 26 号 D5 已知边界，27 不扩范围：`run.status='failed'` 时展示错误横幅 + 重置出口，不假装可恢复 |

## 测试计划

### 后端（真 VM DB，flush + rollback 隔离，永不 commit）

| # | 测试 | 断言 |
|---|---|---|
| CM1 | action_review 下 create 评论（quote=step title） | 返回 Comment；anchor_id 前缀 `step:` 原样落库 |
| CM2 | acting 下 create 评论 | `ValueError("phase_not_review")` |
| CM3 | `_extract_text` / `_quote_in_plan` 对 step title | 返回 True；对不存在的 title 返回 False |
| MG1 | action_review 下 merge 有 accept | phase == `plan_review`；version == current+1；被 accept 的评论 resolved=True |
| MG2 | action_review 下 merge 全 reject | phase 仍 `action_review`；version 不变；无 resolved |
| MG3 | plan_review 下 merge 回归 | phase 仍 `plan_review`（两跳未被误触发） |
| MG4 | acting 下 merge | `ValueError("phase_not_review")` |

MG1-MG4 的 merger 用 mock router 返回固定 `MergerResult`，不打真 LLM。

### 前端（vitest）

| # | 测试 | 断言 |
|---|---|---|
| R1 | `WS_ACT_PLAN_DONE` | status → `action_review`；`run.status='done'`；`allOk` 正确；`comments=[]` |
| R2 | `START_RERUN` 截断 | 目标 step 及其后被删、之前的保留；status → `acting`；`run.status='running'`；`allOk=null` |
| R3 | action_review 下 `LOAD_COMMENTS` / `ADD_COMMENT` | 生效（守卫放宽验证） |
| R4 | `SESSION_COMPLETED` | status → `done` |
| R5 | 非 action_review 下 `START_RERUN` | 原样返回同一 state 引用 |
| R6 | `WS_ACT_PLAN_DONE` 后 `WS_ERROR` | 落 `{status:'error'}`（action_review 不再吞进 run） |
| P1 | StepCard 重跑按钮渲染条件 | `rerunnable=false` 不渲染；`run.status='running'` 不渲染；点击回调带正确 stepId |
| P2 | StepCard 评论按钮 | 点击回调带 step（title + failureReason） |

### 浏览器 E2E 冒烟（真实后端 + VM DB + 真 LLM）

建 session → plan 流式 → advance → act 执行 → **action_review UI 出现** → 对某 step 写评论并提交 → 点"重跑此步" → 观察 revert + 重跑事件流入面板 → 点"标记完成" → done 横幅。每步截图留证。

### VM 真实验证

沿用 26 号方式：scp 变更文件到 `~/workspace/backend`，`DATABASE_URL=postgresql+asyncpg://prar:prar@localhost:5432/prar_agent ~/.local/bin/uv run pytest tests/test_comment_service.py tests/test_session_merge_service.py -q`（一次性，跑完不留脚本）。

## 实施记录（2026-09-04）

### 落地范围

按 D1-D7 原样实现，零新增依赖、零新增端点、零 schema 变更、零迁移。

后端（2 文件）：
- `comment_service.py`：phase 校验放宽为 `("plan_review", "action_review")`；`_extract_text` 字段元组加 `title`。
- `session_service.py`：`merge_plan` 入口校验同步放宽；两跳 `ACTION_REVIEW → PLANNING → PLAN_REVIEW` 严格置于 `accepted_ids` 判空之后。

前端（6 文件）：
- `sessionReducer.ts`：新增 `action_review` / `done` 两态与 `START_RERUN` / `SESSION_COMPLETED` 两个 action；`WS_ACT_PLAN_DONE` 改落 `action_review`；`LOAD_COMMENTS` / `ADD_COMMENT` / `MERGE_COMPLETED` 守卫放宽。
- `api/sessions.ts`：新增 `requestRerun(sessionId, stepId)`、`completeSession(sessionId)`。
- `ActionOutputPanel.tsx`：新增 `reviewable` / `rerunnableStepIds` / `onRerun` / `onComment` / `highlightStepId` props，StepCard 渲染"评论"/"重跑"按钮与高亮态。
- `App.tsx`：`stepQuoteContext()` 生成 quote_context（failureReason 优先 → stdout 首行 → 空串，截断 200）；`rerunnableStepIds` 由 `currentPlan.nodes` 构造；`handleRerun` 走 POST → `START_RERUN` → 重连 act client（`onOpen` 内 `sendExecute`）；`handleSubmitComment` 对 step 评论跳过 ProseMirror mark；`handleComplete` 失败落 `.action-local-error` 局部文案。
- `App.css`：`.action-bar` / `.complete-button` / `.done-banner` / `.action-local-error` / `.action-step-highlight` 等样式。

### 双端三绿

| 侧 | 命令 | 结果 |
|---|---|---|
| 后端 | `uv run pytest -m "not smoke" -q` | 405 passed, 4 skipped, 1 deselected |
| 后端 | `uv run ruff check .` | All checks passed! |
| 后端 | `uv run mypy src` | Success: no issues found in 55 source files |
| 前端 | `npm run lint` | 0 errors；3 warnings 均为 `App.tsx:135/145/178` 既有基线 |
| 前端 | `npm run typecheck` | 无输出（通过） |
| 前端 | `npm run test` | Test Files 10 passed / Tests 117 passed |

### 浏览器 E2E 闭环（真实后端 + VM Postgres + 真 LLM `qwen3-max`）

会话 `4a0ab865-602e-4048-8aa3-ecffe88e590f`，逐环取证：

1. **action_review UI**：`/act` 跑完后状态落 `action_review`，每个 StepCard 出现"评论"/"重跑"按钮，底部出现"标记完成"栏（`[data-testid="complete-bar"]`）。
2. **step 评论**：`POST /api/sessions/{id}/comments` → 201，落库 `anchor_id="step:step_001"`、`quote="创建并写入待办事项文件"`、`quote_context=` failureReason 截断至 200 字符、`plan_version=1`、`resolved=false`。`GET /comments?plan_version=1` 回读一致。
3. **改 plan（两跳）**：点"按评论改 plan" → 真 LLM 调用（4298.6 ms，in 2322 / out 216）→ 同一 request_id 内 `action_review → planning → plan_review` 两跳 → `plan_merged from_version=1 to_version=2 accepted_count=1 actions_count=1`。v2 确实把工具入参 `"data"` 改成 `"content"`，即评论被真正吸收进 plan。
4. **rerun**：`POST /api/sessions/{id}/rerun` → 200 + `rerun_requested rerun_from=step_001` + `action_review → acting` + 新建 ws_act + `acting → action_review`。前端截断生效证据用 DOM marker 法取得：重跑前给卡片打 `data-marker="pre-rerun"`，重跑后 `markerLeft: false` —— 若未截断，React 会复用同一 keyed DOM 节点，marker 必然还在。
5. **git 干净（M4 验收第 2 条）**：`backend/sandbox/runs/4a0ab865-…` 的历史为
   ```
   b94b9b6 [prar:v2:step_001] 创建并写入待办事项文件
   fc74639 Revert "[prar:v2:step_001] 创建并写入待办事项文件"
   7a11ef1 [prar:v2:step_001] 创建并写入待办事项文件
   90fed64 [prar:v1] init
   ```
   `git show --stat b94b9b6` → `steps/step_001/notes.txt | 3 +++`；`git status --porcelain` → 空。真实 revert commit + 真实重跑 commit + 工作区干净。
6. **标记完成**：`POST /complete` → `SESSION_COMPLETED` → `[data-testid="done-banner"]` 渲染"会话已完成 · 4a0ab865-… " + "开始新会话"，`action-local-error` 为 null。

### 相对设计的偏差

- **测试计划外增 3 例**：R7（`MERGE_COMPLETED` 从 `action_review` 生效）、R5b（`START_RERUN` 传未知 step id 时原样返回）、P3（`highlightStepId` 命中时卡片带 `action-step-highlight`）。均为实现中暴露的真实分支，补测而非改设计。
- **D5 理由外延**：设计只说 complete 失败不进 reducer；实现时把 rerun 失败与 step 评论提交失败也一并走 `.action-local-error` 局部文案。理由相同 —— 任何一类失败若落 `{status:'error'}` 都会让用户丢失 review 现场，而这三类操作都可原地重试。
- **取证方式**：未截图。宿主浏览器视口不可见（见 M3-21 记录的 `NATIVE_BROWSER_VIEWPORT_UNAVAILABLE`），改用 DOM 断言 + 后端结构化日志 + git 历史 + DB 回读四类证据，可复核性强于截图。

### 一个排查记录：E2E 残留数据让 M4-24 测试变红

三绿首次运行时 `test_consolidator_service.py` 3 例失败，`processed` 恒比期望多 1。根因不是代码回归：`MemoryService.list_unconsolidated` 按 `kind='episodic' AND consolidated_at IS NULL` 全表取批次，而测试的 flush + rollback 隔离只能挡住本事务内的行，挡不住**已提交**行 —— 上面第 6 步 `POST /complete` 写入的那条 episodic 记忆（`8228bcd4-31d8-45fc-99d0-e744c2953b56`，18:44:44 UTC）正是唯一一条。删除该 E2E 残留行后 405 passed 全绿，未改任何代码。

遗留脆弱性（不在 27 号范围内）：只要有任何真实 session 走到 complete，这 3 例就会再次变红。要根治得让 consolidator 测试只统计自己 seed 的 id，属 M4-24 的测试隔离改造，应作为独立任务处理。
