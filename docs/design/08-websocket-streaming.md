# 08. 基础 WebSocket 流式管道

> **状态**：DRAFT，待 APPROVED
> **依赖**：Task 01（骨架）、Task 05（logging + request_id）、Task 07（PlanEngine）
> **被依赖**：Task 10（前端 plan 渲染消费 WS 事件）、Task 19（工具输出流式复用此管道）
> **commit 范围**：单个 commit

---

## 1. 目标

- **一句话**：前端通过 WebSocket 连到后端，触发一次 plan 生成 → 后端按 plan 节点边界推送 JSON 事件 → 前端逐节点渲染。
- **验收标准**：
  1. `GET /api/ws/sessions/{session_id}/plan` 升级为 WebSocket
  2. 客户端发 `{"type": "generate", "init_request": "..."}` 触发生成
  3. 后端按"plan.start → plan.node × N → plan.done"事件序列推送（每个 node 一个 message frame）
  4. 任何阶段失败 → 推 `{"type": "error", "code": ..., "message": ...}` 并关闭连接
  5. WebSocket 连接同样有 `request_id`，与 HTTP 中间件保持一致的日志格式
  6. `make test` 全绿（mock LLM 不真调 API）

---

## 2. 现状问题

| # | 现状 | 问题 |
|---|------|------|
| P1 | PlanEngine.generate() 是一次 awaitable，整 plan 出来才返回 | 前端等待时间长（10-30s），无反馈 |
| P2 | 没有任何流式管道 | Task 10 / 19 都依赖此 |

---

## 3. 流式策略选择

### 3.1 三种粒度对比

| 粒度 | 实现复杂度 | 用户体验 | 备注 |
|------|----------|---------|------|
| **逐 token chunk** | 高（要 hook 进 instructor 内部） | 富文本会高频 reflow，卡顿 | DESIGN.md §8.2 明确禁止 |
| **按 plan 节点** ⭐ | 低（generate 完整 plan 后逐节点推） | 节点级渲染，平滑 | **本 task 选这个** |
| **整 plan 一次推** | 极低 | 等待感强，丧失流式意义 | 退化到非流式 |

### 3.2 本 task 实现层级

MVP 阶段不接 instructor streaming，**而是在 PlanEngine.generate() 完成后**，**逐节点推 WS 事件 + 节点间加小延迟**模拟流式体验。理由：

1. Instructor 的 streaming 对 Anthropic / OpenAI structured output 的支持差异巨大，会卡 schema validation
2. PlanEngine 总耗时 = Planner 调用 + Critic 调用 = ~10-30s，**节点级"假流式"已经能让前端从黑屏到有内容**
3. M3+ 真正需要 LLM token-level 流式时（如 executor 的 think），那时再扩展

> ⚠️ 决策点 Q1：MVP 用"假流式（generate 完再逐节点推）"还是"真流式（instructor stream + 节点切边界）"？
> A=假流式（推荐，简单可控）/ B=真流式（M5+ 再做）

---

## 4. WebSocket 协议设计

### 4.1 路由

```
WS /api/ws/sessions/{session_id}/plan
```

`session_id` 是 UUID。M1 阶段不强校验是否在 DB 存在（Task 03 状态机的 Session 表 schema 已建好，但还没 CRUD API），仅做格式校验。

### 4.2 消息格式（JSON）

#### 入向消息（client → server）

```json
{
  "type": "generate",
  "init_request": "用户的需求文本",
  "adapter_id": "uuid-of-model-adapter",
  "ltm_recall": [],
  "available_tools": ["shell", "fs.read", "fs.write"]
}
```

字段：

| 字段 | 必选 | 说明 |
|------|------|------|
| `type` | ✅ | 固定 `"generate"`（未来扩展 `"cancel"` / `"answer_decision"` 等） |
| `init_request` | ✅ | 用户需求 |
| `adapter_id` | ✅ | 选用的 LLM adapter ID（4.1 体系） |
| `ltm_recall` | ❌ | 默认 `[]` |
| `available_tools` | ❌ | 默认三个基础工具 |

#### 出向消息（server → client）

**事件 1：开始**

```json
{"type": "plan.start", "session_id": "...", "title": "...", "summary": "..."}
```

**事件 2..N：节点（每个一帧）**

```json
{"type": "plan.node", "index": 0, "node": {"type": "heading", "level": 1, "text": "..."}}
{"type": "plan.node", "index": 1, "node": {"type": "paragraph", "text": "..."}}
{"type": "plan.node", "index": 2, "node": {"type": "decision", "id": "dec_001", ...}}
```

**事件 N+1：结束**

```json
{"type": "plan.done", "total_nodes": 7}
```

**错误事件（任何阶段，发送后关闭）**

```json
{"type": "error", "code": "...", "message": "..."}
```

错误码（与 Task 04 LLMError 层级对齐）：

| code | 触发 |
|------|------|
| `invalid_message` | 入向消息格式错误 / 缺字段 |
| `adapter_not_found` | adapter_id 不在 DB |
| `llm_transport` | LLM 调用失败（网络/认证/unknown provider） |
| `structured_output` | LLM 返回不符合 schema |
| `internal` | 未分类异常 |

---

## 5. 模块结构

```
app/
├── api/
│   └── ws_plan.py         # 新增：WebSocket 路由 + 协议处理
├── core/
│   └── ws_streamer.py     # 新增：plan node → WS 事件序列化与推送
└── main.py                # 改造：include ws_plan 路由
```

### 5.1 `core/ws_streamer.py`

```python
"""把 PlanDocument 序列化为 WS 事件序列。"""

from typing import AsyncIterator
from app.core.plan_schemas import PlanDocument


CHUNK_DELAY_MS = 50  # 节点之间小延迟，模拟流式体验


async def stream_plan(plan: PlanDocument, session_id: str) -> AsyncIterator[dict]:
    """把 PlanDocument 拆成事件序列。"""
    yield {
        "type": "plan.start",
        "session_id": session_id,
        "title": plan.title,
        "summary": plan.summary,
    }
    for i, node in enumerate(plan.nodes):
        await asyncio.sleep(CHUNK_DELAY_MS / 1000)  # 节点间小延迟
        yield {
            "type": "plan.node",
            "index": i,
            "node": node.model_dump(),
        }
    yield {"type": "plan.done", "total_nodes": len(plan.nodes)}
```

> ⚠️ 决策点 Q2：节点间延迟是否硬编码？
> A=硬编码 50ms（推荐，UX 流畅度基准）/ B=配置项（暂时不需要） / C=完全无延迟（生成完一次性推）

### 5.2 `api/ws_plan.py`

```python
"""WebSocket: /api/ws/sessions/{session_id}/plan"""

import uuid
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ValidationError

from app.core.logging import get_logger, request_id_var
from app.core.plan_engine import PlanEngine
from app.core.ws_streamer import stream_plan
from app.llm.router import LLMError, LLMTransportError, StructuredOutputError
from app.services.adapter_service import AdapterNotFoundError

_log = get_logger("ws_plan")
router = APIRouter(prefix="/api/ws", tags=["websocket"])


class GenerateMessage(BaseModel):
    type: str = Field(pattern="^generate$")
    init_request: str = Field(min_length=1)
    adapter_id: uuid.UUID
    ltm_recall: list[str] = Field(default_factory=list)
    available_tools: list[str] | None = None


@router.websocket("/sessions/{session_id}/plan")
async def plan_websocket(websocket: WebSocket, session_id: uuid.UUID) -> None:
    rid = uuid.uuid4().hex[:12]
    request_id_var.set(rid)

    await websocket.accept()
    _log.info("ws_connected", session_id=str(session_id))

    try:
        raw = await websocket.receive_json()
        try:
            msg = GenerateMessage.model_validate(raw)
        except ValidationError as e:
            await _send_error(websocket, "invalid_message", str(e))
            return

        # adapter resolve + plan_engine call + stream
        try:
            adapter, plan_engine = await _resolve_dependencies(msg.adapter_id)
        except AdapterNotFoundError:
            await _send_error(websocket, "adapter_not_found", str(msg.adapter_id))
            return

        try:
            plan = await plan_engine.generate(
                init_request=msg.init_request,
                adapter=adapter,
                ltm_recall=msg.ltm_recall,
                available_tools=msg.available_tools,
            )
        except LLMTransportError as e:
            await _send_error(websocket, "llm_transport", str(e))
            return
        except StructuredOutputError as e:
            await _send_error(websocket, "structured_output", str(e))
            return
        except LLMError as e:
            await _send_error(websocket, "llm_transport", str(e))
            return

        async for event in stream_plan(plan, str(session_id)):
            await websocket.send_json(event)

    except WebSocketDisconnect:
        _log.info("ws_disconnected", session_id=str(session_id))
    except Exception as e:
        _log.exception("ws_internal_error", error=str(e))
        await _send_error(websocket, "internal", str(e))
    finally:
        await _close_quietly(websocket)
```

> ⚠️ 决策点 Q3：WS 的 request_id 来源
> A=每次连接生成新 UUID（推荐）/ B=客户端通过 query string 传 / C=首条消息中带

### 5.3 依赖注入

`_resolve_dependencies(adapter_id)` 需要：

1. 从 DB 取 ModelAdapter（用 AdapterService）
2. 调 `AdapterService.resolve()` 解 env var 拿到 ResolvedAdapter
3. 构造 LLMRouter + PlanEngine

但当前 `adapter_service.get_adapter_service()` 是 `NotImplementedError` 占位（4.1b 设计文档明确说 DB session middleware 建好后接），且**M1 现阶段 DB session 中间件还没建**。

**临时方案**：本 task 接受 WS 不能真正端到端跑通，**只测协议层和 ws_streamer 模块**。等 Task 10（demo 落地）或更早单独做一个 DB session middleware 的 mini task 时补上 `_resolve_dependencies`。

> ⚠️ 决策点 Q4：WS 路由如何处理 adapter resolve 缺失？
> A=本 task 内 `_resolve_dependencies` 抛 NotImplementedError，附 TODO 注释（推荐，留干净的扩展点）
> B=在本 task 内同时把 DB session middleware 一起做（scope 蔓延）
> C=完全跳过端到端，只做 ws_streamer 工具函数（不能真起 WS endpoint）

---

## 6. 文件清单

| 路径 | 类型 | 说明 |
|------|------|------|
| `backend/src/app/core/ws_streamer.py` | **新增** | PlanDocument → 事件序列 |
| `backend/src/app/api/ws_plan.py` | **新增** | WS endpoint + 协议处理 |
| `backend/src/app/main.py` | 改造 | include ws_plan router |
| `backend/tests/test_ws_streamer.py` | **新增** | ws_streamer 单元测试 |
| `backend/tests/test_ws_plan.py` | **新增** | WS endpoint 集成测试（mock PlanEngine + AdapterService） |

---

## 7. 实施步骤

| # | 步骤 | 验证 |
|---|------|------|
| 1 | 写 `core/ws_streamer.py` | import 不报错；单元测试 T1-T3 |
| 2 | 写 `api/ws_plan.py`（含 `_resolve_dependencies` 留 NotImplementedError + TODO） | import 不报错 |
| 3 | `main.py` include ws_plan router | `/api/ws/sessions/.../plan` 路由存在 |
| 4 | 写 `tests/test_ws_streamer.py` | 单元测试全绿 |
| 5 | 写 `tests/test_ws_plan.py`（覆盖入向消息校验 + 错误路径 + 成功路径 mock） | 集成测试全绿 |
| 6 | `make lint && make test` | 0 error |

---

## 8. 测试清单

### `tests/test_ws_streamer.py`（新增）

| # | 测试 | 断言 |
|---|------|------|
| T1 | `test_stream_plan_emits_start_nodes_done` | 事件序列首尾分别是 plan.start / plan.done，中间是 plan.node |
| T2 | `test_stream_plan_node_index_sequential` | plan.node 的 index 递增 0..N-1 |
| T3 | `test_stream_plan_empty_nodes` | 空 nodes 列表仍发 start + done，total_nodes=0 |
| T4 | `test_stream_plan_node_content_matches_input` | 每个 plan.node.node 与输入 PlanDocument.nodes[i] 等价 |

### `tests/test_ws_plan.py`（新增）

使用 fastapi `TestClient.websocket_connect`，全程 mock PlanEngine：

| # | 测试 | 断言 |
|---|------|------|
| T1 | `test_ws_rejects_invalid_first_message` | 发非 generate 类型消息 → 收到 `error/invalid_message` |
| T2 | `test_ws_rejects_missing_required_field` | 缺 init_request → `error/invalid_message` |
| T3 | `test_ws_returns_adapter_not_found` | mock adapter 抛 AdapterNotFoundError → `error/adapter_not_found` |
| T4 | `test_ws_returns_llm_transport_error` | mock plan_engine 抛 LLMTransportError → `error/llm_transport` |
| T5 | `test_ws_returns_structured_output_error` | mock plan_engine 抛 StructuredOutputError → `error/structured_output` |
| T6 | `test_ws_success_emits_full_event_sequence` | 完整链路 mock → 收到 plan.start + N×plan.node + plan.done |

### 已有测试无回归

已有 116 测试不动（ws_plan 是新增模块，无侵入性改动）。

---

## 9. 设计决策

| 决策 | 理由 |
|------|------|
| 节点级流式而非 token 级 | DESIGN.md §8.2 硬约束；UX 平滑；schema validation 不被截断 |
| 单一 WS endpoint 而非 SSE | DESIGN.md 选了 WebSocket；后续 Task 10 的双向通信（答题、取消）也要走同一管道 |
| 事件 type 用 `plan.start` / `plan.node` 命名空间 | Task 19 加 `tool.stdout` / `tool.exit` 时不冲突 |
| `_resolve_dependencies` 留占位不接 DB session | 维持 4.1b 的设计纪律：DB session middleware 是独立 concern，不在 Task 08 里蔓延 scope |
| `CHUNK_DELAY_MS = 50` 硬编码 | UX 基准值；后续真有 perf 问题再改 |
| 错误后必关闭 WS | 错误不可恢复（adapter 不存在 / LLM 失败），让前端重连而非保持半死状态 |

---

## 10. 风险

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| RequestContextMiddleware 是 HTTP 中间件，不拦截 WS | 高 | 中 | ws_plan 内手动 `request_id_var.set()` |
| WS endpoint 不能端到端跑通（adapter resolve 缺失） | 高 | 低 | 本 task 明确 scope 不接 DB session；M1 demo 时补 |
| TestClient WS API 与 starlette 版本绑定 | 低 | 低 | 用 `TestClient.websocket_connect()` 标准 API |
| WS 连接泄漏（异常路径未关） | 中 | 中 | `try/finally` + `_close_quietly` 兜底 |

---

## 11. 决策题汇总

| # | 题目 | 选项 | 推荐 |
|---|------|------|------|
| Q1 | MVP 流式粒度 | A=假流式（generate 完逐节点推） / B=真流式（instructor stream） | **A** |
| Q2 | 节点间延迟 | A=硬编码 50ms / B=配置项 / C=无延迟 | **A** |
| Q3 | WS request_id 来源 | A=每次连接生成 / B=query string / C=首条消息 | **A** |
| Q4 | adapter resolve 缺失处理 | A=占位 NotImplementedError+TODO / B=本 task 一起做 DB session / C=完全跳过端到端 | **A** |

---

主人审阅后回 `APPROVED`（或修改意见 + 决策题选择）即开始编码。
