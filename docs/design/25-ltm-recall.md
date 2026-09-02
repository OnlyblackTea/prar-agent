# 25. LTM_RECALL 实际注入 Planner（填实 M1 存根）

> 对应 ROADMAP M4 #25：`LTM_RECALL 实际注入 Planner（填实 M1 留好的接口存根）`，验收：**第二次相似 session 能看到记忆使用**。
> 上游：任务 23 已交付 episodic 写入，任务 24 已交付 semantic/procedural 提炼；本任务把三层记忆在 plan 生成时真实检索并注入 planner prompt。ROADMAP 明确"M1 已埋好 `ltm_recall` 接口存根，M4 只填实现，不动 M1 接口"。

## 目标

- 一句话目标：后端在 `PlanEngine.generate` 之前用 `MemoryService.search` 对 `init_request` 做三层记忆语义检索，加权排序后注入 planner 的 `{ltm_recall}` 段落——M1 留的 `generate(ltm_recall)` 存根参数从此由调用方真实填入。
- 验收标准：
  1. 第二次相似 session 提需求 → plan 能引用历史决策；日志 `ltm_recall` 事件可证命中（hits > 0）。
  2. 检索：`init_request` 嵌入 → 三层（episodic/semantic/procedural）余弦 top-k → `cosine × importance × recency` 加权 → top-n 注入。
  3. 空记忆库 / 零命中：plan 正常生成（注入"无"），零额外 LLM 成本（只多 1 次 embedding）。
  4. recall 的 embedding 失败 → WS error `embedding_failed`（不静默降级，符合"禁止静默捕获"铁律）。
  5. 命中行 `access_count` +1（`search()` 自带记账）——"记忆使用"的 DB 侧硬证据。

## 现状调研（2026-09-03）

| 依赖项 | 现状 | 结论 |
|---|---|---|
| M1 存根链路 | `GenerateMessage.ltm_recall`（前端可选，从不传）→ ws_plan → `PlanEngine.generate(ltm_recall)` → planner.md `{ltm_recall}` | 后端从不检索；存根接口（generate 的 ltm_recall 参数）保留，调用方真实填入 |
| `MemoryService.search` | 已实现：embed query → 余弦 top-k → access 记账 | 直接复用 |
| 三层记忆写路径 | 23（episodic）+ 24（semantic/procedural 提炼，含 importance 衰减） | recall 的原料齐备；episodic 不衰减，其老化由本任务 recency 加权处理（24 号文档 D4 的承诺） |
| 前端 `ws.ts` | 仅 `ltm_recall?: string[]` 类型声明（grep 全库无其他引用） | 协议收紧时可同步删除 |
| ws_plan 错误映射 | llm_transport / structured_output / llm_error / internal | 需新增 embedding_failed 映射 |
| 测试基建 | test_ws_plan.py mock `_resolve_dependencies`；服务测试真 DB + fake embedding + rollback | 沿用 |

## 设计决策

### D1 检索入口：新模块 `app/memory/recall.py`

与 `long_term.py`/`consolidator.py` 同域，提供 `LtmRecall` 类（构造入参 `db` + `MemoryService`）。调用方是 `ws_plan`（api 层调 service 层，符合架构红线；不把 DB 检索塞进无 DB 依赖的 `PlanEngine`）。`PlanEngine.generate(ltm_recall)` 的 M1 存根接口**不动**。

```
LtmRecall.recall(query) -> list[str]:
  1. hits = store.search(query, limit=20, kinds=("episodic","semantic","procedural"))
     （embed + 余弦 + access 记账，一次完成）
  2. 加权排序：final = cosine × importance × recency(age)
  3. 过滤：cosine < min_score 丢弃（防无关记忆污染 prompt）
  4. 截断 top_n，格式化为 list[str]；空 → []
```

### D2 加权公式（兑现 24 号 D4 承诺）

- `recency = 0.5 ** (age_days / half_life_days)`：30 天半衰期，age 取 `created_at`（记忆产生时刻；merge 不改 created_at，热度归 importance）。
- `final = cosine × importance × recency`：semantic/procedural 的热度由 24 号衰减维护（floor 0.1），episodic 的自然老化由 recency 承担——两者机制互不重复。
- 参数进 Settings（VM 验证可实测调参）：`ltm_recall_min_score=0.35`（余弦硬阈值）、`ltm_recall_top_n=5`（注入上限）、`ltm_recall_half_life_days=30`。候选上限 20 为模块常量（远超 top_n，初期记忆库足够）。

### D3 注入格式与 prompt 更新

每条格式：`[kind|cosine|importance] content`，让 planner 能区分"事实记录/跨会话知识/可复用方法"。`{ltm_recall}` 为空时渲染"无"（M1 行为不变）。`planner.md` 的历史记忆段落补充一句使用指引（引用决策时吸收、不相关时忽略）——改 prompt 不动接口。

### D4 WS 协议收紧：删 `GenerateMessage.ltm_recall`

字段是 M1 存根的一部分，但前端从未赋值（仅类型声明），填实后它成为死字段。删除后端字段 + 前端 `ws.ts` 的类型行，协议两端一致。老客户端仍兼容：`GenerateMessage` 未设 `extra=forbid`，多余字段被 Pydantic 忽略。

### D5 失败语义：上抛不降级

recall 的 embedding 失败（`EmbeddingTransportError`/`EmbeddingDimensionError`）上抛，ws_plan 新增映射 `EmbeddingError → embedding_failed`（与 complete 端点的 502 惯例一致）。DB 异常走既有兜底 internal。**不**做"失败就注入空"的静默降级——那会让记忆悄无声息失效，违背项目纪律。

### D6 日志与证据

`LtmRecall.recall` 打 `ltm_recall` 事件：query（截断 60 字符）、candidates、injected、scores。加上 `search()` 的 access 记账，验收标准 1 的"日志可证 LTM 命中"由日志事件 + DB 行 access_count 双重支撑。

### D7 成本

零新增 LLM 调用；每次 plan 生成 +1 次 embedding（init_request）。空库时同样 1 次 embedding（query 嵌入不可避免），可接受。

## 交付物清单

1. `backend/src/app/memory/recall.py`：`LtmRecall`（加权排序抽纯函数便于单测）
2. `backend/src/app/api/ws_plan.py`：删 `GenerateMessage.ltm_recall`；generate 前 recall；`EmbeddingError → embedding_failed`
3. `backend/src/app/config.py`：3 个 Settings 字段（min_score/top_n/half_life_days）
4. `backend/src/app/llm/prompts/planner.md`：历史记忆段落 + 使用指引
5. `frontend/src/api/ws.ts`：删 `ltm_recall` 类型行
6. 测试：`test_recall.py`（unit，全 mock：排序/阈值/格式化/空命中）、`test_recall_service.py`（真 DB + fake embedding：三层命中/记账/排除低分）、`test_ws_plan.py` 扩展（recall 注入成功路径 + embedding_failed）

## 风险与对策

| 风险 | 对策 |
|---|---|
| 阈值 0.35 是经验值，可能漏/误命中 | Settings 可调；VM 全链路验证实测相似度分布后微调 |
| 注入记忆污染 prompt（无关内容） | 余弦硬阈值 + top_n 截断 + prompt 指引"不相关忽略" |
| 旧前端客户端仍发 ltm_recall | Pydantic 默认忽略多余字段，兼容 |
| recall 失败使 plan 生成失败 | 上抛 502 用户可重试；后台无静默失效路径 |
| episodic 已消费行仍参与检索 | 有意为之（24 号 D2：episodic 不删除，保留原料可追溯），recency 加权保证新记忆优先 |

## 实施记录

### 交付内容（2026-09-03）

| 文件 | 变更 |
|---|---|
| `backend/src/app/memory/recall.py` | 新增：`LtmRecall` + `_rank`/`_format`/`_recency_factor` 纯函数 + `RankedHit`；`RECALL_KINDS` 三层、`CANDIDATE_LIMIT=20` |
| `backend/src/app/api/ws_plan.py` | 删 `GenerateMessage.ltm_recall`；新增 `_recall_ltm` 测试缝（与 `_resolve_dependencies` 同构）；generate 前 recall，`EmbeddingError → embedding_failed` |
| `backend/src/app/config.py` | 新增 `ltm_recall_min_score=0.35` / `ltm_recall_top_n=5` / `ltm_recall_half_life_days=30.0` |
| `backend/src/app/llm/prompts/planner.md` | 历史记忆段落补充格式说明与"相关引用、不相关忽略"使用指引 |
| `frontend/src/api/ws.ts` | 删 `ltm_recall` 类型行（协议两端一致） |
| 测试 | `test_recall.py`（R1-R6 全 mock 单测）、`test_recall_service.py`（S1-S5 真 DB + fake embedding）、`test_ws_plan.py` 扩展（T7 注入成功路径 + T8 embedding_failed） |

### 三绿

- pytest：369 passed / 4 skipped / 1 deselected（全量，`-m "not smoke"`）
- ruff：All checks passed
- mypy：55 源码文件零错

### 实施偏差与修正

1. **D2 公式实现**：`0.5 ** float_expr` 在 mypy strict 下被判 Any（typeshed `float.__pow__` overload 缺陷），改为 `math.pow(0.5, ...)`（语义等价，精确 float 类型）；`from math import pow` 会触发 ruff A004（遮蔽内置），故 `import math` + `math.pow`。
2. **WS 测试缝**：原设计 T4/T5/T6 需同时 patch MemoryService/embedding/LtmRecall 三处，改为模块级 `_recall_ltm(db, *, query)` 单一 patch 目标，与既有 `_resolve_dependencies` 模式一致。
3. **S5 测试设计**：两条同内容同 importance 行格式化后无法区分顺序，改用"新鲜 importance 0.9 vs 陈旧 60 天 importance 1.0"，以行内 importance 标签断言 recency 压过 importance。

### VM 真实全链路验证（192.168.1.147 + 真 Bailian text-embedding-v1）

一次性脚本 seed 三层记忆（flush 不 commit，rollback 零残留）后改述 query 召回：

| 用例 | 结果 | 实测余弦 |
|---|---|---|
| 改述 query（换框架）→ semantic 行 | 命中注入，排序第 1（final 0.4716） | **0.52** |
| 同 query 顺带命中 episodic 行 | 注入第 2（final 0.1771） | 0.35（恰在阈值边缘，通过） |
| 登录改述 query → episodic 行 | 命中注入（final 0.2913） | **0.58** |
| 无关 query（冒泡排序） | 注入 0 行，零污染 | < 0.35 全部滤除 |

- access 记账：semantic 行 `access_count` 0 → 1（`search()` 自带记账落库生效）。
- 日志证据：`ltm_recall` 事件 candidates=3 / injected=2/1/0 / scores 完整输出。
- **阈值经验值验证**：相关 0.52-0.58、边缘 0.35、无关 < 0.35 —— 默认 0.35 硬阈值与真实 embedding 分布吻合，无需调参（D 风险表第 1 行闭环）。

### 成本

每次 plan 生成 +1 次 embedding（init_request），零新增 LLM 调用；空库同 1 次 embedding。
