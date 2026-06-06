# 10. Plan 文档渲染 + Decision 答题闭环 (M1 Demo)

> **状态**：DRAFT，待 APPROVED
> **依赖**：Task 02（DB schema）、Task 03（state_machine）、Task 04.1（adapter）、Task 07（plan_engine）、Task 08（WS 协议）、Task 09（前端骨架）
> **被依赖**：M2 Plan Review 循环
> **commit 范围**：拆 2-3 个 commit（详见 §13）

---

## 1. 目标 — M1 Demo

- **一句话**：打通 init_request → WS 流式接收 plan → 前端 Tiptap 三种自定义节点渲染 → 用户答 blocking 决策题 → 持久化到 DB → 所有 blocking 答完后"进入 Action"按钮可点。
- **M1 demo 验收**（ROADMAP M1 验收原文）：
  1. 在 UI 输入"实现一个 X 功能"
  2. 实时流式看到完整 plan（含名词解释、决策题、步骤）
  3. 答完所有 blocking 决策题后"进入 Action"按钮可点
  4. 全程日志可查（request_id 串起 LLM call + state transitions）

---

## 2. 现状缺口

| # | 现状 | Task 10 要补 |
|---|------|------------|
| P1 | `ws_plan._resolve_dependencies` 抛 NotImplementedError | 补 DB session middleware + adapter resolve + PlanEngine 构造 |
| P2 | 无 Session CRUD | 创建/查询 session（init_request + adapter_id → phase=PLANNING） |
| P3 | Plan 不持久化（PlanEngine 生成完即返） | 流式推完后落 DB `plans` 表 |
| P4 | 决策题答案无入口 | HTTP `POST /api/sessions/{sid}/decisions/{dec_id}` |
| P5 | 无"进入 Action"判定 | 检查当前 plan 所有 blocking decisions 是否 answered → 允许 PLAN_REVIEW → ACTING |
| P6 | 前端 `PlanDocEditor` 只渲染 StarterKit | 加 Decision / Glossary / Step 三个自定义节点 |
| P7 | 前端 `api/ws.ts` 只有类型 | 真正连 WS + 事件 dispatcher |
| P8 | 前端无状态管理 | useReducer 管 plan + 答题状态 |
| P9 | 前端无答题 UI | radio/checkbox + 提交按钮 + 调 POST API |
| P10 | 前端无 App 主界面 | init_request 输入 → 触发 WS → 展示 plan + Action 按钮 |

---

## 3. 架构鸟瞰

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              Browser (前端)                              │
│  ┌───────────────┐  ┌──────────────────┐  ┌────────────────────────┐    │
│  │ InitRequest   │  │  PlanDocEditor   │  │ Decision 答题 UI       │    │
│  │  + 选 adapter │──▶│  Tiptap 三节点  │  │ + 提交 + ActionBtn     │    │
│  └───────┬───────┘  └─────────▲────────┘  └───────────┬────────────┘    │
│          │                    │                       │                 │
│          │ WS connect        │ dispatch              │ HTTP POST       │
│          ▼                    │                       ▼                 │
└──────────┼────────────────────┼───────────────────────┼─────────────────┘
           │                    │                       │
┌──────────▼────────────────────┴───────────────────────▼─────────────────┐
│                              Backend (FastAPI)                          │
│  ┌────────────────┐   ┌─────────────────┐   ┌─────────────────────┐    │
│  │ POST /sessions │   │ WS /sessions/   │   │ POST .../decisions  │    │
│  │  (create)      │   │   {sid}/plan    │   │   /{dec_id}         │    │
│  └───────┬────────┘   └────────┬────────┘   └──────────┬──────────┘    │
│          │                     │                       │               │
│  ┌───────▼─────────────────────▼───────────────────────▼──────────┐    │
│  │              SessionService + AdapterService                  │    │
│  │              PlanEngine (Task 07)                              │    │
│  └───────┬─────────────────────┬───────────────────────┬──────────┘    │
│          │                     │                       │               │
│  ┌───────▼─────────────────────▼───────────────────────▼──────────┐    │
│  │          AsyncSession (per-request DB session middleware)      │    │
│  └───────┬─────────────────────┬───────────────────────┬──────────┘    │
└──────────┼─────────────────────┼───────────────────────┼───────────────┘
           │                     │                       │
           ▼                     ▼                       ▼
        sessions             plans (JSONB)        plans.document.nodes[*].answer
```

---

## 4. 后端设计 (Task 10a)

### 4.1 DB Session 中间件 — 新增 `app/db/session.py`

```python
"""每请求一个 AsyncSession，FastAPI Depends 注入。"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _sessionmaker


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency. 每请求一个 session，自动 commit / rollback / close。"""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

> ⚠️ 决策点 Q1：DB 连接池策略
> A=lazy init + module-level singleton（推荐，零配置）/ B=lifespan event 显式管理 / C=每请求 create_async_engine（不可行，开销大）

### 4.2 `AdapterService.get_adapter_service` Dependency — 改造 4.1b 占位

```python
# api/adapters.py
async def get_adapter_service(
    db: AsyncSession = Depends(get_db),
) -> AdapterService:
    return AdapterService(db)
```

> 4.1b 设计文档原话："DB session middleware 建好后接" — 现在接。

### 4.3 新增 `app/services/session_service.py`

```python
class SessionService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self, *, init_request: str, adapter_id: UUID,
    ) -> models.Session:
        s = models.Session(
            init_request=init_request,
            adapter_id=adapter_id,
            phase=Phase.PLANNING.value,  # 创建即进入 PLANNING（INIT→PLANNING）
        )
        self._db.add(s)
        await self._db.flush()
        return s

    async def get(self, session_id: UUID) -> models.Session: ...

    async def save_plan(
        self, *, session_id: UUID, plan: PlanDocument,
    ) -> models.Plan:
        """plan 生成完写入 plans 表，version 自增，sessions.current_plan_version += 1。"""
        s = await self.get(session_id)
        new_version = s.current_plan_version + 1
        p = models.Plan(
            session_id=session_id,
            version=new_version,
            document=plan.model_dump(),
        )
        self._db.add(p)
        s.current_plan_version = new_version
        s.phase = Phase.PLAN_REVIEW.value  # PLANNING → PLAN_REVIEW
        await self._db.flush()
        return p

    async def get_current_plan(self, session_id: UUID) -> models.Plan: ...

    async def answer_decision(
        self, *, session_id: UUID, decision_id: str, answer: str,
    ) -> bool:
        """更新 plans.document.nodes[?].answer 字段。返回 all_blocking_answered。"""
        plan = await self.get_current_plan(session_id)
        doc = dict(plan.document)
        nodes = doc["nodes"]
        found = False
        for n in nodes:
            if n.get("type") == "decision" and n.get("id") == decision_id:
                # 校验 answer 在 options 中
                if answer not in n["options"]:
                    raise ValueError(f"answer {answer!r} not in options")
                n["answer"] = answer
                found = True
                break
        if not found:
            raise ValueError(f"decision {decision_id!r} not found")
        plan.document = doc
        await self._db.flush()
        return self._all_blocking_answered(nodes)

    @staticmethod
    def _all_blocking_answered(nodes: list[dict]) -> bool:
        for n in nodes:
            if (n.get("type") == "decision"
                    and n.get("blocking") and n.get("answer") is None):
                return False
        return True

    async def advance_to_acting(self, session_id: UUID) -> models.Session:
        """PLAN_REVIEW → ACTING；校验所有 blocking 已答。"""
        s = await self.get(session_id)
        plan = await self.get_current_plan(session_id)
        if not self._all_blocking_answered(plan.document["nodes"]):
            raise ValueError("not all blocking decisions answered")
        new_phase = transition(
            Phase(s.phase), Phase.ACTING, session_id=str(session_id),
        )
        s.phase = new_phase.value
        await self._db.flush()
        return s
```

### 4.4 新增 `app/api/sessions.py`

| Method | Path | 用途 | 状态码 |
|--------|------|------|--------|
| POST | `/api/sessions` | 创建 session（接受 init_request + adapter_id） | 201 |
| GET | `/api/sessions/{sid}` | 查 session（含 phase、current_plan_version） | 200 |
| GET | `/api/sessions/{sid}/plan` | 查当前 plan（用于刷新页面恢复） | 200 |
| POST | `/api/sessions/{sid}/decisions/{dec_id}` | 答 decision，body `{"answer": "..."}` | 200，返回 `{all_blocking_answered: bool}` |
| POST | `/api/sessions/{sid}/advance-to-acting` | 触发 PLAN_REVIEW → ACTING | 200 |

错误码：

| 场景 | 状态 | code |
|------|------|------|
| session 不存在 | 404 | `session_not_found` |
| decision id 不在当前 plan | 404 | `decision_not_found` |
| answer 不在 options | 400 | `answer_invalid` |
| 还有 blocking 未答就 advance | 409 | `blocking_unanswered` |
| phase 非法转移（如非 PLAN_REVIEW 状态调 advance） | 409 | `illegal_phase_transition` |

### 4.5 `ws_plan._resolve_dependencies` 填实

```python
async def _resolve_dependencies(
    session_id: uuid.UUID,
    adapter_id: uuid.UUID,
    db: AsyncSession,
    router: LLMRouter,
) -> tuple[ResolvedAdapter, PlanEngine]:
    adapter_service = AdapterService(db)
    db_adapter = await adapter_service.get(adapter_id)  # 可能抛 AdapterNotFoundError
    resolved = adapter_service.resolve(db_adapter)
    plan_engine = PlanEngine(router)
    return resolved, plan_engine
```

WS endpoint 需要拿到 `AsyncSession`，starlette 的 WS 不走 HTTP middleware，所以需要在 WS handler 内手动 enter sessionmaker：

```python
@router.websocket("/sessions/{session_id}/plan")
async def plan_websocket(websocket: WebSocket, session_id: uuid.UUID) -> None:
    ...
    async with get_sessionmaker()() as db:
        try:
            resolved, plan_engine = await _resolve_dependencies(
                session_id, msg.adapter_id, db, get_router(),
            )
            plan = await plan_engine.generate(...)
            # plan 完成后落 DB
            session_service = SessionService(db)
            await session_service.save_plan(session_id=session_id, plan=plan)
            await db.commit()
        except ...: ...
        # 然后 stream
        async for event in stream_plan(plan, str(session_id)):
            await websocket.send_json(event)
```

> ⚠️ 决策点 Q2：plan 持久化时机
> A=stream 前持久化（推荐，前端断线也不丢）/ B=stream 完成后持久化 / C=每个节点一条 stream + persist

### 4.6 LLMRouter 单例

```python
# app/main.py 或 app/llm/__init__.py
@lru_cache(maxsize=1)
def get_router() -> LLMRouter:
    return LLMRouter(get_settings())
```

WS 和 HTTP 共享同一个 router（含客户端缓存）。

---

## 5. 前端设计 (Task 10b)

### 5.1 状态机

```typescript
// src/state/sessionReducer.ts
type SessionState =
  | { status: 'idle' }
  | { status: 'connecting'; sessionId: string }
  | { status: 'streaming'; sessionId: string; plan: PartialPlan }
  | { status: 'review'; sessionId: string; plan: PlanDocument }
  | { status: 'error'; code: string; message: string }

interface PartialPlan { title: string; summary: string; nodes: PlanNode[] }
```

reducer 处理：
- `START_SESSION` → connecting
- `WS_PLAN_START` → streaming + 初始化 title/summary
- `WS_PLAN_NODE` → streaming + 追加节点
- `WS_PLAN_DONE` → review
- `WS_ERROR` → error
- `ANSWER_DECISION` → 更新对应节点 answer
- `RESET` → idle

### 5.2 Tiptap 自定义节点

三个节点用 Tiptap 的 `Node.create()` + React `NodeViewWrapper`：

**`src/editor/nodes/DecisionNode.tsx`**

```typescript
export const DecisionNode = Node.create({
  name: 'decision',
  group: 'block',
  atom: true,  // 不允许内部光标
  addAttributes() {
    return { id: { default: '' }, question: { default: '' }, ... }
  },
  parseHTML() { return [{ tag: 'div[data-type="decision"]' }] },
  renderHTML({ HTMLAttributes }) {
    return ['div', { ...HTMLAttributes, 'data-type': 'decision' }]
  },
  addNodeView() {
    return ReactNodeViewRenderer(DecisionView)
  },
})

// React view component
function DecisionView({ node }: NodeViewProps) {
  const { id, question, kind, options, answer, blocking } = node.attrs
  const { sessionId, dispatch } = useSession()  // context

  const handleSelect = async (choice: string) => {
    const res = await fetch(`/api/sessions/${sessionId}/decisions/${id}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ answer: choice }),
    })
    if (res.ok) dispatch({ type: 'ANSWER_DECISION', id, answer: choice })
  }

  return (
    <NodeViewWrapper className="decision-node">
      <div className="question">{question} {blocking && <span className="badge">必选</span>}</div>
      <div className="options">
        {options.map((opt: string) => (
          <label key={opt}>
            <input
              type="radio"
              name={id}
              value={opt}
              checked={answer === opt}
              onChange={() => handleSelect(opt)}
            />
            {opt}
          </label>
        ))}
      </div>
    </NodeViewWrapper>
  )
}
```

**`GlossaryNode.tsx`** — inline 名词解释 hover 卡片：

```typescript
export const GlossaryNode = Node.create({
  name: 'glossary',
  group: 'inline',
  inline: true,
  atom: true,
  addAttributes() { return { term: {default:''}, definition: {default:''} } },
  addNodeView() { return ReactNodeViewRenderer(GlossaryView) },
})

function GlossaryView({ node }: NodeViewProps) {
  return (
    <NodeViewWrapper as="span" className="glossary-term" title={node.attrs.definition}>
      {node.attrs.term}
    </NodeViewWrapper>
  )
}
```

**`StepNode.tsx`** — step 卡片：

```typescript
function StepView({ node }: NodeViewProps) {
  const { id, title, description, tool, tool_args, rerunnable } = node.attrs
  return (
    <NodeViewWrapper className="step-node">
      <header><strong>{title}</strong> <code>{tool}</code></header>
      <p>{description}</p>
      <pre>{JSON.stringify(tool_args, null, 2)}</pre>
    </NodeViewWrapper>
  )
}
```

> ⚠️ 决策点 Q3：是否引入 hover 卡片库（floating-ui / radix-ui）
> A=否（M1 用原生 title attr）/ B=引入 floating-ui

### 5.3 PlanDocument → Tiptap doc 转换

```typescript
// src/editor/serializer.ts
export function planToTiptapDoc(plan: PlanDocument | PartialPlan): JSONContent {
  return {
    type: 'doc',
    content: plan.nodes.map(nodeToTiptap),
  }
}

function nodeToTiptap(node: PlanNode): JSONContent {
  switch (node.type) {
    case 'heading':
      return { type: 'heading', attrs: { level: node.level }, content: [{ type: 'text', text: node.text }] }
    case 'paragraph':
      return { type: 'paragraph', content: [{ type: 'text', text: node.text }] }
    case 'decision':
      return { type: 'decision', attrs: node }
    case 'glossary':
      return { type: 'glossary', attrs: node }
    case 'step':
      return { type: 'step', attrs: node }
  }
}
```

### 5.4 WebSocket 客户端 — 改造 `api/ws.ts`

```typescript
export class PlanStreamClient {
  private ws: WebSocket | null = null

  connect(
    sessionId: string,
    onEvent: (event: WSEvent) => void,
    onClose: () => void,
  ): void {
    const url = `ws://${window.location.host}/api/ws/sessions/${sessionId}/plan`
    this.ws = new WebSocket(url)
    this.ws.onmessage = (e) => onEvent(JSON.parse(e.data) as WSEvent)
    this.ws.onclose = onClose
    this.ws.onerror = () => onClose()
  }

  sendGenerate(params: {
    init_request: string
    adapter_id: string
    ltm_recall?: string[]
    available_tools?: string[]
  }): void {
    this.ws?.send(JSON.stringify({ type: 'generate', ...params }))
  }

  close(): void {
    this.ws?.close()
    this.ws = null
  }
}
```

> ⚠️ Vite proxy 已在 Task 09 设计预留，本 task 取消注释即生效。

### 5.5 App 主界面 — 改造 `App.tsx`

```typescript
function App() {
  const [state, dispatch] = useReducer(sessionReducer, { status: 'idle' })
  const clientRef = useRef<PlanStreamClient | null>(null)

  const handleStart = async (initRequest: string, adapterId: string) => {
    // 1. POST /api/sessions
    const res = await fetch('/api/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ init_request: initRequest, adapter_id: adapterId }),
    })
    const session = await res.json()
    dispatch({ type: 'START_SESSION', sessionId: session.id })

    // 2. WS connect + send generate
    const client = new PlanStreamClient()
    clientRef.current = client
    client.connect(session.id, (event) => dispatch(eventToAction(event)), () => {})
    client.sendGenerate({ init_request: initRequest, adapter_id: adapterId })
  }

  const handleAdvance = async () => {
    if (state.status !== 'review') return
    await fetch(`/api/sessions/${state.sessionId}/advance-to-acting`, { method: 'POST' })
    alert('进入 Action 阶段（M3 实现）')
  }

  return (
    <SessionContext.Provider value={{ sessionId: state.status !== 'idle' ? state.sessionId : '', dispatch }}>
      <div className="app">
        <InitForm onSubmit={handleStart} disabled={state.status !== 'idle' && state.status !== 'error'} />
        {(state.status === 'streaming' || state.status === 'review') && (
          <PlanDocEditor doc={planToTiptapDoc(state.plan)} />
        )}
        {state.status === 'review' && (
          <ActionButton
            disabled={!allBlockingAnswered(state.plan.nodes)}
            onClick={handleAdvance}
          />
        )}
        {state.status === 'error' && <ErrorBanner code={state.code} message={state.message} />}
      </div>
    </SessionContext.Provider>
  )
}
```

### 5.6 SessionContext

`DecisionView` 需要拿 sessionId 和 dispatch；React Context 比 prop drilling 干净。

---

## 6. 文件清单

### 6.1 Backend (Task 10a)

| 路径 | 类型 |
|------|------|
| `backend/src/app/db/session.py` | **新增** |
| `backend/src/app/services/session_service.py` | **新增** |
| `backend/src/app/api/sessions.py` | **新增** |
| `backend/src/app/api/adapters.py` | 改造 `get_adapter_service` 接 DB |
| `backend/src/app/api/ws_plan.py` | 改造 `_resolve_dependencies` + 持久化 plan |
| `backend/src/app/main.py` | include sessions_router |
| `backend/tests/test_session_service.py` | **新增** |
| `backend/tests/test_sessions_api.py` | **新增** |
| `backend/tests/test_ws_plan.py` | 改造（resolve 不再抛 NotImplemented） |
| `backend/tests/conftest.py` | 加 in-memory SQLite fallback fixture（或要求真 Postgres） |

### 6.2 Frontend (Task 10b)

| 路径 | 类型 |
|------|------|
| `frontend/src/editor/nodes/DecisionNode.tsx` | **新增** |
| `frontend/src/editor/nodes/GlossaryNode.tsx` | **新增** |
| `frontend/src/editor/nodes/StepNode.tsx` | **新增** |
| `frontend/src/editor/serializer.ts` | **新增** |
| `frontend/src/editor/PlanDocEditor.tsx` | 改造：扩展 extensions + 接受 `doc` prop |
| `frontend/src/state/sessionReducer.ts` | **新增** |
| `frontend/src/state/SessionContext.tsx` | **新增** |
| `frontend/src/api/ws.ts` | 改造：PlanStreamClient 类实现 |
| `frontend/src/api/sessions.ts` | **新增**（POST /sessions + decision API 封装） |
| `frontend/src/App.tsx` | 改造：reducer + InitForm + ActionButton |
| `frontend/src/components/InitForm.tsx` | **新增** |
| `frontend/src/components/ActionButton.tsx` | **新增** |
| `frontend/src/components/ErrorBanner.tsx` | **新增** |
| `frontend/vite.config.ts` | 改造：开启 /api proxy |
| `frontend/src/editor/nodes/*.test.tsx` | 三个节点的 render 测试 |
| `frontend/src/state/sessionReducer.test.ts` | reducer 测试 |

---

## 7. 测试清单

### 7.1 Backend

`tests/test_session_service.py`（需要 DB，参见 §10）：

| # | 测试 | 断言 |
|---|------|------|
| B1 | `create_session_starts_in_planning` | phase = 'planning' |
| B2 | `save_plan_advances_to_plan_review` | phase = 'plan_review', current_plan_version+1 |
| B3 | `answer_decision_updates_node` | plan.document.nodes 中目标 decision.answer 被设 |
| B4 | `answer_decision_rejects_invalid_option` | raise ValueError |
| B5 | `all_blocking_answered_false_when_some_pending` | 一个 blocking 未答 → False |
| B6 | `all_blocking_answered_true_when_all_done` | 全答 → True |
| B7 | `advance_to_acting_blocked_when_unanswered` | raise → 409 |
| B8 | `advance_to_acting_transitions_state` | phase: plan_review → acting |

`tests/test_sessions_api.py`：

| # | 测试 | 断言 |
|---|------|------|
| A1 | POST /sessions 成功 → 201 + body | id 是 UUID，phase='planning' |
| A2 | POST 无效 adapter_id → 400/404 | code='adapter_not_found' |
| A3 | POST /decisions answer_invalid → 400 | |
| A4 | POST /advance-to-acting unanswered → 409 | |
| A5 | POST /advance-to-acting ok → 200 + phase='acting' | |

`tests/test_ws_plan.py` 改造：T3 改 mock `AdapterService.get` 抛 `AdapterNotFoundError`，其他保持。

### 7.2 Frontend

| # | 文件 | 测试 |
|---|------|------|
| F1 | `sessionReducer.test.ts` | START_SESSION → connecting |
| F2 | 同上 | WS_PLAN_START → streaming + plan 初始化 |
| F3 | 同上 | WS_PLAN_NODE 追加节点 |
| F4 | 同上 | WS_PLAN_DONE → review |
| F5 | 同上 | ANSWER_DECISION 更新目标节点 |
| F6 | `DecisionNode.test.tsx` | 渲染 question + N 个 radio |
| F7 | 同上 | blocking=true 显示"必选"badge |
| F8 | `GlossaryNode.test.tsx` | term 显示 + title=definition |
| F9 | `StepNode.test.tsx` | tool 名 + description + args |
| F10 | `serializer.test.ts` | PlanDocument → 正确 Tiptap JSON |

---

## 8. 端到端 Demo 脚本

完成后手测：

```bash
# 1. 启 DB
cd prar-agent && docker compose up -d postgres

# 2. 起后端
cd backend && make db-init && make dev

# 3. 起前端
cd frontend && pnpm dev

# 4. 浏览器
# - 打开 http://localhost:5173
# - 输入需求 "实现一个 todo list 应用"
# - 选 adapter（先用 backend 的 /api/adapters POST 创一个 anthropic adapter）
# - 点提交 → 看到节点流式出现
# - 答完所有 blocking 决策题
# - "进入 Action" 按钮变可点
```

---

## 9. 关键设计决策

| 决策 | 理由 |
|------|------|
| 决策答案存在 `plans.document` JSONB 而非独立表 | M1 范围内简单；M2 引入 Comment 表时再考虑是否拆 |
| Plan 持久化在 stream 前 | 网络中断重连后能从 DB 恢复 plan |
| Decision answer 走 HTTP POST 而非 WS message | 答题非实时性 + RESTful 更符合直觉 + 简化 WS handler 状态 |
| "进入 Action" 走独立 endpoint 而非 WS message | 同上 |
| LLMRouter 单例 + lru_cache | 客户端缓存才有意义；避免每请求重建 |
| WS handler 内手动 sessionmaker context | starlette WS 不走 HTTP middleware；显式 `async with` 干净 |
| state_machine.transition 用现有签名 | Task 05 已加 session_id 参数；本 task 直接用 |
| Decision answer 校验 `answer in options` | 防御性；前端 radio 理论上不会出错但 API 不能假设 |
| 前端用 useReducer + Context | M1 单一会话，无需 zustand；M2+ 引入跨组件共享时评估 |
| Tiptap node `atom: true` | decision/step/glossary 是不可分裂的整体；防止光标进入内部破坏结构 |

---

## 10. 测试基础设施开放问题

backend 当前 conftest 没有 DB fixture（test_models 只测 ORM schema 不查询）。Task 10 引入真 query：

> ⚠️ 决策点 Q4：测试 DB 策略
> A=要求开发机 docker-compose 起 postgres，CI 用 service container（推荐，与生产一致）
> B=test fixture 用 SQLite + 跳过 pgvector/JSONB 相关测试（复杂）
> C=用 testcontainers-python 跑 ephemeral postgres（最干净但慢）

---

## 11. 风险

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| Tiptap NodeView + WS 流式插入导致 schema 抖动 | 高 | 中 | 每次 WS_PLAN_NODE 完整重建 doc（M1 节点 < 30 个无 perf 问题） |
| 决策答案 race condition（用户点击 + WS 同时改 plan） | 低 | 中 | M1 WS_PLAN_DONE 后才允许答题；前端 status=review 才显示 radio |
| `plans.document` JSONB 字段更新需 deep copy | 中 | 中 | SessionService.answer_decision 显式 `dict(plan.document)` + 重新赋值 |
| DB 连接池在测试环境耗尽 | 低 | 低 | conftest fixture 用 NullPool |
| Vite proxy WS 转发 `ws://` 不工作 | 中 | 中 | `ws: true` 已在配置中，验证 dev 环境 |

---

## 12. 决策题汇总

| # | 题目 | 选项 | 推荐 |
|---|------|------|------|
| Q1 | DB 连接池策略 | A=lazy module singleton / B=lifespan 显式 / C=每请求 create_engine | **A** |
| Q2 | Plan 持久化时机 | A=stream 前 / B=stream 后 / C=每节点 stream+persist | **A** |
| Q3 | Glossary hover 卡片 | A=原生 title attr / B=floating-ui | **A** |
| Q4 | 测试 DB 策略 | A=docker postgres / B=SQLite fixture / C=testcontainers | **A** |

---

## 13. Commit 拆分

> ⚠️ 决策点 Q5：commit 拆分粒度
> A=10a backend + 10b frontend 两 commit（推荐，前后端独立 reviewable）
> B=单 commit（违反 WORKFLOW.md §3.3 跨任务禁律——backend 和 frontend 是耦合任务但物理隔离）
> C=10a-db + 10a-api + 10b 三 commit（更细但 PR 链路繁琐）

**推荐 A**：
- `feat(session): SessionService + sessions API + plan 持久化 + ws_plan resolve 接通 (M1-10a)`
- `feat(frontend): Plan 三节点渲染 + WS 客户端 + Decision 答题闭环 + Action 按钮 (M1-10b)`

---

## 14. M1 完成判定

Task 10 合并后，M1 milestone 完成。验收手测脚本见 §8，自动化测试见 §7。

---

主人审阅后回 `APPROVED`（含 Q1-Q5 选择）即开始编码。
