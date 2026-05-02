# PRAR-Agent 概要设计

> 本文档冻结当前的整体架构决策。修改前必须在 PR description 中说明动机；个别 step 的详细设计放 `docs/design/NN-*.md`。

## 1. 目标与范围

构建一个 Agent 框架：

- 接入任意大模型（用户在前端选）
- 强制走 **Plan → User Review → Action → User Review** 4 阶段循环
- Plan 阶段大模型输出"类飞书文档"的可读计划：含文本介绍、名词解释、决策选择题、可评论锚点
- Action 阶段大模型调用可插拔工具，结果再次进入用户 review
- 角色比喻：**Manager（Planner）↔ Programmer（Executor）**
- **不微调任何模型**，纯框架代码 + 结构化输出 + 上下文工程实现

## 2. 用户回答确定的边界

| # | 维度 | 决策 |
|---|------|------|
| 1 | Action 阶段做什么 | 抽象 **Tool 模块**，后期可插拔各类工具 |
| 2 | 协作模式 | **单用户单 session**（无并发评论冲突） |
| 3 | Plan 存储 | **PostgreSQL + 富文本（ProseMirror JSON 存 JSONB）** |
| 4 | LLM 路由 | **用户在前端选择**模型 |
| 5 | 失败回滚 | **支持局部工具重跑**，全程 git 管理 |
| 6 | 目标形态 | 类 VSCode 的 **Web/桌面应用**（先 Web 后 Tauri 壳） |
| 7 | 长期记忆 | 是 |

## 3. 技术栈

| 层 | 选型 | 理由 |
|----|------|------|
| 桌面壳 | **Tauri 2.x**（晚期再套） | 体积/内存优于 Electron；早期纯 Web 调试更快 |
| 前端 | React + TS + Vite + **Tiptap** + Monaco | Tiptap 自定义节点能力强，原生支持评论锚定 |
| 后端 | **FastAPI** + WebSocket（SSE-over-WS） | Python LLM 生态最全 |
| DB | **PostgreSQL 16 + pgvector** | JSONB 存富文本树 + 向量检索一锅端 |
| LLM 路由 | **LiteLLM** | 100+ 模型统一接口 |
| Git | **pygit2** | 比 subprocess 快，类型安全 |
| 沙箱 | Docker container per session | Tool 统一执行环境 |

## 4. 模块拆分

```
prar-agent/
├── frontend/
│   └── src/
│       ├── editor/
│       │   ├── PlanDocEditor.tsx          # 核心：Tiptap 编辑器
│       │   ├── nodes/
│       │   │   ├── DecisionNode.ts        # 决策题（radio/checkbox）
│       │   │   ├── GlossaryNode.ts        # 名词解释（hover 卡片）
│       │   │   ├── StepNode.ts            # 可执行步骤
│       │   │   └── AnchorMark.ts          # 评论锚点（mark 而非 node）
│       │   └── extensions/
│       │       ├── CommentThread.ts       # 高亮 + 侧边栏
│       │       └── VersionDiff.ts         # Plan vN vs vN+1
│       ├── workspace/                     # VSCode 风格多面板
│       └── modelpicker/                   # LLM 路由选择
├── backend/
│   ├── core/
│   │   ├── state_machine.py               # ★ 4 阶段硬编码状态机
│   │   ├── plan_engine.py                 # 生成/修订 Plan
│   │   ├── critic.py                      # self-critique pass
│   │   ├── action_dispatcher.py           # 工具调度
│   │   ├── review_merger.py               # comments → plan patch
│   │   └── checkpoint.py                  # git 快照
│   ├── llm/
│   │   ├── router.py                      # LiteLLM 封装 + schema 归一化
│   │   ├── prompts/{planner,critic,executor,merger}.md
│   │   └── schema.py                      # 输出 JSON Schema
│   ├── memory/
│   │   ├── short_term.py                  # session 内
│   │   ├── long_term.py                   # pgvector
│   │   └── consolidator.py                # 后台任务，离线提炼
│   ├── tools/
│   │   ├── base.py                        # Tool ABC
│   │   ├── registry.py
│   │   ├── sandbox.py                     # Docker 执行器
│   │   └── builtin/{shell,fs,http}.py
│   ├── api/{session,plan,action,ws}.py
│   └── db/{models.py, migrations/}
└── shared/
    └── schema.json                        # 前后端共享 JSON Schema
```

## 5. 状态机（硬编码，模型不参与转移）

```python
class Phase(Enum):
    INIT = "init"
    PLANNING = "planning"
    PLAN_REVIEW = "plan_review"
    ACTING = "acting"
    ACTION_REVIEW = "action_review"
    DONE = "done"

TRANSITIONS = {
    Phase.INIT:          [Phase.PLANNING],
    Phase.PLANNING:      [Phase.PLAN_REVIEW],
    Phase.PLAN_REVIEW:   [Phase.PLANNING, Phase.ACTING],     # 改 plan / 推进
    Phase.ACTING:        [Phase.ACTION_REVIEW],
    Phase.ACTION_REVIEW: [Phase.ACTING, Phase.PLANNING, Phase.DONE],
}
```

转移触发权全部在用户/框架，**LLM 无权决定阶段切换**。

## 6. 核心数据模型

### 6.1 Plan 文档（ProseMirror JSON, 存 JSONB）

```json
{
  "type": "doc",
  "content": [
    {"type": "heading", "attrs": {"level": 1}, "content": [...]},
    {"type": "paragraph", "content": [...]},
    {"type": "decision", "attrs": {
      "id": "dec_001",
      "question": "...",
      "kind": "single_choice",
      "options": ["A", "B"],
      "answer": null,
      "blocking": true
    }},
    {"type": "glossary", "attrs": {"term": "OT", "definition": "..."}},
    {"type": "step", "attrs": {
      "id": "step_001",
      "tool": "shell",
      "args": {...},
      "rerunnable": true,
      "anchor_id": "anc_xxx"
    }, "content": [...]}
  ]
}
```

### 6.2 Comment（评论锚定）

```python
class Comment(BaseModel):
    id: str
    plan_version: int
    anchor_id: str             # 优先匹配
    quote: str                 # 锚定时原文，防漂移用
    quote_context: str         # 前后各 50 字符
    body: str
    resolved: bool = False
```

**锚定算法**：

1. 优先按 `anchor_id` 直查
2. 找不到（plan 已改）则 fuzzy match `quote + quote_context`
3. 命中率 < 0.7 时，前端显示"评论已悬空"，让用户重新指认 — **不让模型瞎猜**

### 6.3 Tool 抽象

```python
class Tool(ABC):
    name: str
    json_schema: dict          # function-calling schema
    rerunnable: bool = True

    @abstractmethod
    async def execute(self, args: dict, ctx: ExecContext) -> ToolResult: ...

class ToolResult(BaseModel):
    ok: bool
    output: str
    artifacts: list[Path] = []   # 待 git commit
    git_commit: str | None = None
```

### 6.4 Git Checkpoint 规范

每个 step 一个 commit，message 固定：

```
[sess_<8>] step_<id>: <短描述>

phase: ACTING → ACTION_REVIEW
tool: shell
plan_version: 3
```

回滚 = `git revert <commit>` + 重跑该 step。Plan 本身也走 git：每个版本存 `.plan/v{N}.json`，diff 可视化体验拉满。

### 6.5 长期记忆（pgvector）

```sql
CREATE TABLE memory (
    id UUID PRIMARY KEY,
    user_id UUID,
    kind TEXT CHECK (kind IN ('episodic','semantic','procedural')),
    content TEXT,
    embedding vector(1536),
    importance REAL DEFAULT 0.5,
    last_accessed TIMESTAMPTZ DEFAULT NOW(),
    access_count INT DEFAULT 0,
    source_session UUID
);
CREATE INDEX ON memory USING hnsw (embedding vector_cosine_ops);
```

| 层 | 内容 | 写入时机 |
|----|------|---------|
| Episodic | "2026-04-15 用 Postgres 实现 X" | session DONE 时摘要 |
| Semantic | "用户偏好 Postgres > MongoDB" | consolidator 离线提炼 |
| Procedural | "shell→git→test 高频组合" | 工具序列模板化 |

**Plan 阶段开始**：`top_k(query=init_request, k=10)` → rerank（importance × recency）→ 注入 prompt top-5。

## 7. 关键 Prompt 骨架

### 7.1 Planner (Manager)

```
你是项目经理。基于用户需求，输出**结构化计划 JSON**（schema 见下）。

硬约束：
- 决策题最多 5 个，只问无法合理推断的盲点
- 每个 step 必须绑定一个已注册的 tool（白名单：{TOOL_REGISTRY}）
- 名词解释面向非专业用户
- 不写代码、不动手

输入：
- INIT_REQUEST: {init}
- LTM_RECALL: {top-k 长期记忆}
- AVAILABLE_TOOLS: {registry}

只输出 JSON，无前后缀。
```

### 7.2 Critic（必跑，不可选）

```
对以下 Plan 做评审，输出 JSON Patch (RFC 6902)：
- 删除可推断的决策题
- 标注未声明假设
- 拆分非原子的 step
- 检查 tool 参数完备
```

### 7.3 Review Merger

```
Plan v{N} + 用户评论列表（含 quote）→ 生成 Plan v{N+1}。
对每条评论必须给出：处理方式 (accept/reject/partial) + 一句话理由。
```

### 7.4 Executor (Programmer)

```
执行 step: {step}
按 ReAct：think → call_tool → observe → ...
完成调用 finish_step(summary, artifacts)。
```

## 8. 已知坑（设计期就要规避）

1. **不同 LLM 的 structured output 能力差异巨大**——Claude prefill `{` + stop sequence、OpenAI strict mode、Gemini responseSchema、Qwen function call。LiteLLM 只统一 transport，**schema 归一化在 router 层自己做**。
2. **流式渲染富文本不要逐 token 推**——按 ProseMirror node 边界 chunk 推（10-50 tokens/帧），否则前端高频 reflow 卡顿。
3. **Tool 沙箱默认禁网**，只在 schema 显式声明 `network: true` 才放行。
4. **Plan 节点 ID 由框架后处理分配**（`dec_001/step_001/anc_xxx`），prompt 里只让模型给位置标记，避免格式漂移和重复。
5. **Comment 锚定永远会有边缘 case**——准备"悬空评论" UI 状态。
6. **决策题 blocking 性硬编码**：第一轮所有决策默认 blocking，二轮 review 后由用户手动降级，**不交给模型判定**。

## 9. 不在范围

- 多用户协作 / OT/CRDT
- 实时多人编辑
- 移动端 / iPad
- Plan 文档版本之外的全文搜索（pgvector 只索引 memory）
- 商业化计费 / 多租户
