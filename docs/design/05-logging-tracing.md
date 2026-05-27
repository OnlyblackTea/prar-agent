# 05. Logging / 链路追踪基建

> **状态**：DRAFT，待 APPROVED
> **依赖**：Task 01（骨架）、Task 04.1b（router 改造后的 `complete_structured` 签名）
> **被依赖**：Task 07（plan_engine 日志）、Task 08（WebSocket 事件日志）、Task 18（action_dispatcher 日志）
> **commit 范围**：单个 commit

---

## 1. 目标

- **一句话**：让每个 HTTP 请求从入口到 LLM 调用到状态机转移全程可追溯，开发期日志可读、生产期日志可查。
- **验收标准**：
  1. 每条日志自动带 `request_id`（UUID4 短格式），同一请求链路内一致
  2. 开发环境：console 彩色人类可读；`LOG_FORMAT=json` 时：JSONL 单行输出
  3. LLM 调用日志：model / provider / token usage / 耗时（ms）/ 成功或失败
  4. 状态机转移日志：session_id / from_phase / to_phase
  5. FastAPI request/response 日志：method / path / status_code / duration_ms
  6. `make test` 全绿（含新增日志测试），`make lint` 无警告

---

## 2. 现状问题

| # | 现状 | 问题 |
|---|------|------|
| P1 | 无 structlog，无任何日志输出 | 调试靠 print，生产完全盲区 |
| P2 | 无 request_id | 并发请求无法区分日志归属 |
| P3 | LLM 调用无耗时/token 记录 | 成本无法追踪，慢调用无法定位 |
| P4 | 状态机转移无日志 | 状态异常难以复盘 |

---

## 3. 技术选型

| 维度 | 选择 | 理由 |
|------|------|------|
| 日志库 | **structlog** | 结构化日志标准；绑定 context var 天然支持 request_id 传播；processor pipeline 灵活 |
| 输出格式 | dev=`ConsoleRenderer`，prod=`JSONRenderer` | dev 可读，prod 可被 ELK/Loki 采集 |
| request_id 传播 | **`contextvars.ContextVar`** | 原生协程安全；FastAPI middleware 写入，全链路自动继承 |
| 集成点 | FastAPI middleware + router wrapper + state_machine hook | 最少侵入，不改业务代码签名 |

---

## 4. 架构设计

### 4.1 模块结构

```
app/
├── core/
│   ├── logging.py          # 新增：structlog 配置 + request context
│   └── state_machine.py    # 改造：转移时调 logger
├── llm/
│   └── router.py           # 改造：complete_structured 内部记录耗时/token
└── main.py                 # 改造：挂载 RequestContextMiddleware
```

### 4.2 `core/logging.py` — 配置与 Context

```python
import contextvars
import logging
import uuid

import structlog

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)


def _add_request_id(
    logger: logging.Logger, method_name: str, event_dict: dict
) -> dict:
    rid = request_id_var.get("")
    if rid:
        event_dict["request_id"] = rid
    return event_dict


def setup_logging(*, log_level: str = "INFO", json_format: bool = False) -> None:
    """应用启动时调一次。"""
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        _add_request_id,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_format:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(log_level.upper())

    # 降噪：第三方库
    for noisy in ("httpcore", "httpx", "asyncio", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
```

### 4.3 `RequestContextMiddleware` — request_id 注入

```python
# app/core/logging.py 内

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
import time


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        request_id_var.set(rid)

        logger = get_logger("http")
        start = time.perf_counter()

        response = await call_next(request)

        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        logger.info(
            "request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )

        response.headers["X-Request-ID"] = rid
        return response
```

### 4.4 `main.py` 改造

```python
from app.core.logging import RequestContextMiddleware, setup_logging

def create_app() -> FastAPI:
    s = get_settings()
    setup_logging(
        log_level=s.log_level,
        json_format=s.environment == "production",
    )
    app = FastAPI(title=s.app_name, version=s.app_version)
    app.add_middleware(RequestContextMiddleware)
    # ... include_router ...
    return app
```

### 4.5 `router.py` 改造 — LLM 调用日志

在 `complete_structured` 方法内部，wrap 一层计时 + 日志：

```python
import time
from app.core.logging import get_logger

_log = get_logger("llm")

# 在 complete_structured 内部：
start = time.perf_counter()
try:
    parsed, raw = await client.chat.completions.create_with_completion(...)
except ...:
    duration_ms = round((time.perf_counter() - start) * 1000, 1)
    _log.error("llm_call_failed", provider=adapter.provider,
               model=adapter.model, duration_ms=duration_ms, error=str(e))
    raise

duration_ms = round((time.perf_counter() - start) * 1000, 1)
usage = _extract_usage(raw)
_log.info(
    "llm_call",
    provider=adapter.provider,
    model=adapter.model,
    input_tokens=usage.input_tokens,
    output_tokens=usage.output_tokens,
    duration_ms=duration_ms,
)
```

### 4.6 `state_machine.py` 改造 — 转移日志

在 `transition()` 函数中添加日志（不改函数签名）：

```python
from app.core.logging import get_logger

_log = get_logger("state_machine")

def transition(current: Phase, target: Phase) -> Phase:
    if target not in TRANSITIONS[current]:
        _log.warning("transition_rejected", ...)
        raise InvalidTransitionError(...)
    _log.info("transition", from_phase=current.value, to_phase=target.value)
    return target
```

> ⚠️ 决策点 Q1：state_machine.transition() 是否加可选 `session_id` 参数用于日志？
> A=加（推荐，日志更有用）/ B=不加（保持纯净，session_id 靠 contextvar 传播）

---

## 5. Settings 新增字段

```python
# config.py 新增：
log_format: str = "console"  # "console" | "json"
```

> `log_level` 已存在（Task 01 加的）。新增 `log_format` 控制输出格式。
> `environment == "production"` 时自动切 json 的逻辑改为：优先读 `log_format`，没显式设则按 `environment` 推断。

---

## 6. 依赖变更

```toml
# pyproject.toml dependencies 新增：
"structlog>=24.0.0",
```

无其他新依赖。structlog 本身零依赖。

---

## 7. 文件清单

| 路径 | 类型 | 说明 |
|------|------|------|
| `backend/src/app/core/logging.py` | **新增** | structlog 配置 + ContextVar + RequestContextMiddleware + get_logger |
| `backend/src/app/main.py` | 改造 | 调 setup_logging + add_middleware |
| `backend/src/app/config.py` | 改造 | 新增 `log_format` 字段 |
| `backend/src/app/llm/router.py` | 改造 | complete_structured 内加计时 + 日志 |
| `backend/src/app/core/state_machine.py` | 改造 | transition() 内加日志 |
| `backend/pyproject.toml` | 改造 | 加 `structlog>=24.0.0` |
| `backend/tests/test_logging.py` | **新增** | 日志基建测试 |

---

## 8. 实施步骤

| # | 步骤 | 验证 |
|---|------|------|
| 1 | `pyproject.toml` 加 structlog 依赖 + `uv sync` | import structlog 不报错 |
| 2 | 写 `core/logging.py`（setup_logging + ContextVar + Middleware + get_logger） | 单元测试 T1-T3 |
| 3 | `config.py` 加 `log_format` 字段 | Settings() 实例化不报错 |
| 4 | `main.py` 挂 setup_logging + RequestContextMiddleware | `/health` 请求产生带 request_id 的日志行 |
| 5 | `router.py` 加 LLM 调用日志 | test_router 仍全绿 + mock 调用产生 llm_call 日志 |
| 6 | `state_machine.py` 加转移日志 | test_state_machine 仍全绿 |
| 7 | 写 `tests/test_logging.py` | make test 全绿 |
| 8 | `make lint && make test` | 0 error |

---

## 9. 测试清单

### `tests/test_logging.py`（新增）

| # | 测试 | 断言 |
|---|------|------|
| T1 | `test_setup_logging_console_mode` | 调 `setup_logging(json_format=False)` 不抛异常；root logger 有 handler |
| T2 | `test_setup_logging_json_mode` | 调 `setup_logging(json_format=True)` 不抛异常 |
| T3 | `test_request_id_var_propagation` | 设 `request_id_var.set("abc")`，structlog 输出包含 `request_id=abc` |
| T4 | `test_request_context_middleware_sets_request_id` | TestClient 发请求 → 响应 header 包含 `X-Request-ID` |
| T5 | `test_request_context_middleware_respects_incoming_header` | 请求带 `X-Request-ID: custom-123` → 响应 header 返回 `custom-123` |
| T6 | `test_get_logger_returns_bound_logger` | `get_logger("test")` 返回 `structlog.stdlib.BoundLogger` 实例 |

### 已有测试回归

- `test_health.py`：3 个 → 仍绿（middleware 不影响 /health 行为）
- `test_router.py`：15 个 → 仍绿（日志是旁路，不改返回值）
- `test_state_machine.py`：全部 → 仍绿（日志是旁路）
- `test_models.py`：全部 → 仍绿（无关）
- `test_providers.py`：全部 → 仍绿（无关）

---

## 10. 日志样例

### Console 模式（开发）

```
2026-05-28T01:00:00.123Z [info] request  request_id=a1b2c3d4e5f6 method=POST path=/api/adapters status_code=201 duration_ms=12.3
2026-05-28T01:00:00.456Z [info] llm_call request_id=a1b2c3d4e5f6 provider=anthropic model=claude-sonnet-4-6 input_tokens=150 output_tokens=320 duration_ms=1823.4
2026-05-28T01:00:00.789Z [info] transition request_id=a1b2c3d4e5f6 from_phase=init to_phase=planning
```

### JSON 模式（生产）

```json
{"event":"request","request_id":"a1b2c3d4e5f6","method":"POST","path":"/api/adapters","status_code":201,"duration_ms":12.3,"level":"info","timestamp":"2026-05-28T01:00:00.123Z"}
{"event":"llm_call","request_id":"a1b2c3d4e5f6","provider":"anthropic","model":"claude-sonnet-4-6","input_tokens":150,"output_tokens":320,"duration_ms":1823.4,"level":"info","timestamp":"2026-05-28T01:00:00.456Z"}
```

---

## 11. 设计决策记录

| 决策 | 理由 |
|------|------|
| structlog 而非 stdlib logging | 结构化字段绑定 + processor pipeline + contextvar 集成开箱即用 |
| request_id 用 ContextVar 而非中间件参数传递 | 协程安全；任意深度的函数调用都能 get()，不需要层层传参 |
| request_id 12 字符 hex（非完整 UUID） | 日志可读性 vs 唯一性平衡；12 hex = 48 bit ≈ 2.8×10¹⁴，单机无碰撞风险 |
| BaseHTTPMiddleware 而非 raw ASGI | 代码简洁；性能差异在当前规模不可察觉；如后续需要 streaming 兼容再换 |
| 第三方库降噪（httpcore/httpx/asyncio） | 这些库 DEBUG 级别极其碎嘴，不降噪会淹没业务日志 |
| LLM 日志在 router 内部而非 middleware | router 知道 provider/model/token，middleware 不知道；职责更清晰 |

---

## 12. 风险与未决

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| BaseHTTPMiddleware 与 WebSocket 不兼容 | 高 | 中 | Task 08 WebSocket 管道时需要单独处理 WS 连接的 request_id 注入，不走此 middleware |
| structlog 与 uvicorn 内置 logger 冲突 | 低 | 低 | setup_logging 清空 root handlers 再装自己的 |
| 日志量过大（LLM 调用含大 prompt） | 中 | 低 | 日志只记 meta（token 数/耗时），不记 prompt 内容 |

---

## 13. 决策题

| # | 题目 | 选项 | 推荐 |
|---|------|------|------|
| Q1 | state_machine.transition() 是否加可选 session_id 参数 | A=加（推荐）/ B=不加 | A |

---

主人审阅后回 `APPROVED`（或修改意见）即开始编码。
