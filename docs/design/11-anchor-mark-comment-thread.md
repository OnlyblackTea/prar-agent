# 11. AnchorMark + CommentThread (M2 Plan Review 入口)

> **状态**：APPROVED（Q1=A, Q2=A, Q3=A — 按推荐默认值）
> **依赖**：Task 02（`comments` 表已建）、Task 03（state_machine）、Task 09（前端骨架）、Task 10（Plan 渲染 + Session API + Tiptap 三节点）
> **被依赖**：Task 12（Review Merger 从 `comments` 读输入）、Task 13（diff 视图过滤历史评论）、Task 14（fuzzy anchor 回源算法）
> **commit 范围**：拆 2 个 commit（详见 §11）

---

## 0. M2 总体定位（开篇 context，非 Task 11 范围）

ROADMAP §41-50 定义的 M2 目标：**用户评论 → 真正改 plan → 看到 v1 → v2 diff**。

M2 四个 task **串行执行**：

| NN | 任务 | 产出 |
|----|------|------|
| **11** | AnchorMark + CommentThread + Comment 后端 CRUD | 用户选中文本 → 写评论 → 持久化；侧边栏可见评论列表 |
| 12 | Review Merger + `merger.md` prompt | comments + plan vN → plan vN+1，每条评论附 accept/reject/partial 理由 |
| 13 | Plan 版本管理 + 前端 diff 视图 | 节点级 diff UI |
| 14 | Anchor 回源算法 + "悬空评论" UI 状态 | 改 plan 后评论位置不丢 |

**为什么 Task 11 优先做**：评论数据是 12/13/14 三个任务的共同输入；没有 Task 11 落地的 `comments` 表数据，后续三个 task 无法独立验收。

---

## 1. 目标 — Task 11 一句话

打通 **用户在 PlanDocEditor 选中文本 → 浮窗输入评论 → POST 持久化 → 编辑器高亮锚点 + 侧边栏列出评论**，刷新页面后评论与锚点都能从 DB 回填。

### 1.1 验收 demo（人工 E2E）

1. M1 demo 跑完，session 进入 `plan_review` phase
2. 在 paragraph 节点内选中一段文字
3. BubbleMenu 弹出"Add Comment"按钮，点击
4. 侧边栏 `CommentThreadPanel` 弹输入框 → 输入 body → Submit
5. 验收：
   - DB `comments` 表新增 1 行，字段完整（含 anchor_id / quote / quote_context）
   - 编辑器中原选中文字呈黄色 `<mark>` 高亮
   - 侧边栏列出该评论
   - 刷新页面 → 评论与高亮 Mark 都还在（GET 拉取生效）
   - 切到 `acting` phase 试图加新评论 → 409 + 前端禁用按钮
6. 回归：M1 决策题答题流程不受影响

---

## 2. 现状缺口

| # | 现状 | Task 11 要补 |
|---|------|------------|
| P1 | `comments` 表已建（[models.py:129-150](../../backend/src/app/db/models.py)）但无任何调用 | 后端 service + API + Pydantic schema |
| P2 | Tiptap 编辑器 `editable: false`，无任何 Mark 实现 | 加 `AnchorMark`（自写，不依赖 extension-highlight） |
| P3 | 选中文本无 UI 入口 | 接入 `@tiptap/extension-bubble-menu`，selection 非空时显示"Add Comment" |
| P4 | 无评论侧边栏 | 新增 `CommentThreadPanel` 组件 + 输入表单 |
| P5 | `sessionReducer` 的 `review` 状态无 `comments` 字段 | 扩展 reducer + 新 actions |
| P6 | 进入 review 状态后无评论回填 | `WS_PLAN_DONE` 之后额外 GET `/comments?plan_version=` |
| P7 | shared schema 未含 Comment 类型 | `shared/schemas.py` + 重生成 `schema.json` |

---

## 3. 架构鸟瞰

```
┌────────────────────────────────────────────────────────────────────────┐
│                            Browser (前端)                              │
│                                                                        │
│  ┌──────────────────────────┐   ┌────────────────────────────────┐    │
│  │   PlanDocEditor          │   │   CommentThreadPanel (侧边栏)  │    │
│  │   ┌────────────────────┐ │   │   ┌──────────────────────────┐ │    │
│  │   │ Tiptap doc         │ │   │   │ 已有评论列表 (按 ts)     │ │    │
│  │   │  + AnchorMark      │◀┼───┼───│  点击 → highlight 锚点  │ │    │
│  │   │  + BubbleMenu      │ │   │   ├──────────────────────────┤ │    │
│  │   └─────────┬──────────┘ │   │   │ 新评论输入框 (弹出态)    │ │    │
│  └─────────────┼────────────┘   │   │  body + Submit           │ │    │
│                │ selection 非空  │   └─────────────┬────────────┘ │    │
│                ▼                 │                 │ POST          │    │
│        Click "Add Comment" ─────▶│                 ▼               │    │
│                                  │       fetch POST /comments      │    │
│                                  └─────────────────┬───────────────┘    │
└─────────────────────────────────────────────────────┼──────────────────┘
                                                      │
┌─────────────────────────────────────────────────────▼──────────────────┐
│                            Backend (FastAPI)                           │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  api/comments.py                                                 │  │
│  │    POST   /api/sessions/{sid}/comments                           │  │
│  │    GET    /api/sessions/{sid}/comments?plan_version=N            │  │
│  │    GET    /api/sessions/{sid}/comments/{cid}                     │  │
│  └────────────────────────────┬─────────────────────────────────────┘  │
│                               │ Depends(get_db)                        │
│  ┌────────────────────────────▼─────────────────────────────────────┐  │
│  │  services/comment_service.py — CommentService                    │  │
│  │   create / list_by_version / get                                 │  │
│  │   + 4 ValueError: session_not_found / invalid_plan_version /     │  │
│  │     phase_not_review / quote_not_found_in_plan                   │  │
│  └────────────────────────────┬─────────────────────────────────────┘  │
│                               │                                        │
│  ┌────────────────────────────▼─────────────────────────────────────┐  │
│  │       AsyncSession → comments / sessions / plans                 │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 4. 后端设计 (Task 11a)

### 4.1 Pydantic schemas — 新增 `app/core/comment_schemas.py`

```python
"""Comment API 数据契约。"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CommentCreate(BaseModel):
    """前端写入评论请求体。"""

    anchor_id: str = Field(min_length=1, max_length=64)
    plan_version: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=2000)
    quote_context: str = Field(default="", max_length=200)
    body: str = Field(min_length=1, max_length=4000)


class CommentResponse(BaseModel):
    """评论返回体。"""

    id: UUID
    session_id: UUID
    plan_version: int
    anchor_id: str
    quote: str
    quote_context: str
    body: str
    resolved: bool
    created_at: datetime

    model_config = {"from_attributes": True}
```

**与 DB 表对齐**：字段与 [`models.Comment`](../../backend/src/app/db/models.py)（line 129-150）1:1，**不需要新 alembic migration**。

### 4.2 Service — 新增 `app/services/comment_service.py`

```python
"""Comment CRUD + 写入前置校验。"""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.comment_schemas import CommentCreate
from app.core.logging import get_logger
from app.db.models import Comment, Plan, Session

_log = get_logger("comment_service")


class CommentNotFoundError(Exception):
    """评论不存在。"""


class CommentService:
    """评论持久化。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self, *, session_id: UUID, payload: CommentCreate
    ) -> Comment:
        """写入评论，含 4 道前置校验。"""
        session = await self._db.get(Session, session_id)
        if session is None:
            from app.services.session_service import SessionNotFoundError
            raise SessionNotFoundError(str(session_id))

        if payload.plan_version > session.current_plan_version:
            raise ValueError("invalid_plan_version")

        if session.phase != "plan_review":
            raise ValueError("phase_not_review")

        # quote sanity check：避免脏数据进库
        plan = await self._get_plan(session_id, payload.plan_version)
        if not _quote_in_plan(payload.quote, plan.document):
            raise ValueError("quote_not_found_in_plan")

        comment = Comment(
            session_id=session_id,
            plan_version=payload.plan_version,
            anchor_id=payload.anchor_id,
            quote=payload.quote,
            quote_context=payload.quote_context,
            body=payload.body,
        )
        self._db.add(comment)
        await self._db.flush()
        await self._db.refresh(comment)
        _log.info("comment_created",
                  comment_id=str(comment.id), session_id=str(session_id))
        return comment

    async def list_by_version(
        self, *, session_id: UUID, plan_version: int
    ) -> list[Comment]:
        """按版本列出评论，created_at 升序。"""
        stmt = (
            select(Comment)
            .where(Comment.session_id == session_id)
            .where(Comment.plan_version == plan_version)
            .order_by(Comment.created_at.asc())
        )
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def get(self, comment_id: UUID) -> Comment:
        c = await self._db.get(Comment, comment_id)
        if c is None:
            raise CommentNotFoundError(str(comment_id))
        return c

    async def _get_plan(self, session_id: UUID, version: int) -> Plan:
        stmt = (
            select(Plan)
            .where(Plan.session_id == session_id)
            .where(Plan.version == version)
        )
        result = await self._db.execute(stmt)
        plan = result.scalar_one_or_none()
        if plan is None:
            raise ValueError("invalid_plan_version")
        return plan


def _quote_in_plan(quote: str, document: dict) -> bool:
    """把 plan document 全部 text 拼起来判断 quote 是否子串。

    粗粒度但够用：Task 14 才上 fuzzy match。
    """
    nodes = document.get("nodes", [])
    full_text = "\n".join(_extract_text(n) for n in nodes)
    return quote in full_text


def _extract_text(node: dict) -> str:
    """从 plan node 抽 text 字段（heading/paragraph 有 text；其他节点取 question/term/description）。"""
    if "text" in node:
        return node["text"]
    parts = [node.get(k, "") for k in ("question", "term", "definition", "description")]
    return " ".join(p for p in parts if p)
```

**关键设计**：
- `_quote_in_plan` 用「全文拼接 + `in` 判断」，**不是** fuzzy match，**不是** 锚点回源（那是 Task 14）。它只防止前端送来根本不存在的 quote 进库。
- `_extract_text` 兼容五种 PlanNode 的不同字段，避免 LLM 改 schema 时这里挂掉（Task 11 范围内 schema 不变，但留点鲁棒性不亏）。

### 4.3 API — 新增 `app/api/comments.py`

```python
"""Comment CRUD 路由。"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.comment_schemas import CommentCreate, CommentResponse
from app.db.session import get_db
from app.services.comment_service import CommentNotFoundError, CommentService
from app.services.session_service import SessionNotFoundError

router = APIRouter(prefix="/api/sessions", tags=["comments"])


async def get_comment_service(
    db: AsyncSession = Depends(get_db),
) -> CommentService:
    return CommentService(db)


@router.post(
    "/{session_id}/comments",
    response_model=CommentResponse,
    status_code=201,
)
async def create_comment(
    session_id: UUID,
    payload: CommentCreate,
    service: CommentService = Depends(get_comment_service),
) -> CommentResponse:
    try:
        c = await service.create(session_id=session_id, payload=payload)
    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail="session_not_found") from e
    except ValueError as e:
        msg = str(e)
        status = 409 if msg == "phase_not_review" else 400
        raise HTTPException(status_code=status, detail=msg) from e
    return CommentResponse.model_validate(c)


@router.get(
    "/{session_id}/comments",
    response_model=list[CommentResponse],
)
async def list_comments(
    session_id: UUID,
    plan_version: int = Query(ge=1),
    service: CommentService = Depends(get_comment_service),
) -> list[CommentResponse]:
    comments = await service.list_by_version(
        session_id=session_id, plan_version=plan_version
    )
    return [CommentResponse.model_validate(c) for c in comments]


@router.get(
    "/{session_id}/comments/{comment_id}",
    response_model=CommentResponse,
)
async def get_comment(
    session_id: UUID,
    comment_id: UUID,
    service: CommentService = Depends(get_comment_service),
) -> CommentResponse:
    try:
        c = await service.get(comment_id)
    except CommentNotFoundError as e:
        raise HTTPException(status_code=404, detail="comment_not_found") from e
    if c.session_id != session_id:
        raise HTTPException(status_code=404, detail="comment_not_found")
    return CommentResponse.model_validate(c)
```

**注意**：
- 错误码遵循 M1-10 已建立的约定（`*_not_found` 全部 detail 字符串，无 i18n）
- `phase_not_review` 用 **409 Conflict**（业务状态冲突），其他 ValueError 用 **400 Bad Request**
- `from e` 全程保留 exception chain（M1-10 fixup 已立的纪律）

### 4.4 主入口注册 — 修改 `app/main.py`

加一行：
```python
from app.api import comments
...
app.include_router(comments.router)
```

### 4.5 shared schema 注册 — 修改 `shared/schemas.py`

把 `CommentCreate` / `CommentResponse` 加入 export 列表，跑 `make gen-schema` 重生成 `shared/schema.json`。

### 4.6 测试

`backend/tests/test_comment_service.py`（unit，~6 cases）：

| # | case | 期望 |
|---|------|------|
| 1 | session 不存在 → `SessionNotFoundError` | ✓ |
| 2 | `plan_version > current_plan_version` → `ValueError("invalid_plan_version")` | ✓ |
| 3 | session.phase = "acting" → `ValueError("phase_not_review")` | ✓ |
| 4 | quote 不在 plan 文本里 → `ValueError("quote_not_found_in_plan")` | ✓ |
| 5 | 正常写入 → 返回 Comment ORM 对象，字段齐 | ✓ |
| 6 | `list_by_version` 按 created_at 升序，过滤其他 session | ✓ |

`backend/tests/test_comments_api.py`（integration，~4 cases）：

| # | case | 期望 HTTP |
|---|------|---------|
| 1 | POST 正常 | 201 + body |
| 2 | POST phase=acting | 409 + detail "phase_not_review" |
| 3 | GET 列表（指定 plan_version） | 200 + 数组 |
| 4 | GET 单条不存在 | 404 |

---

## 5. 前端设计 (Task 11b)

### 5.1 新依赖

`frontend/package.json` 加：
```json
"@tiptap/extension-bubble-menu": "^2.10.0"
```

版本对齐 `@tiptap/react` 现有版本。

### 5.2 AnchorMark — 新增 `frontend/src/editor/marks/AnchorMark.ts`

```ts
import { Mark, mergeAttributes } from '@tiptap/react'

export interface AnchorAttrs {
  anchor_id: string
  resolved: boolean
}

export const AnchorMark = Mark.create<AnchorAttrs>({
  name: 'anchor',
  inclusive: false, // 不要把 selection 后新输入的字符也染上

  addAttributes() {
    return {
      anchor_id: { default: '' },
      resolved: { default: false },
    }
  },

  parseHTML() {
    return [{ tag: 'mark[data-anchor-id]' }]
  },

  renderHTML({ HTMLAttributes }) {
    return [
      'mark',
      mergeAttributes(HTMLAttributes, {
        class: 'prar-anchor',
        'data-anchor-id': HTMLAttributes.anchor_id,
        'data-resolved': String(HTMLAttributes.resolved),
      }),
      0,
    ]
  },
})
```

**关键决策**：
- 自写而非用 `@tiptap/extension-highlight`：后者无自定义属性，我们需要 `anchor_id` + `resolved`
- `inclusive: false`：避免用户后续在锚点旁打字（其实 editable: false 不会发生，但留这个语义更稳）
- 不写 `addCommands`：所有 Mark 落点通过 `editor.view.dispatch(tr.addMark(...))` 底层 API 触发，**绕过 Tiptap commands 对 editable 的检查**

### 5.3 锚点样式 — 新增 `frontend/src/editor/marks/anchor.css`

```css
.prar-anchor {
  background: #fff3a0;
  padding: 0 2px;
  border-radius: 2px;
  cursor: pointer;
  transition: background 120ms;
}
.prar-anchor:hover { background: #ffe066; }
.prar-anchor[data-resolved="true"] { background: #d4edda; }
```

在 `frontend/src/App.tsx` 或全局 entry 处 import。

### 5.4 PlanDocEditor 改造 — 修改 [`PlanDocEditor.tsx`](../../frontend/src/editor/PlanDocEditor.tsx)

**保持 `editable: false`**。理由：ProseMirror 在 readonly 模式下仍允许 selection，只禁止 typing；我们要的就是这种"可选不可改"的语义。

新增 prop：
```ts
interface PlanDocEditorProps {
  initialContent?: string
  doc?: JSONContent
  onRequestAddComment?: (sel: SelectionSnapshot) => void
}

export interface SelectionSnapshot {
  from: number
  to: number
  quote: string
  quoteContext: string  // 前后各 50 字符
}
```

接入 BubbleMenu：

```tsx
import BubbleMenu from '@tiptap/extension-bubble-menu'

const editor = useEditor({
  extensions: [StarterKit, DecisionNode, GlossaryNode, StepNode, AnchorMark],
  content: doc ?? initialContent,
  editable: false,
})

// selection change 时通过 React state 决定是否展示 button
const handleAdd = () => {
  if (!editor || !onRequestAddComment) return
  const { from, to } = editor.state.selection
  if (from === to) return
  const quote = editor.state.doc.textBetween(from, to, '\n')
  const ctxStart = Math.max(0, from - 50)
  const ctxEnd = Math.min(editor.state.doc.content.size, to + 50)
  const quoteContext = editor.state.doc.textBetween(ctxStart, ctxEnd, '\n')
  onRequestAddComment({ from, to, quote, quoteContext })
}

return (
  <div className="plan-doc-editor" data-testid="plan-doc-editor">
    {editor && (
      <BubbleMenuPlugin editor={editor}>
        <button onClick={handleAdd}>Add Comment</button>
      </BubbleMenuPlugin>
    )}
    <EditorContent editor={editor} />
  </div>
)
```

> 具体 BubbleMenu 接入语法以 `@tiptap/extension-bubble-menu` 2.x 文档为准；实施时若发现 API 不同（如改名 BubbleMenu React 包装），允许微调，不算偏离设计。

**Mark 落点 helper**（暴露给 App.tsx 调用，落点用底层 dispatch 绕过 editable 锁）：

```ts
export function applyAnchorMark(
  editor: Editor,
  from: number,
  to: number,
  attrs: AnchorAttrs,
): void {
  const tr = editor.state.tr.addMark(
    from,
    to,
    editor.state.schema.marks.anchor.create(attrs),
  )
  editor.view.dispatch(tr)
}
```

### 5.5 CommentThreadPanel — 新增 `frontend/src/components/CommentThreadPanel.tsx`

```tsx
import { useState } from 'react'
import type { CommentResponse } from '@/types/shared'

interface CommentThreadPanelProps {
  comments: CommentResponse[]
  pendingSelection: SelectionSnapshot | null
  onCancel: () => void
  onSubmit: (body: string) => Promise<void>
  onJumpToAnchor: (anchorId: string) => void
}

export function CommentThreadPanel({
  comments,
  pendingSelection,
  onCancel,
  onSubmit,
  onJumpToAnchor,
}: CommentThreadPanelProps) {
  const [body, setBody] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async () => {
    if (!body.trim()) return
    setSubmitting(true)
    try {
      await onSubmit(body.trim())
      setBody('')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <aside className="comment-panel" data-testid="comment-panel">
      <h3>Comments</h3>
      {pendingSelection && (
        <div className="comment-new">
          <blockquote>{pendingSelection.quote}</blockquote>
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="Leave a comment..."
            maxLength={4000}
          />
          <div className="actions">
            <button onClick={onCancel} disabled={submitting}>Cancel</button>
            <button onClick={handleSubmit} disabled={submitting || !body.trim()}>
              Submit
            </button>
          </div>
        </div>
      )}
      <ul className="comment-list">
        {comments.map((c) => (
          <li key={c.id} onClick={() => onJumpToAnchor(c.anchor_id)}>
            <blockquote>{c.quote}</blockquote>
            <p>{c.body}</p>
            <time>{new Date(c.created_at).toLocaleString()}</time>
          </li>
        ))}
      </ul>
    </aside>
  )
}
```

**职责清单**：
- 渲染评论列表（按 created_at 升序，与后端一致）
- 渲染"新评论"输入区（仅在 `pendingSelection !== null` 时显示）
- 点击列表项 → 调 `onJumpToAnchor` 让父组件在 editor 中跳转 + 高亮
- `Submit` 按钮 disable 条件：submitting 中 / body 空白

**职责外**（M2 不做）：
- 评论编辑 / 删除
- 标记 resolved
- 评论作者头像 / 时间相对显示（"5 minutes ago"）

### 5.6 API client — 新增 `frontend/src/api/comments.ts`

```ts
import type { CommentResponse } from '@/types/shared'

export interface CreateCommentBody {
  anchor_id: string
  plan_version: number
  quote: string
  quote_context: string
  body: string
}

export async function createComment(
  sessionId: string,
  payload: CreateCommentBody,
): Promise<CommentResponse> {
  const res = await fetch(`/api/sessions/${sessionId}/comments`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail ?? `comment_create_failed_${res.status}`)
  }
  return (await res.json()) as CommentResponse
}

export async function listComments(
  sessionId: string,
  planVersion: number,
): Promise<CommentResponse[]> {
  const res = await fetch(
    `/api/sessions/${sessionId}/comments?plan_version=${planVersion}`,
  )
  if (!res.ok) throw new Error(`comment_list_failed_${res.status}`)
  return (await res.json()) as CommentResponse[]
}
```

### 5.7 sessionReducer 改造 — 修改 [`sessionReducer.ts`](../../frontend/src/state/sessionReducer.ts)

**仅** 在 `review` 状态加 `comments` 字段：

```ts
| { status: 'review'; sessionId: string; plan: PlanDocument; comments: CommentResponse[] }
```

新增 actions：

| Action | payload | 触发位置 |
|--------|---------|---------|
| `LOAD_COMMENTS` | `{ comments: CommentResponse[] }` | `WS_PLAN_DONE` 之后立即 GET，结果 dispatch |
| `ADD_COMMENT` | `{ comment: CommentResponse }` | `createComment` 成功后 |

reducer 逻辑：
- `LOAD_COMMENTS`：只在 `status === 'review'` 时生效，替换整个 comments 数组
- `ADD_COMMENT`：只在 `status === 'review'` 时生效，append 到末尾

**进入 review 时的初始值**：`comments: []`（在 `WS_PLAN_DONE` 转 review 时初始化，待 `LOAD_COMMENTS` 填充）。

### 5.8 App.tsx 装配 — 修改 [`App.tsx`](../../frontend/src/App.tsx)

伪代码：

```tsx
const editorRef = useRef<Editor | null>(null)
const [pendingSel, setPendingSel] = useState<SelectionSnapshot | null>(null)

// 进入 review 状态时拉评论
useEffect(() => {
  if (state.status !== 'review') return
  listComments(state.sessionId, state.plan.version).then((comments) => {
    dispatch({ type: 'LOAD_COMMENTS', comments })
  })
}, [state.status])

const handleRequestAddComment = (sel: SelectionSnapshot) => {
  setPendingSel(sel)
}

const handleSubmitComment = async (body: string) => {
  if (!pendingSel || state.status !== 'review') return
  const anchor_id = crypto.randomUUID().replace(/-/g, '').slice(0, 16)
  const comment = await createComment(state.sessionId, {
    anchor_id,
    plan_version: state.plan.version,
    quote: pendingSel.quote,
    quote_context: pendingSel.quoteContext,
    body,
  })
  // 写入成功后才落 Mark（悲观流程）
  if (editorRef.current) {
    applyAnchorMark(editorRef.current, pendingSel.from, pendingSel.to, {
      anchor_id,
      resolved: false,
    })
  }
  dispatch({ type: 'ADD_COMMENT', comment })
  setPendingSel(null)
}

const handleJumpToAnchor = (anchorId: string) => {
  // 找 Mark 位置，setTextSelection + scroll
  if (!editorRef.current) return
  const { doc } = editorRef.current.state
  let pos: { from: number; to: number } | null = null
  doc.descendants((node, p) => {
    node.marks.forEach((m) => {
      if (m.type.name === 'anchor' && m.attrs.anchor_id === anchorId) {
        pos = { from: p, to: p + node.nodeSize }
      }
    })
  })
  if (pos) {
    editorRef.current.commands.setTextSelection(pos)
    editorRef.current.commands.scrollIntoView()
  }
}
```

布局：左侧 `PlanDocEditor`，右侧 `CommentThreadPanel`，两栏 flex 布局。

### 5.9 LOAD_COMMENTS 后批量回放 Mark

进入已有评论的 session 时，需要把所有 comment 的锚点 Mark 重新应用到 editor。简单策略：

```ts
useEffect(() => {
  if (state.status !== 'review' || !editorRef.current) return
  for (const c of state.comments) {
    const range = findRangeByQuote(editorRef.current.state.doc, c.quote)
    if (range) {
      applyAnchorMark(editorRef.current, range.from, range.to, {
        anchor_id: c.anchor_id,
        resolved: c.resolved,
      })
    }
  }
}, [state.status, state.comments.length])
```

`findRangeByQuote` 暂时用首次出现 `indexOf` 实现（M2 Task 14 才上 fuzzy match）：

```ts
function findRangeByQuote(doc: Node, quote: string): { from: number; to: number } | null {
  let result: { from: number; to: number } | null = null
  doc.descendants((node, pos) => {
    if (!node.isText || !node.text) return
    const idx = node.text.indexOf(quote)
    if (idx >= 0 && !result) {
      result = { from: pos + idx, to: pos + idx + quote.length }
    }
  })
  return result
}
```

**Task 11 的局限**：跨节点 quote 在这版 `findRangeByQuote` 里会失败（找不到则 Mark 不回放）。**这是已知缺陷，留给 Task 14 解决**——Task 11 验收时人工 demo 限定单节点 quote 即可。

---

## 6. 数据流（端到端）

```
[用户在 PlanDocEditor 选中一段文字]
         ↓ Tiptap selection update
[BubbleMenu 出现 "Add Comment" 按钮]
         ↓ 用户点击
[onRequestAddComment(sel) → App setPendingSel]
         ↓
[CommentThreadPanel 出现输入框（pendingSel 非空）]
         ↓ 用户输入 body → Submit
[App.handleSubmitComment]
   1. 生成 anchor_id = randomUUID().slice(0, 16)
   2. createComment(sessionId, {anchor_id, plan_version, quote, quote_context, body})
         ↓ POST /api/sessions/{sid}/comments
[CommentService.create]
   - session 存在? phase==plan_review? quote in plan?
   - INSERT comments row
         ↓ 201 + CommentResponse
[App]
   3. applyAnchorMark(editor, sel.from, sel.to, {anchor_id, resolved:false})
   4. dispatch ADD_COMMENT
   5. setPendingSel(null)
         ↓
[侧边栏出现新条目；编辑器中原文字高亮]
```

---

## 7. 边界情况

| 边界 | 处理 |
|------|------|
| 选中文本跨多个 inline 节点（含 GlossaryNode） | `textBetween` 自动跳过非 text 内容；后端 `_quote_in_plan` 同步拼接判断；视觉上可能锚点不连续，M2 不优化 |
| 选中文本跨多个 block 节点 | `textBetween` 用 `\n` 分隔；后端拼接也用 `\n`；能通过 sanity check |
| 同一段文本被多次评论 | 允许；多个 Mark 叠加，CSS 不做特殊处理（M2 不优化）|
| 评论 body 含恶意 HTML | 永远 `{c.body}` 渲染，禁用 `dangerouslySetInnerHTML` |
| 后端写入成功但前端 dispatch 之前页面崩溃 | 评论已落库，refresh 后 LOAD_COMMENTS 回放 Mark — 自愈 |
| `planning` phase 用户尝试选中 | BubbleMenu 在 `status !== 'review'` 时不渲染（App 侧根据 state 决定是否挂 BubbleMenu）|
| `acting` phase 后用户仍想加评论 | BubbleMenu 隐藏；万一前端绕过，后端返 409 |
| quote 超过 2000 字符 | 前端在 handleAdd 时检查长度，超长 disable 按钮 + tooltip |
| selection 跨进了已有 anchor 区域 | M2 不做特殊处理，允许重叠 Mark；Task 14 视觉上可优化 |
| anchor_id 哈希碰撞 | 16 字符 hex ≈ 64 bit，单 session 内碰撞概率极低；不做唯一性约束 |

---

## 8. 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| Mark vs Node 实现锚点 | **Mark** | 锚点是 inline 装饰，不引入新节点类型；可叠加在任意 text 上 |
| 自写 AnchorMark vs `extension-highlight` | **自写** | 后者无自定义属性，需要 fork；自写仅 30 行 |
| editable: true vs false | **保持 false** | readonly 仍允许 selection；防止用户改 Node 内容；Mark 落点走底层 dispatch 绕过 editable 锁 |
| 评论提交：乐观 vs 悲观 | **悲观**（POST 成功才落 Mark） | 避免后端校验失败（如 phase_not_review）时 Mark 已落需要回滚的复杂性 |
| anchor_id 生成方 | **前端 crypto.randomUUID().slice(0,16)** | 不依赖后端往返；落 Mark 与 POST 用同一 ID |
| anchor_id 类型 | **String(64)** | DB 已建 String(64)；M2 不动 schema |
| quote 校验位置 | **后端 + 粗粒度** | 防御性 sanity check；不是 Task 14 的 fuzzy match |
| 历史评论回放方式 | **进 review 时 GET + indexOf 找位置** | M2 简化版；Task 14 替换为 fuzzy match |
| 评论编辑/删除 | **不做** | YAGNI；评论 immutable，错了再加新评论 |
| Comment.resolved 字段 UI 操作 | **不做** | 留给 Task 12 Merger 自动标记 |
| 跨节点 quote | **已知缺陷不修** | Task 14 范围；Task 11 验收限单节点 quote |

---

## 9. 复用清单

| 用途 | 已有可复用 | 路径 |
|------|---------|------|
| Session 读取 | `SessionService.get` | `backend/src/app/services/session_service.py` |
| Phase 字段 | `Session.phase` 字符串 | `backend/src/app/db/models.py:79` |
| DB session DI | `Depends(get_db)` | `backend/src/app/db/session.py` |
| structlog logger | `get_logger(name)` | `backend/src/app/core/logging.py` |
| HTTPException + `from e` 模式 | 参考 | `backend/src/app/api/sessions.py:92-156` |
| 前端 reducer 模式 | useReducer + Context | `frontend/src/state/sessionReducer.ts` |
| Tiptap 扩展参考 | DecisionNode (Node) | `frontend/src/editor/nodes/DecisionNode.tsx` |
| HTTPException 状态约定 | 404/409/400 已立 | M1-10 |

---

## 10. 测试 & 验收

### 10.1 后端

```bash
cd backend && uv run ruff check src tests
cd backend && uv run pytest tests/test_comment_service.py tests/test_comments_api.py -v
cd backend && uv run pytest -m "not smoke"  # 全量回归，M1 的 126 测试 + Task 11 新增 ≈ 136 全绿
```

### 10.2 前端

```bash
cd frontend && pnpm typecheck
cd frontend && pnpm build
```

### 10.3 人工 E2E（§1.1 demo 流程）

附加冒烟项：
- 选中跨节点文本 → BubbleMenu 仍出现 → POST 成功，但**已知 Mark 回放可能失败**（Task 14 修）
- 在 `acting` phase 篡改前端绕过 BubbleMenu → 后端 409 → 前端报错 toast

---

## 11. Commit 拆分

| commit | 范围 | 标题模板 |
|--------|------|---------|
| 1 | Task 11a 后端（schema + service + api + tests + shared）+ 本设计文档 | `feat(backend): comment CRUD api + schema + tests (M2-11a)` |
| 2 | Task 11b 前端（AnchorMark + BubbleMenu + Panel + reducer + App 装配） | `feat(frontend): AnchorMark + CommentThreadPanel (M2-11b)` |

**纪律重申**：本设计文档与 commit 1 **同 commit**，遵守 [WORKFLOW.md](../WORKFLOW.md) 约定的"设计 + 实施同 commit"。

---

## 12. M2 后续展望（非本 task）

- **Task 12 Review Merger**：把本 task 持久化的 `comments` 表作为 LLM 输入，产生 `plan vN+1` + 每条评论的 accept/reject/partial 标记。会引入 `merger.md` prompt 与 `ReviewMerger` 类。
- **Task 13 版本管理**：扩展前端 reducer 支持版本切换；新增 `GET /api/sessions/{sid}/plans/{version}` endpoint；diff 视图组件。
- **Task 14 Anchor 鲁棒性**：把 §5.9 的 `findRangeByQuote` 升级成 `quote + quote_context` fuzzy match；命中率 <0.7 → 「悬空评论」UI 状态。

---

## 13. 待主人决策（开工前）

以下 3 个非阻塞但需主人拍板的细节，回答后即可标 APPROVED 进入实施：

1. **quote sanity check 是否真要做（§4.2 第 4 道校验）**？
   - A. 做（推荐，防脏数据，但要求前后端 `_extract_text` 完全一致）
   - B. 不做（信任前端，省 ~30 行代码 + 1 个 unit test）

2. **BubbleMenu 触发条件**：仅 paragraph/heading 节点内选中 vs 任何 inline 内容选中？
   - A. 任何 inline 都允许（推荐，包括 GlossaryNode 内文字）
   - B. 仅限 paragraph/heading（更克制，但需要 Tiptap selection 节点类型判断）

3. **`acting` phase 后已有 anchor 高亮**：保留 vs 淡化？
   - A. 保留黄色（推荐，让用户随时能看到历史评论位置）
   - B. 淡化为灰色（暗示「review 已结束」）
