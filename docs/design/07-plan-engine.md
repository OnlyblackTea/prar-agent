# 07. Plan 引擎

> **状态**：APPROVED，已实施（commit `6a6f3ca`）
> **依赖**：Task 04.1b（router `complete_structured` + `ResolvedAdapter`）、Task 05（logging）
> **被依赖**：Task 08（WebSocket 流式推送 plan 节点）、Task 10（前端渲染 plan document）
> **commit 范围**：单个 commit

---

## 1. 目标

- **一句话**：给定 init_request → 调 LLM 生成结构化 Plan JSON → Critic 自审修正 → 返回可渲染的 Plan 文档。
- **验收标准**：
  1. `PlanEngine.generate(init_request, adapter) -> PlanDocument` 生成含 heading / paragraph / decision / glossary / step 节点的结构化文档
  2. Critic 必跑：Planner 输出 → Critic 审查 → 合并修正 → 返回终稿
  3. LTM 接口预留：`ltm_recall: list[str] = []` 参数，M4 填实现
  4. 节点 ID 由框架后处理分配（`dec_001` / `step_001` / `gls_001`），LLM 不生成 ID
  5. 决策题第一轮全 `blocking=True`（硬编码）
  6. Prompt 存 `llm/prompts/*.md`，不硬编码在 .py 中
  7. `make test` 全绿（mock LLM，不真调 API）

---

## 2. 架构设计

### 2.1 数据流

```
init_request + ltm_recall + available_tools
        │
        ▼
  ┌─────────────┐    structured output     ┌───────────┐
  │   Planner    │ ─────────────────────▶  │ Raw Plan  │
  │  (LLM call)  │    PlanDocument schema  │  (draft)  │
  └─────────────┘                          └─────┬─────┘
                                                  │
                                                  ▼
                                         ┌───────────────┐
                                         │  assign_ids() │  框架后处理
                                         └───────┬───────┘
                                                  │
                                                  ▼
                                         ┌───────────────┐    structured output
                                         │    Critic     │ ─────────────────────▶
                                         │  (LLM call)   │    CriticResult schema
                                         └───────┬───────┘
                                                  │
                                                  ▼
                                         ┌───────────────┐
                                         │ apply_critic()│  合并修正
                                         └───────┬───────┘
                                                  │
                                                  ▼
                                           PlanDocument (final)
```

### 2.2 模块结构

```
app/
├── core/
│   └── plan_engine.py         # 新增：PlanEngine 类
├── llm/
│   ├── prompts/
│   │   ├── planner.md         # 新增：Planner system prompt
│   │   └── critic.md          # 新增：Critic system prompt
│   └── router.py              # 已有，被调用
└── shared/
    └── schemas.py             # 改造：新增 PlanDocument 到 SHARED_SCHEMAS
```

---

## 3. Pydantic Schema（LLM structured output 目标）

### 3.1 Plan 节点类型

```python
# app/core/plan_schemas.py

from pydantic import BaseModel, Field

class TextContent(BaseModel):
    """内联文本。"""
    type: str = "text"
    text: str

class HeadingNode(BaseModel):
    type: str = "heading"
    level: int = Field(ge=1, le=3)
    text: str

class ParagraphNode(BaseModel):
    type: str = "paragraph"
    text: str

class DecisionNode(BaseModel):
    type: str = "decision"
    id: str = ""  # 框架后处理分配
    question: str
    kind: str = "single_choice"  # single_choice | multi_choice
    options: list[str] = Field(min_length=2, max_length=6)
    answer: str | None = None
    blocking: bool = True  # 第一轮硬编码 True

class GlossaryNode(BaseModel):
    type: str = "glossary"
    id: str = ""  # 框架后处理分配
    term: str
    definition: str

class StepNode(BaseModel):
    type: str = "step"
    id: str = ""  # 框架后处理分配
    title: str
    description: str
    tool: str  # 必须是已注册的 tool 名
    tool_args: dict = Field(default_factory=dict)
    rerunnable: bool = True

# Discriminated union
PlanNode = HeadingNode | ParagraphNode | DecisionNode | GlossaryNode | StepNode

class PlanDocument(BaseModel):
    """Planner LLM 输出的结构化计划。"""
    title: str
    summary: str
    nodes: list[PlanNode]
```

### 3.2 Critic 输出

```python
class CriticAction(BaseModel):
    """Critic 对单个节点的修正指令。"""
    node_index: int = Field(ge=0, description="目标节点在 nodes 列表中的下标")
    action: str  # "remove" | "replace" | "insert_after"
    reason: str
    replacement: PlanNode | None = None  # action=replace/insert_after 时提供

class CriticResult(BaseModel):
    """Critic 输出：一组修正 + 全局评语。"""
    actions: list[CriticAction] = Field(default_factory=list)
    overall_comment: str = ""
```

---

## 4. Prompt 设计

### 4.1 `llm/prompts/planner.md`

```markdown
你是一个严谨的项目规划师。基于用户需求，输出结构化的项目执行计划。

## 硬约束

- 决策题（decision）最多 5 个，只问无法合理推断的关键盲点
- 每个执行步骤（step）必须绑定一个工具（tool），工具白名单见下方
- 名词解释（glossary）面向非专业用户，解释技术术语
- 你只做规划，不写代码、不动手执行
- 不要生成节点 ID（id 字段留空字符串，框架会自动分配）

## 输入

- **用户需求**：{init_request}
- **历史记忆**：{ltm_recall}
- **可用工具**：{available_tools}

## 输出格式

严格按照给定的 JSON Schema 输出，不要添加任何前缀、后缀或解释。
```

### 4.2 `llm/prompts/critic.md`

```markdown
你是一个计划评审专家。对以下项目计划进行评审，输出修正指令。

## 评审维度

1. **冗余决策题**：删除可通过上下文合理推断的决策题
2. **未声明假设**：如果 plan 隐含假设但未明示，添加 paragraph 说明
3. **非原子步骤**：如果某个 step 包含多个独立操作，拆分为多个 step
4. **工具参数完备**：检查每个 step 的 tool_args 是否足够执行
5. **顺序合理性**：检查步骤是否有逻辑依赖被打乱

## 输入

- **计划文档**：{plan_json}

## 输出格式

严格按照给定的 JSON Schema 输出修正指令列表。
如果计划已经很好，actions 可以为空列表。
```

---

## 5. PlanEngine 实现

```python
# app/core/plan_engine.py

class PlanEngine:
    def __init__(self, router: LLMRouter) -> None:
        self._router = router
        self._planner_prompt = _load_prompt("planner.md")
        self._critic_prompt = _load_prompt("critic.md")

    async def generate(
        self,
        *,
        init_request: str,
        adapter: ResolvedAdapter,
        ltm_recall: list[str] | None = None,
        available_tools: list[str] | None = None,
    ) -> PlanDocument:
        """生成 Plan：Planner → assign_ids → Critic → apply_critic。"""
        # 1. Planner LLM call
        plan = await self._call_planner(
            init_request=init_request,
            adapter=adapter,
            ltm_recall=ltm_recall or [],
            available_tools=available_tools or ["shell", "fs.read", "fs.write"],
        )

        # 2. 框架后处理：分配节点 ID
        plan = _assign_ids(plan)

        # 3. Critic LLM call
        critic_result = await self._call_critic(plan=plan, adapter=adapter)

        # 4. 合并修正
        plan = _apply_critic(plan, critic_result)

        return plan
```

### 5.1 节点 ID 分配

```python
def _assign_ids(plan: PlanDocument) -> PlanDocument:
    """框架后处理：为 decision/glossary/step 分配确定性 ID。"""
    counters = {"decision": 0, "glossary": 0, "step": 0}
    prefixes = {"decision": "dec", "glossary": "gls", "step": "step"}
    new_nodes = []
    for node in plan.nodes:
        if node.type in counters:
            counters[node.type] += 1
            node = node.model_copy(
                update={"id": f"{prefixes[node.type]}_{counters[node.type]:03d}"}
            )
        new_nodes.append(node)
    return plan.model_copy(update={"nodes": new_nodes})
```

### 5.2 Critic 修正合并

```python
def _apply_critic(plan: PlanDocument, critic: CriticResult) -> PlanDocument:
    """应用 Critic 修正：按 node_index 倒序处理避免下标偏移。"""
    nodes = list(plan.nodes)
    for action in sorted(critic.actions, key=lambda a: a.node_index, reverse=True):
        idx = action.node_index
        if idx >= len(nodes):
            continue  # 越界跳过
        if action.action == "remove":
            nodes.pop(idx)
        elif action.action == "replace" and action.replacement:
            nodes[idx] = action.replacement
        elif action.action == "insert_after" and action.replacement:
            nodes.insert(idx + 1, action.replacement)
    # 重新分配 ID（critic 可能改变了节点顺序/数量）
    result = plan.model_copy(update={"nodes": nodes})
    return _assign_ids(result)
```

---

## 6. 文件清单

| 路径 | 类型 | 说明 |
|------|------|------|
| `backend/src/app/core/plan_schemas.py` | **新增** | PlanNode / PlanDocument / CriticResult pydantic 模型 |
| `backend/src/app/core/plan_engine.py` | **新增** | PlanEngine 类 + _assign_ids + _apply_critic + _load_prompt |
| `backend/src/app/llm/prompts/planner.md` | **新增** | Planner system prompt |
| `backend/src/app/llm/prompts/critic.md` | **新增** | Critic system prompt |
| `backend/src/app/shared/schemas.py` | 改造 | 新增 PlanDocument + CriticResult 到 SHARED_SCHEMAS |
| `shared/schema.json` | 重新生成 | 含新增 schema |
| `backend/tests/test_plan_engine.py` | **新增** | Plan 引擎测试 |

---

## 7. 实施步骤

| # | 步骤 | 验证 |
|---|------|------|
| 1 | 写 `core/plan_schemas.py` | import 不报错；PlanDocument 实例化通过 |
| 2 | 写 prompt 文件 `planner.md` / `critic.md` | 文件存在且可读 |
| 3 | 写 `core/plan_engine.py` | import 不报错 |
| 4 | 更新 `shared/schemas.py` + `make gen-schema` | schema.json 含新类型 |
| 5 | 写 `tests/test_plan_engine.py`（全 mock LLM） | make test 全绿 |
| 6 | `make lint && make test` | 0 error |

---

## 8. 测试清单

### `tests/test_plan_engine.py`（新增，全部 mock LLM）

| # | 测试 | 断言 |
|---|------|------|
| T1 | `test_assign_ids_numbers_sequentially` | 3 个 step + 2 个 decision → id 分别为 step_001/002/003、dec_001/002 |
| T2 | `test_assign_ids_skips_paragraph_and_heading` | paragraph 和 heading 无 id 字段变化 |
| T3 | `test_assign_ids_glossary` | glossary 节点获得 gls_001 等 id |
| T4 | `test_apply_critic_remove` | critic action=remove 删除指定节点 |
| T5 | `test_apply_critic_replace` | critic action=replace 替换指定节点 |
| T6 | `test_apply_critic_insert_after` | critic action=insert_after 在指定位置后插入 |
| T7 | `test_apply_critic_out_of_bounds_skipped` | node_index 越界不抛异常 |
| T8 | `test_apply_critic_reassigns_ids` | critic 修正后 id 被重新分配 |
| T9 | `test_generate_calls_planner_then_critic` | mock router → generate() 调两次 complete_structured（planner + critic） |
| T10 | `test_generate_passes_ltm_recall_to_prompt` | ltm_recall 内容出现在 planner 的 user prompt 中 |
| T11 | `test_generate_default_tools` | 不传 available_tools 时默认包含 shell/fs.read/fs.write |
| T12 | `test_plan_document_schema_valid` | PlanDocument.model_json_schema() 生成有效 JSON Schema |
| T13 | `test_decision_blocking_always_true` | Planner 输出的 decision 节点 blocking 固定为 True |
| T14 | `test_load_prompt_reads_file` | _load_prompt 正确读取 .md 文件内容 |

### 已有测试无回归

所有已有 100 测试不受影响（plan_engine 是新增模块，无侵入性改动）。

---

## 9. 设计决策

| 决策 | 理由 |
|------|------|
| Discriminated union `PlanNode` 而非单一大 model | 各节点类型字段差异大；前端按 `type` dispatch 渲染 |
| Critic 输出 `CriticAction` 列表而非 JSON Patch | RFC 6902 对 LLM 来说太复杂，容易格式错误；action 列表语义更清晰 |
| `_assign_ids` 在 Planner 之后、Critic 之前跑，Critic 之后再跑一次 | Critic 需要引用 node_index（需稳定下标），修正后 id 可能变化需重分配 |
| `blocking=True` 硬编码不让 LLM 决定 | DESIGN.md §8.6 明确要求；二轮 review 后由用户手动降级 |
| Prompt 存 `.md` 文件 | CLAUDE.md 硬约束：`prompt 必须存 backend/llm/prompts/*.md` |
| `ltm_recall` 默认空列表 | M4 填实现，M1 只预留接口 |
| `available_tools` 默认三个基础工具 | Task 17 实现 shell/fs.read/fs.write，Plan 引擎需要知道工具白名单以约束 LLM |

---

## 10. 风险

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| LLM 无法可靠生成 discriminated union 的 `type` 字段 | 中 | 中 | Instructor 强制 schema 校验 + max_retries=2 |
| Critic 修正的 node_index 与实际不一致 | 中 | 低 | 越界检查 + 倒序处理；最坏情况：critic 修正被丢弃，不 crash |
| Prompt 过长导致 token 浪费 | 低 | 低 | Prompt 模板精简；schema 由 Instructor 自动注入，不手写 |
| PlanDocument 与前端 ProseMirror 结构不完全匹配 | 中 | 中 | Task 10 前端渲染时做 adapter 层转换；本 task 的 schema 是逻辑层，不是渲染层 |

---

## 11. 决策题

| # | 题目 | 选项 | 推荐 |
|---|------|------|------|
| Q1 | Critic 修正格式 | A=CriticAction 列表（推荐）/ B=RFC 6902 JSON Patch / C=直接输出修正后的完整 Plan | A |
| Q2 | PlanNode union 方式 | A=`type` 字段 discriminated union（推荐）/ B=单一 model 含 optional 字段 | A |

---

主人审阅后回 `APPROVED`（或修改意见 + 决策题选择）即开始编码。
