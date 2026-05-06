# 04. Instructor + 直连 SDK 路由（弃用 LiteLLM）

## 目标

- **一句话**：`llm/router.py` 用 **Instructor** 包 **anthropic / openai 直连 SDK**，让上层一句话拿到 pydantic 校验过的结构化响应；schema 失败由 Instructor 自动重试反馈给模型，无需自己写归一化胶水。
- **验收标准**（缺一不可）：
  1. `cd backend && make test` 全绿（旧 60 + 本任务新增 ~14）
  2. `cd backend && make lint && make typecheck` 仍零警告/零错误
  3. 单元测试 100% mock，**禁止真调 API**（CLAUDE.md "LLM 成本意识" 硬约束）
  4. 测试覆盖：anthropic 路由、openai 路由、unknown model 拒绝、默认 model fallback、Instructor 重试耗尽、传输异常、token usage 抽取（两家差异）、finish_reason 抽取
  5. 提供 `make smoke-llm` target 跑真烟测（仅在 `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` 存在时执行，默认跳过）

## 输入 / 输出

**前置任务**：
- Task 01（后端骨架，含 Settings）— ✅
- Task 02（DB 模型）— ✅（不直接依赖）
- Task 03（state machine，Phase）— ✅（不直接依赖）

**交付物清单**：
- `src/app/llm/__init__.py`（空）
- `src/app/llm/router.py`：`LLMRouter` 类 + 响应 pydantic 模型 + 异常层级（约 80 行）
- `src/app/llm/prompts/README.md`：放 prompt 模板的目录说明
- `tests/test_router.py`：mock Instructor 客户端的 ~14 用例
- `pyproject.toml`：runtime +`anthropic>=0.40.0` / `openai>=1.55.0` / `instructor>=1.7.0`；pytest markers + 默认排除 `smoke`
- `.env.example`：+`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` 占位 + `DEFAULT_MODEL_ID`
- `Settings`：+4 个 LLM 默认参数字段
- `Makefile`：`test` target 默认排除 `-m "not smoke"`；新增 `smoke-llm` target

**不交付**（留给后续 task）：
- 流式输出（→ Task 08 SSE-over-WS 基础管道）
- Token cost 美元计算 / 链路追踪（→ Task 05 logging）
- 实际 prompt 模板内容（→ Task 07 起填 `prompts/{planner,critic,merger,executor}.md`）
- LTM_RECALL 注入（→ Task 25）
- LLM 调度策略（→ 暂不做，model_id 由调用方明确指定）
- `complete_text()` 普通文本补全（YAGNI——PRAR 业务全用 structured；真有需求再加）
- **Model Adapter 体系**（router 改造为接 `ModelAdapter` 对象 / `model_adapters` DB 表 / CRUD API / 前端引导式 wizard / `sessions.model_id` → `sessions.adapter_id` fix-up migration）→ **转交 Task 4.1 全栈完成**。本 task 暂保留 `default_model_id: str = "claude-sonnet-4-6"` Settings + prefix-based 路由作为过渡实现，Task 4.1 会替换掉

## 接口设计

### 目录结构（增量）

```
backend/
├── src/app/
│   ├── config.py                     # 修改（+ 4 个 LLM 默认字段）
│   └── llm/
│       ├── __init__.py               # 新增（空）
│       ├── router.py                 # 新增（约 80 行）
│       └── prompts/
│           └── README.md             # 新增
├── tests/
│   └── test_router.py                # 新增（mock Instructor 客户端）
├── pyproject.toml                    # 修改（+3 deps；pytest markers）
├── .env.example                      # 修改（+API key 占位 + DEFAULT_MODEL_ID）
└── Makefile                          # 修改（test target -m "not smoke"；+smoke-llm）
```

### 公开 API（`llm/router.py`）

#### 响应模型（pydantic v2）

```python
from typing import Generic, TypeVar
from pydantic import BaseModel, ConfigDict

T = TypeVar("T", bound=BaseModel)


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class StructuredResponse(BaseModel, Generic[T]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    parsed: T
    raw_text: str          # parsed.model_dump_json()，供日志/replay
    model_id: str
    usage: TokenUsage
    finish_reason: str
```

#### 异常层级

```python
class LLMError(Exception):
    """所有 LLM 调用异常基类。"""


class LLMTransportError(LLMError):
    """网络 / 认证 / 限流 / 模型 ID 不识别等传输层异常。"""

    def __init__(self, message: str, *, model_id: str, cause: Exception | None = None):
        super().__init__(message)
        self.model_id = model_id
        self.cause = cause


class StructuredOutputError(LLMError):
    """模型在 max_retries 次内仍无法产出符合 schema 的 JSON（Instructor 也无法救）。"""

    def __init__(
        self,
        message: str,
        *,
        model_id: str,
        raw_text: str | None = None,
    ):
        super().__init__(message)
        self.model_id = model_id
        self.raw_text = raw_text
```

#### `LLMRouter` 类

```python
class LLMRouter:
    """Instructor + 直连 SDK 路由器。

    职责：
      1. 按 model_id 前缀路由到 anthropic 或 openai 的 Instructor 客户端
      2. 委托 Instructor 处理结构化输出（自动 schema 校验 + 失败重试）
      3. 抽取 token usage / finish_reason 归一化（两家字段名不同）
      4. 异常归一化（Instructor / SDK 异常 → 我们三层 LLMError）

    不在职责内：
      - 流式（complete_*_stream 不在本 task）
      - prompt 模板加载（caller 自己渲染好喂进来）
      - LTM 注入 / cost 统计（分别在 Task 25 / Task 05）
      - complete_text()（YAGNI，真用到再加）
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._anthropic: instructor.AsyncInstructor | None = None
        self._openai: instructor.AsyncInstructor | None = None

    async def complete_structured[T: BaseModel](
        self,
        *,
        model_id: str | None = None,
        system: str,
        user: str,
        schema: type[T],
        max_retries: int = 2,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> StructuredResponse[T]:
        """结构化补全，返回经 pydantic 校验的实例。

        Instructor 在 schema 校验失败时会自动 retry max_retries 次，把上一次错误反馈
        给模型让它修正。仍失败则抛 InstructorRetryException，本方法包成 StructuredOutputError。

        异常路径：
          - 不支持的 model_id           → LLMTransportError
          - SDK 抛（网络/认证/限流）    → LLMTransportError
          - Instructor 重试耗尽         → StructuredOutputError(raw_text=最后一次模型输出)
        """
```

> `model_id=None` 用 `settings.default_model_id`。

### 路由规则（`_client_for(model_id)` 内部）

```python
ANTHROPIC_PREFIXES = ("claude-", "anthropic/")
OPENAI_PREFIXES = ("gpt-", "o1-", "o3-", "openai/")

def _client_for(self, model_id: str) -> instructor.AsyncInstructor:
    if model_id.startswith(ANTHROPIC_PREFIXES):
        if self._anthropic is None:
            self._anthropic = instructor.from_anthropic(AsyncAnthropic())
        return self._anthropic
    if model_id.startswith(OPENAI_PREFIXES):
        if self._openai is None:
            self._openai = instructor.from_openai(AsyncOpenAI())
        return self._openai
    raise LLMTransportError(
        f"Unsupported model_id prefix: {model_id}", model_id=model_id
    )
```

> 客户端 lazy-init：避免 import 时就要求 API key 设置；测试也好 patch。

### 关键调用链（`complete_structured` 内部）

```
1. mid = model_id or settings.default_model_id
2. client = self._client_for(mid)
3. try:
     parsed, raw = await client.chat.completions.create_with_completion(
         model=mid,
         response_model=schema,
         max_retries=max_retries,
         messages=[
             {"role": "system", "content": system},
             {"role": "user", "content": user},
         ],
         temperature=temperature,
         max_tokens=max_tokens,
     )
   except InstructorRetryException as e:
       raise StructuredOutputError(
           f"Schema validation failed after {max_retries} retries",
           model_id=mid,
           raw_text=str(getattr(e, "last_completion", "") or e),
       ) from e
   except Exception as e:
       raise LLMTransportError(str(e), model_id=mid, cause=e) from e
4. return StructuredResponse(
       parsed=parsed,
       raw_text=parsed.model_dump_json(),
       model_id=mid,
       usage=_extract_usage(raw),
       finish_reason=_extract_finish_reason(raw),
   )
```

### `_extract_usage` / `_extract_finish_reason` 归一化

两家原始响应字段名不同，本 task 是唯一处理点：

```python
def _extract_usage(raw: object) -> TokenUsage:
    """raw 可能是 anthropic.types.Message 或 openai.types.ChatCompletion。"""
    usage = getattr(raw, "usage", None)
    if usage is None:
        return TokenUsage()
    # Anthropic: input_tokens, output_tokens
    if hasattr(usage, "input_tokens"):
        i = int(getattr(usage, "input_tokens", 0) or 0)
        o = int(getattr(usage, "output_tokens", 0) or 0)
        return TokenUsage(input_tokens=i, output_tokens=o, total_tokens=i + o)
    # OpenAI: prompt_tokens, completion_tokens, total_tokens
    if hasattr(usage, "prompt_tokens"):
        return TokenUsage(
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
        )
    return TokenUsage()


def _extract_finish_reason(raw: object) -> str:
    """raw 可能是 anthropic.types.Message 或 openai.types.ChatCompletion。"""
    if hasattr(raw, "stop_reason"):  # Anthropic
        return str(getattr(raw, "stop_reason", None) or "unknown")
    choices = getattr(raw, "choices", None)  # OpenAI
    if choices:
        return str(getattr(choices[0], "finish_reason", None) or "unknown")
    return "unknown"
```

### `Settings` 增量

```python
default_model_id: str = "claude-sonnet-4-6"
llm_default_temperature: float = 0.7
llm_default_max_tokens: int = 4096
llm_default_max_retries: int = 2
```

API key **不**在 Settings 里——anthropic / openai SDK 直接读环境变量，避免 key 进 pydantic 模型被日志误输出。

### `.env.example` 增量

```
# LLM
DEFAULT_MODEL_ID=claude-sonnet-4-6

# LLM provider API keys (SDK 直接读取)
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
```

### `Makefile` 增量

```makefile
# test target 改成默认排除 smoke
test:
	uv run pytest -v -m "not smoke"

smoke-llm:
	@if [ -z "$$ANTHROPIC_API_KEY$$OPENAI_API_KEY" ]; then \
		echo "ERROR: 至少设一个 ANTHROPIC_API_KEY 或 OPENAI_API_KEY"; exit 1; \
	fi
	uv run pytest -v -m smoke tests/test_router.py
```

### `pyproject.toml` 增量

```toml
[project]
dependencies = [
    # ... 已有 ...
    "anthropic>=0.40.0",
    "openai>=1.55.0",
    "instructor>=1.7.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = [
    "smoke: real LLM API smoke test, skip by default",
]
```

### `prompts/README.md`

```markdown
# Prompt 模板

本目录存放所有 LLM prompt 模板（`.md` 文件），便于不改代码迭代 prompt。

## 命名约定
- `planner.md`        — Task 07 plan_engine 的 Planner Manager 角色
- `critic.md`         — Task 11 critic 自审
- `merger.md`         — Task 12 review_merger 合 comments → plan v{N+1}
- `executor.md`       — Task 18 action_dispatcher 的 Programmer 角色

## 模板语法
用 Python `str.format(**ctx)` 渲染。占位符用 `{var_name}`。例：

```
你是项目经理。基于以下需求生成结构化计划：
INIT_REQUEST: {init_request}
LTM_RECALL: {ltm_recall}
AVAILABLE_TOOLS: {tool_registry}
```

caller 负责传入 ctx，router 不渲染（router 收到的就是渲染后的最终 string）。
```

## 文件清单

| 路径 | 类型 | 说明 |
|------|------|------|
| `backend/src/app/llm/__init__.py` | 新增 | 空，包标记 |
| `backend/src/app/llm/router.py` | 新增 | LLMRouter + 响应模型 + 异常层级（约 80 行） |
| `backend/src/app/llm/prompts/README.md` | 新增 | prompt 命名/语法约定 |
| `backend/tests/test_router.py` | 新增 | mock Instructor 客户端的 ~14 用例 |
| `backend/pyproject.toml` | 修改 | +3 deps；pytest markers + smoke 默认排除 |
| `backend/.env.example` | 修改 | +DEFAULT_MODEL_ID + API key 占位 |
| `backend/src/app/config.py` | 修改 | +4 个 LLM 默认参数字段 |
| `backend/Makefile` | 修改 | test target -m "not smoke"；+smoke-llm |

5 新增 + 4 修改 = 9 文件改动。

## 实施步骤

1. `mkdir -p backend/src/app/llm/prompts`
2. `pyproject.toml` 加 `anthropic` / `openai` / `instructor` + pytest markers + `uv sync`
3. 写 `llm/__init__.py` 空 / `llm/prompts/README.md`
4. 写 `llm/router.py`（约 80 行）
5. `.env.example` 与 `Settings` 加 LLM 字段
6. 写 `tests/test_router.py`（用 `unittest.mock.MagicMock` patch `instructor.from_anthropic` / `instructor.from_openai`）
7. `Makefile` 改 test target + 新增 smoke-llm
8. `make test` 全绿（默认 not smoke）
9. `make lint && make typecheck` 零警告
10. （可选）`ANTHROPIC_API_KEY=... make smoke-llm` 真烟测一次

## 测试清单

### 单元测试（mock Instructor 客户端）

| # | 测试 | 断言 |
|---|------|------|
| T1 | `test_routes_claude_to_anthropic_client` | 调用 `model_id="claude-sonnet-4-6"`，验证从 `anthropic` Instructor 客户端取出，未触碰 openai |
| T2 | `test_routes_gpt_to_openai_client` | 调用 `model_id="gpt-5"`，验证用 `openai` 客户端，未触碰 anthropic |
| T3 | `test_routes_o1_o3_prefix_to_openai` | `o1-mini` / `o3-large` 路由到 openai |
| T4 | `test_unknown_model_id_raises_transport_error` | `model_id="qwen-turbo"`，抛 `LLMTransportError` |
| T5 | `test_uses_default_model_when_id_none` | `model_id=None`，验证用 `settings.default_model_id` |
| T6 | `test_returns_structured_response_with_parsed_pydantic` | mock `create_with_completion` 返回 `(FooSchema(name="x"), raw)`，router 返回 `StructuredResponse[FooSchema]` 含 `parsed.name=="x"` |
| T7 | `test_instructor_retry_exception_becomes_structured_output_error` | mock 抛 `InstructorRetryException(last_completion="bad")`，router 抛 `StructuredOutputError(raw_text="bad")` |
| T8 | `test_arbitrary_exception_wrapped_as_transport_error` | mock 抛 `RuntimeError("boom")`，router 抛 `LLMTransportError("boom", model_id=..., cause=...)` |
| T9 | `test_extracts_anthropic_usage` | mock raw 是 `MockAnthropicMessage(usage=MockUsage(input_tokens=10, output_tokens=20))`，router `usage.total_tokens == 30` |
| T10 | `test_extracts_openai_usage` | mock raw 含 `usage.prompt_tokens=15, completion_tokens=25, total_tokens=40`，router `usage.total_tokens == 40` |
| T11 | `test_extracts_anthropic_finish_reason` | mock `stop_reason="end_turn"`，router `finish_reason=="end_turn"` |
| T12 | `test_extracts_openai_finish_reason` | mock `choices[0].finish_reason="stop"`，router `finish_reason=="stop"` |
| T13 | `test_passes_max_retries_temperature_max_tokens` | mock 验证 kwargs 透传给 `create_with_completion` |
| T14 | `test_raw_text_is_parsed_model_dump_json` | mock 返回特定 parsed 实例，router `raw_text == parsed.model_dump_json()` |

### Smoke 测试（标 `@pytest.mark.smoke`，默认跳过）

| # | 测试 | 断言 |
|---|------|------|
| S1 | `test_smoke_anthropic_structured` (skipif no `ANTHROPIC_API_KEY`) | 真调 Claude，给极简 schema (`class Greeting(BaseModel): hi: str`)，验证返回非空字符串 |
| S2 | `test_smoke_openai_structured` (skipif no `OPENAI_API_KEY`) | 同上，对 GPT |

### 边缘情况

- `usage` / `finish_reason` 字段缺失 → `_extract_*` 返回默认值（`TokenUsage()` / `"unknown"`），不抛
- pydantic schema 含 `Annotated` / `Literal` / `Union` → `model_json_schema()` 直接支持，Instructor 也支持
- Schema 含 `additionalProperties` 不为 false：OpenAI strict mode 不接受；Task 07 起 plan schema 设计时显式 `model_config = ConfigDict(extra="forbid")` 让 pydantic 输出 strict 兼容 schema
- Instructor 默认用 OpenAI 风格 messages（含 `system` role），对 Anthropic 客户端会自动转换 `system` 参数；不需要 caller 知道差异

### 集成测试入口

```bash
cd backend && make test                                # 默认排除 smoke
cd backend && ANTHROPIC_API_KEY=xxx make smoke-llm     # 真烟测
```

## 风险与未决

### 已识别风险

| 风险 | 缓解 |
|------|------|
| `instructor>=1.7` 后续 API 变化 | 锁 `>=1.7.0`；有 14 个单测兜底 API 行为；新版本升级前先跑 test |
| `anthropic` / `openai` SDK 升级 break Instructor 兼容 | 三个 dep 的升级主人手动控制；smoke 测试做集成验证 |
| `_extract_usage` 漏掉某家 SDK 改字段名 | 默认 `TokenUsage()` 安全降级；CLAUDE.md "silent failure" 警告——后续 Task 05 logging 加 metric 监控 0-token 异常率 |
| Anthropic SDK 的 system prompt 处理 | Instructor 自动处理；不需要 router 关心 |
| Instructor `create_with_completion` 在 Anthropic 上的返回 raw 类型 | 测试覆盖两家 raw 字段差异（T9-T12）；如某天 Instructor 改返回类型，单测立刻红 |
| API key 泄漏到 commit | `.env.example` 占位为空；`.gitignore` 已含 `.env*` |
| Test 里把真 LLM 调跑了 | `make test` 默认 `-m "not smoke"`；smoke 测试单独 target |
| 客户端 lazy-init 在测试里 monkeypatch 时机问题 | 测试用 fixture 直接 set `router._anthropic` / `router._openai` 为 mock，绕过 init |

### 已决策（默认值，主人不反对就这么走）

| # | 项目 | 决策 | 反对就告诉我 |
|---|------|------|-------------|
| Q1 | LLM 客户端层 | **Instructor + 直连 anthropic/openai SDK** | "回到 LiteLLM" / "再加一家" |
| Q2 | 同步 vs async | **async only**（FastAPI） | "也提供 sync 版" |
| Q3 | 默认 temperature | **0.7**（主人定） | "改回 0.0" |
| Q4 | 默认 max_retries（Instructor 自动重试） | **2** | "0（禁重试）" / "5" |
| Q5 | 重试时让 caller 决定（除 Instructor 自带的 schema 重试） | **是**（外层重试 caller 自己加） | "router 内部加 max_attempts" |
| Q6 | 默认 model | **`claude-sonnet-4-6`** | "`gpt-5`" / "其它" |
| Q7 | 测试 mock 方案 | **`MagicMock` patch `instructor.from_anthropic` / `from_openai`** | "改用 respx" |
| Q8 | smoke 测试默认排除 | **是**（pytest -m "not smoke"） | "默认跑" |
| Q9 | API key 通过 env vs Settings | **env**（SDK 约定 + 不进 pydantic 模型） | "进 Settings 但 SecretStr 包装" |
| Q10 | prompts/ 何时填 | **本 task 只放 README**；真 prompt 留 Task 07/11/12/18 | "本 task 就放 stub" |
| Q11 | 是否在本 task 提供 `complete_text()` | **不提供（YAGNI）**；PRAR 业务全用 structured；真用到再加 | "也提供" |
| Q12 | model_id 路由前缀策略 | **prefix-based**（`claude-`/`anthropic/` → anthropic；`gpt-`/`o1-`/`o3-`/`openai/` → openai）；不识别抛 `LLMTransportError` | "用 enum 显式映射" |

如以上 12 项主人无异议，请直接回复 `APPROVED`，我立即按本设计落代码并 commit。

## 已做的设计决策（记录依据）

| 决策 | 理由 |
|------|------|
| 弃用 LiteLLM 改 Instructor + 直连 SDK | 项目实际只需 2 家 provider，LiteLLM 80% 功能用不上；Instructor 是 Pydantic 原生且自带 schema 重试，恰好对应 Critic / Merger 业务需求；CLAUDE.md "最少代码解决问题" 硬约束 |
| Router 是**类**而不是模块级函数 | 持有 settings 引用 + lazy-init 客户端；将来加 logger / cost tracker 不破坏接口 |
| 异常三层（LLMError → LLMTransportError / StructuredOutputError） | caller 区分"网络挂"vs"模型不会写"，做不同处理 |
| `StructuredResponse[T]` 用 pydantic Generic 而非 TypedDict | pydantic 校验 + JSON 序列化天然支持；type-check 准确 |
| 同时返回 `parsed` 和 `raw_text` | `raw_text = parsed.model_dump_json()` 是正规化的 JSON 字符串，给日志/replay 用 |
| `model_id` 在响应里冗余存一份 | 调用方可能不传（用 default），返回时让调用方知道实际用哪个 |
| Router 不渲染 prompt 模板 | 渲染逻辑在 caller；router 只管"把 messages 喂模型" |
| Router 不做 LTM 注入 | 分层职责；LTM 是业务逻辑，不是 LLM 调用基建 |
| `temperature=0.0` 默认 | 结构化输出 + Plan/Critic/Merger 业务都要可复现 |
| `max_tokens=4096` 默认 | 够大多数 plan 输出；不够 caller 显式调大 |
| 客户端 lazy-init | 避免 import 时就要求 API key；测试也好 patch |
| prefix-based 路由（`claude-` → anthropic，`gpt-`/`o1-`/`o3-` → openai） | 简单、足够；遇到 namespace prefix（`anthropic/` / `openai/`）也支持；新增 provider 加 5 行分支 |
| 不抽象 provider adapter ABC | YAGNI——两家差异极小，Instructor 已抹平；自己再抽一层属于过度设计 |
| 不在本 task 提供 `complete_text()` | YAGNI——PRAR 业务全是结构化（Plan/Critic/Merger/Executor 都输出 JSON）；真有需求再加 |
| 不在 router 内做外层 retry | LiteLLM-vs-direct 之争已避开；外层 retry 业务语义不同（要不要换 model？要不要改 prompt？），让 caller 决定 |
| pytest mark `smoke` 默认排除 | CLAUDE.md "测试要 mock LLM call，禁止 CI 真调 API" 硬约束 |

---

**Q1-Q12 已锁定默认，主人审阅整体方案，回 `APPROVED` 即开始落代码 + 跑测试 + commit（设计与代码同 commit）。**
