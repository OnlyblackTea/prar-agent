# 13. Plan 版本管理 + 前端 diff 视图

> **状态**：APPROVED（2026-08-25）；13a/13b 已实施，E2E 冒烟通过
> **依赖**：Task 12（merge 落 plan v{N+1}、`MERGE_COMPLETED`、alert 临时方案）、Task 11（comments 按 plan_version 查询）
> **被依赖**：Task 14（anchor 跨版本回源时复用历史版本查询 API）
> **commit 范围**：单设计文档，拆 2 个 commit（13a 后端 / 13b 前端），设计文档随 13a 同入

***

## 0. 背景与遗留承诺

Task 12 设计文档留给本任务的三笔债（均已写入 12 文档）：

1. **§13-1.A（主人已拍板）**：merge 完成后用**抽屉**展示完整 `merger_result` + diff，替换临时 `alert`。
2. **§5.5 已知缺陷**：v1 的 reject comments 在 v2 视图不可见 → 本任务加**版本切换**，用户可切回 v1 查看（含驳回理由）。
3. **§12 展望**：版本切换 API（`GET /api/sessions/{sid}/plans/{version}`）+ 节点级 diff 组件。

ROADMAP 产出要求："能看到 v1 → v2 节点级 diff"。

## 1. 目标

- 后端暴露 plan 版本历史（列表 + 按版本取）；**DB `plans` 表为唯一事实源**（已有全量存储，不加文件系统落盘，见 §8 未决 1）
- 前端：
  - merge 完成后弹**抽屉**：逐条评论决策（accept/partial/reject + reason）+ v{N-1} → v{N} 节点级 diff 高亮，替换 `alert`
  - **版本选择器**：浏览任意历史版本（只读渲染 + 该版本的评论列表），随时切回当前版本继续评论
- 验收标准：
  - `GET /plans` 返回全部版本元信息；`GET /plans/{version}` 404 边界正确
  - 抽屉内能看到每条注释的决策理由与节点级 diff（增/删/改三色标记）
  - 切到 v1 能看到该版本的评论（含 reject 的 unresolved 评论）；历史版本只读
  - `diffPlans` 纯函数单测覆盖增/删/改/混合/空输入

## 2. 输入 / 输出

- 上游产物：
  - `plans` 表（Task 02）：`(session_id, version, document)` 已全量持久化
  - `SessionService._get_plan`（Task 12 已写私有方法）
  - `MergeResponse.merger_result`（Task 12）
  - 前端 `PlanDocEditor` / `planToTiptapDoc` / `sessionReducer`（Task 09-12）
- 交付物：
  - 后端：2 个只读 API + service 公开方法 + 新 schema
  - 前端：`diff.ts` 纯函数、`MergeResultDrawer`、`PlanDiffView`、版本选择器、`api/plans.ts`

## 3. 接口设计

### 3.1 后端 API（只读，无状态变更）

```
GET /api/sessions/{session_id}/plans
  200 → PlanListResponse
  404 → session_not_found

GET /api/sessions/{session_id}/plans/{version}
  200 → PlanResponse（复用 Task 12 已有 schema）
  404 → session_not_found / plan_version_not_found
```

```python
class PlanSummary(BaseModel):
    version: int
    node_count: int          # len(document["nodes"])，省得前端拉全文档算
    created_at: datetime

class PlanListResponse(BaseModel):
    session_id: UUID
    current_version: int
    versions: list[PlanSummary]   # version 升序
```

- `node_count` 从 `document` JSONB 现算（`len(doc.get("nodes", []))`），不加表列（避免 migration）。
- 两个 schema 加入 `shared/schema.json`（`make gen-schema`）。

### 3.2 Service 层

```python
# SessionService 新增：
async def list_plans(self, session_id: UUID) -> tuple[models.Session, list[models.Plan]]:
    """session 存在性校验 + 全版本升序返回。"""

async def get_plan(self, session_id: UUID, version: int) -> models.Plan:
    """公开版 _get_plan；version 不存在 raise ValueError('plan_version_not_found')。"""
```

### 3.3 前端 diff 算法（纯函数 `editor/diff.ts`）

**节点 id 跨版本不稳定**（`_assign_ids` 按类型序号重排，删一个节点后续全移位），因此不能按 id 对齐，用 **LCS + 相似度**：

```ts
export interface DiffRow {
  kind: 'unchanged' | 'added' | 'removed' | 'modified'
  oldNode?: PlanNode
  newNode?: PlanNode
}

export function diffPlans(oldNodes: PlanNode[], newNodes: PlanNode[]): DiffRow[]
```

算法：
1. 相似度 `sim(a, b)`：`a.type !== b.type` → 0；否则对节点的"文本指纹"（heading/paragraph 的 `text`；decision/step/glossary 的 `JSON.stringify(attrs)`）计算 bigram Jaccard，得 0~1。
2. DP LCS（n×m，plan 节点量 < 50，O(nm) 无压力）：`sim ≥ 0.5` 视为可匹配，DP 值 = 前驱 + sim（优先匹配更相似的）。
3. 回溯产出 `DiffRow[]`：匹配对若完全相等 → `unchanged`，否则 `modified`；未匹配的旧节点 `removed`、新节点 `added`。

### 3.4 前端组件与状态

```
App.tsx
 ├─ MergeResultDrawer（merge 成功后打开）
 │   ├─ 决策列表：每条 comment → decision 徽标 + reason
 │   └─ PlanDiffView(rows)        ← diffPlans(prevPlan.nodes, newPlan.nodes)
 ├─ 版本选择器 <select>           ← review 态 + versions.length > 1 时显示
 │   options: v1..v{current}（当前版本标 "current"）
 └─ PlanDocEditor（不变；历史版本时 onRequestAddComment=undefined → 天然只读无气泡）
```

- **prevPlan 不进 reducer**：App 用 `useRef<PlanDocument | null>` 在 `handleApplyReviews` dispatch 前保存 `state.plan`，供抽屉 diff；抽屉关闭即弃。
- **版本浏览态**：App 本地 `const [viewingVersion, setViewingVersion] = useState<number | null>(null)`（null = 当前版本）。选中历史版本时：`getPlan(sid, v)` 取文档渲染 + `listComments(sid, v)` 展示该版本评论；编辑器不传 `onRequestAddComment`、隐藏 Apply 按钮（`CommentThreadPanel` 加 `readonly` prop）。
- 切回当前版本：恢复 `state.plan`（reducer 里的始终是当前版本，未被污染）。
- **reducer 零改动**（本任务不动 `sessionReducer`）。
- `alert(...)` 两处 merge 文案全部移除，替换为抽屉；全 reject 场景抽屉照常打开（diff 为空、决策全 reject、顶部一行 "Plan unchanged — all comments rejected"）。

### 3.5 数据流

```
merge 成功 → App 存 prevPlan=state.plan → dispatch MERGE_COMPLETED
          → setDrawer({ result, prevPlan }) 打开抽屉
          → 抽屉内：决策列表 + diffPlans(prev.nodes, result.plan.nodes)
          → 用户关抽屉 → 正常 review 态（v{N+1}）

用户切版本 → viewingVersion=v → Promise.all(getPlan(v), listComments(v))
          → 编辑器只读渲染 v 版本文档 + 面板展示 v 版本评论
          → 切回 "current" → viewingVersion=null → 恢复 reducer 状态
```

## 4. 文件清单

**后端（13a）**

| 文件 | 动作 | 作用 |
|---|---|---|
| `backend/src/app/services/session_service.py` | 修改 | `list_plans` / `get_plan` 公开方法 |
| `backend/src/app/api/sessions.py` | 修改 | 2 个路由 + `PlanSummary`/`PlanListResponse` |
| `backend/tests/test_sessions_api.py`（或并入既有 API 测试文件） | 新增/修改 | API 测试 |
| `shared/schema.json` | 生成 | `make gen-schema` |

**前端（13b）**

| 文件 | 动作 | 作用 |
|---|---|---|
| `frontend/src/api/plans.ts` | 新增 | `listPlans` / `getPlan` |
| `frontend/src/editor/diff.ts` | 新增 | `diffPlans` 纯函数 |
| `frontend/src/editor/diff.test.ts` | 新增 | diff 单测 |
| `frontend/src/components/PlanDiffView.tsx` | 新增 | diff 行渲染（三色标记） |
| `frontend/src/components/MergeResultDrawer.tsx` | 新增 | 决策列表 + diff 抽屉 |
| `frontend/src/components/CommentThreadPanel.tsx` | 修改 | `readonly` prop（禁输入/禁 Apply） |
| `frontend/src/App.tsx` | 修改 | 抽屉装配、版本选择器、prevPlan、viewingVersion；移除 2 处 merge alert |
| `frontend/src/App.css` | 修改 | 抽屉/选择器/diff 标记样式 |
| 相应 `*.test.*` | 新增 | 见 §6 |

## 5. 实施步骤

**13a 后端**（1 commit）
1. `SessionService.list_plans` / `get_plan` + service 单测（复用既有 DB fixture 模式）
2. 2 个路由 + schema + API 测试（含 404 边界）
3. `make gen-schema`，确认 shared/schema.json 新增 2 schema
4. 后端全套回归

**13b 前端**（1 commit）
5. `api/plans.ts`
6. `diff.ts` + 单测（先写测试）
7. `PlanDiffView` + render 测试
8. `MergeResultDrawer`（决策列表 + diff + 全 reject 文案）
9. `CommentThreadPanel` readonly prop
10. App 装配：抽屉替换 alert、版本选择器、历史版本浏览；移除 2 处 `alert`
11. 前端全套回归（vitest + tsc + eslint）

**E2E 冒烟**（浏览器手工，不计 commit）：走一轮生成 → 评论 → merge → 验证抽屉与版本切换。

## 6. 测试清单

**后端**
- `list_plans`：无 plan 的 session → 空列表；多版本升序；不存在 session → 404
- `get_plan`：合法版本 200；越界版本 → `plan_version_not_found` 404；不存在 session → 404
- `PlanSummary.node_count` 与实际 nodes 数一致

**前端 `diff.test.ts`（纯函数，重点）**
- 全同 → 全 `unchanged`
- 纯新增 / 纯删除 / 中间插入
- 单节点文本改动 → `modified`
- 删除 + 修改混合（模拟真实 merge：删一步 + 改概述）
- 空旧/空新/双空
- 类型不同但文本相似 → 不匹配（`added`+`removed`）
- id 漂移场景：删 `step_001` 后 `step_002` 变 `step_001`，其余应 `unchanged` 而非全 `modified`

**前端组件**
- `PlanDiffView`：四种 kind 各自样式类存在
- `MergeResultDrawer`：渲染决策徽标 + reason；`plan_changed=false` 时显示 "Plan unchanged" 文案
- `CommentThreadPanel` readonly：输入框与 Apply 按钮不可用

**边缘情况**
- 版本选择器只在 `versions.length > 1` 且 review 态出现
- 浏览历史版本期间发生不了交互（只读），无并发状态问题
- 抽屉打开时 viewingVersion 强制为 null（抽屉与版本浏览互斥：打开抽屉时重置浏览态）

**集成入口**：`cd backend && uv run pytest`；`cd frontend && pnpm test && pnpm typecheck && pnpm lint`

## 7. 风险与未决

> 未决项需要主人决策的在 §8 列出；以下为技术风险。

| 风险 | 缓解 |
|---|---|
| LCS+相似度在极端重写（>半数节点改写）下可能误判 modified/增删 | MVP 接受；diff 是展示层辅助，不影响数据正确性；单测覆盖典型 merge 形态 |
| `getPlan` 拉历史大文档阻塞切换 | 文档量小（< 50 节点），不加 loading 态；后续按需 |
| 抽屉内 diff 与编辑器渲染两套节点视图，视觉不一致 | `PlanDiffView` 用精简文本摘要渲染（节点类型徽标 + 文本），不复用 Tiptap，保持抽屉轻量 |

## 8. 待主人决策（APPROVED 前）

> **决策结果（2026-08-25 主人拍板）**：三题全选接受。实施以此为准。
>
> 1. 不落 `.plan/v{N}.json` 文件：接受（前提：DB `plans` 表全量存储）。
> 2. 历史版本只读：接受。
> 3. diff 抽屉仅展示最近一次 merge：接受。

1. **持久化偏离 ROADMAP 字面**：ROADMAP 写 ".plan/v{N}.json 落盘"，但 `plans` 表已是全量持久化的事实源。本设计**不做文件系统双写**，版本历史全走 DB + API。是否接受此偏离？（推荐接受，双写徒增一致性负担）
2. **历史版本只读**：切回 v1 时不允许新增评论/答题/merge，仅浏览（评论与操作都绑定当前版本）。是否接受？（推荐接受；在旧版本留评论会让 merge 语义复杂化，留给 Task 14+ 再议）
3. **diff 抽屉仅展示最近一次 merge（v{N-1} → v{N}）**，不做任意两版本对比。是否接受？（推荐接受，控制边界；历史版本浏览已是正常全文渲染）
