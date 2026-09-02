# 24. Consolidator 后台任务（衰减/合并/提炼 semantic）

> 对应 ROADMAP M4 #24：`Consolidator 后台任务（APScheduler + 衰减/合并/提炼）`，验收：**定时跑通，semantic 从多个 episodic 提炼**。
> 上游任务 23 已交付：episodic 确定性模板写入（`memory/long_term.py`）、`MemoryService.store/search`、`AdapterService.get_default()`、`LLMRouter.complete_structured`。本任务在其上构建，不回溯改 23 的模板格式。

## 目标

- 一句话目标：实现 `memory/consolidator.py` 后台任务——每轮把未提炼的 episodic 批量喂给默认 adapter 提炼为 semantic/procedural 记忆（一次 LLM 调用），写入前去重合并，并对已有知识层做 importance 衰减；由 FastAPI lifespan 定时驱动 + 手动触发端点兜底。
- 验收标准：
  1. 定时循环跑通：后端启动后按 `CONSOLIDATOR_INTERVAL_SECONDS` 周期自动执行（零新增依赖）。
  2. 多个 episodic 行 → 一次提炼 → semantic 行落库；episodic 行打上已消费标记（幂等，不重复 LLM）。
  3. 衰减：semantic/procedural 的 importance 每轮按 factor 衰减、有下限。
  4. 合并：新提炼结果与已有同 kind 行余弦相似度 ≥ 阈值时不插新行、更新旧行。
  5. 手动触发 `POST /api/memories/consolidate` 返回本轮计数（processed/distilled/inserted/merged/decayed）。
  6. 失败语义：LLM/embedding 失败 → 本轮不标记、零部分写入，下轮可重试；无待处理行 → 零 LLM 零 embedding。

## 现状调研（2026-09-03）

| 依赖项 | 现状 | 结论 |
|---|---|---|
| `Memory` 表（M3-22） | 有 `kind/content/embedding/importance/access_count/last_accessed/source_session/created_at`，**无消费标记列** | 需 alembic 迁移加 `consolidated_at` |
| `MemoryService` | 仅 `store/search`（search 内部 embed query，再查余弦） | 需加 `search_vector`（避免重复 embed）、`merge_into`、`list_unconsolidated`、`mark_consolidated`、`decay_importance` |
| `AdapterService.get_default()` | 已存在（`is_default` 唯一索引） | Consolidator 用默认 adapter |
| `LLMRouter.complete_structured` | 已存在（Instructor 结构化输出） | 提炼用结构化 schema |
| prompt 加载 | `plan_engine._load_prompt` 模块级私有函数 | 提升为 `llm/prompts/loader.py: load_prompt()`（第二个消费者出现） |
| `main.py` | 无 lifespan | 加 lifespan 启动后台循环 |
| 后台调度 | ROADMAP 写 APScheduler，pyproject 无此依赖 | **零新增依赖铁律 → asyncio 循环**（偏离 ROADMAP 的 APScheduler 字样，理由见设计决策） |
| 测试基建 | `conftest.py` 的 `TestClient(app)` fixture 不 `with`，lifespan 不执行 | 后台任务不干扰 API 测试；service 测试沿用 M4-23 真 DB + rollback 模式 |

## 设计决策

### D1 调度机制：asyncio 循环替代 APScheduler

ROADMAP 原文写 "APScheduler"，但项目铁律是**零新增依赖**。`asyncio.create_task` + `asyncio.sleep(interval)` 完全覆盖需求（单实例、单进程 uvicorn）。间隔由 `Settings.consolidator_interval_seconds`（默认 3600）配置，VM 验证时可改小观察定时跑通。另加手动触发端点 `POST /api/memories/consolidate`，与循环共用同一 `run_once()`——端点提供确定性验证手段，循环提供"定时跑通"验收。

### D2 消费标记：memories 加 `consolidated_at` 列（迁移 0003）

没有标记列，每轮要么重复提炼同一批 episodic（违背成本纪律），要么全表重炼。`consolidated_at TIMESTAMPTZ NULL`：NULL = 未消费。空提炼结果（LLM 判定"无可提炼知识"）**同样消费**——这是合法结论，防止每轮为同一批原料重复烧 LLM。episodic 行不删除：保留原料可追溯（source_session 链接），任务 25 recall 仍可用。

### D3 每轮流程（单事务，行锁防并发）

两个触发源（后台循环 + 手动端点）可能并发，批次选取用 `SELECT ... FOR UPDATE` 行锁：

```
run_once(db, store, router):
  1. batch = store.list_unconsolidated(limit=20, for_update=True)
  2. 若空：直接走 6（衰减照常），返回全 0 计数 —— 零 LLM 零 embedding
  3. adapter = AdapterService(db).get_default()；无 → raise NoDefaultAdapterError（不标记不写）
  4. 一次 LLM：router.complete_structured(schema=ConsolidatedMemories, prompt=consolidator.md)
     - LLM 失败 → 异常上抛：不标记、零部分写入、下轮重试
  5. 每条 DistilledMemory：
     a. vec = embedding.embed_one(content)
     b. hit = store.search_vector(vec, kinds=[kind], limit=1)
     c. hit.score ≥ merge_threshold → store.merge_into(hit.id, content, vec, importance=max)
        （新提炼内容覆盖旧内容：提炼结果是更综合的知识；importance 取大）
        else → store.store(kind, content, embedding=vec, importance)
     - embedding 失败 → 异常上抛：整个事务回滚（不标记）
  6. store.mark_consolidated(batch ids)（步骤 4 成功且 5 成功才执行）
  7. store.decay_importance(kinds=["semantic","procedural"], factor=0.9, floor=0.1)
  8. 返回计数 {processed, distilled, inserted, merged, decayed}
```

事务边界：`run_once` 只 flush 不 commit；调用方 commit（API 走 `get_db`，循环走 sessionmaker + 显式 rollback on error）。LLM/embedding 异常由调用方回滚，batch 保持未消费。

### D4 衰减范围：只衰减 semantic/procedural

episodic 是一次性原料（事实记录，非知识）；其老化由任务 25 recall 的 importance × recency 排序处理，再对 episodic 衰减是重复机制。`importance = GREATEST(floor, importance * factor)`，floor=0.1，factor=0.9。

### D5 提炼 schema 与成本纪律

- LLM 输出 schema（定义在 `memory/consolidator.py`，与 `core/plan_schemas.py` 同风格）：
  `DistilledMemory{kind: "semantic"|"procedural", content: str≥1, importance: float∈[0,1] 默认 0.5}`，容器 `ConsolidatedMemories{items: list[...]}`。
  一次调用同时产 semantic（跨会话知识/偏好）与 procedural（可复用方法/流程）——满足任务 23 契约表"semantic/procedural 写路径留给任务 24"，成本不变。
- 成本：每轮 ≤1 次 LLM 调用（仅当有未消费 batch）；每条提炼结果 1 次 embedding（合并路径用 `search_vector` 不重复嵌入）；无 batch 零 LLM 零 embedding。

### D6 MemoryService 扩展（5 个方法）

| 方法 | 语义 | 理由 |
|---|---|---|
| `search_vector(*, vec, limit, kinds)` | 现有 `search` 抽出向量检索部分；`search` 改为 embed + 委托 | 合并检查避免对同一文本二次 embed |
| `merge_into(*, memory_id, content, embedding, importance)` | 更新旧行 content/embedding/importance | 合并去重 |
| `list_unconsolidated(*, limit, for_update)` | `kind='episodic' AND consolidated_at IS NULL ORDER BY created_at LIMIT n [FOR UPDATE]` | 取原料 |
| `mark_consolidated(*, ids)` | `UPDATE ... SET consolidated_at = now()` | 幂等消费标记 |
| `decay_importance(*, kinds, factor, floor)` | `UPDATE ... SET importance = GREATEST(:floor, importance * :factor)` | 衰减 |

### D7 后台循环（lifespan + 容错）

`main.py` 加 lifespan：启动即跑一轮（消化停机积压），随后 `sleep(interval)` 循环；shutdown 时 cancel。循环体 `run_cycle`：每轮异常 `log.exception` 后继续（后台任务不因单轮失败退出，日志不静默）。`consolidator_loop(interval, run_cycle)` 抽为纯循环函数供单测（注入 fake `run_cycle` + monkeypatch `asyncio.sleep`）。

### D8 API：POST /api/memories/consolidate

- 无请求体；响应 `ConsolidateResponse{processed, distilled, inserted, merged, decayed}`（内联定义，与 memories.py 其他模型一致——M3-22 未把 memories 模型纳入 shared/schema.json，本任务同样不纳入）。
- 异常映射：`NoDefaultAdapterError → 503 no_default_adapter`；`LLMError → 502 llm_failed`；`EmbeddingError → 502 embedding_failed`（与 complete 端点的 502 惯例一致）。

### D9 prompt 文件 `llm/prompts/consolidator.md`

`str.format(**ctx)` 语法（与 planner.md 一致），ctx 仅 `{episodic_block}`（batch 内容按序号拼接）。系统提示要求：只提炼跨会话有效、明确的知识/方法；无把握宁缺毋滥（输出空列表合法）。

## 交付物清单

1. `backend/alembic/versions/0003_memory_consolidated_at.py` + `models.py` 加 `Memory.consolidated_at`
2. `app/llm/prompts/loader.py`（`load_prompt` 自 plan_engine 提升；plan_engine 改 import）+ `llm/prompts/consolidator.md`
3. `app/services/memory_service.py`：+5 方法（D6）
4. `app/memory/consolidator.py`：schema + `Consolidator.run_once` + `consolidator_loop`
5. `app/api/memories.py`：`POST /api/memories/consolidate`
6. `app/main.py`：lifespan；`app/config.py`：`consolidator_interval_seconds`
7. 测试：`test_consolidator.py`（unit，全 mock）、`test_consolidator_service.py`（真 DB + fake LLM）、`test_memories_consolidate_api.py`（API 层）

## 风险与对策

| 风险 | 对策 |
|---|---|
| episodic 模板质量决定提炼效果 | 23 的模板含决策答案 + 步骤结果，语义密度足够；任务 25 可调 recall 检索参数，不回溯改模板 |
| 无默认 adapter（配置缺失） | 明确异常 503；不标记不写；日志 warning；后台循环下轮重试 |
| LLM/embedding 挂 → 数据积压 | 不标记 → 下轮重试；无静默 catch；API 502 |
| 循环与手动端点并发 | 批次 `FOR UPDATE` 行锁；单实例假设（uvicorn 单进程）已满足 |
| 合并覆盖旧内容丢信息 | 提炼结果语义上是"更新更综合的知识"；importance 取 max 保留热度；阈值 0.9 保守 |
| alembic 迁移执行（中文 Windows GBK 坑） | 用已知 monkeypatch 脚本模式跑迁移（项目记忆 windows-alembic-utf-8-ini-gbk） |
| TestClient fixture 触发 lifespan | conftest 的 `TestClient(app)` 不 `with`，lifespan 不执行，测试不受后台任务干扰 |

## 实施记录（2026-09-03）

- **交付**：7 项交付物全部落地。`memory/consolidator.py`（ConsolidatedMemories/DistilledMemory schema + `Consolidator.run_once` 仅 flush + `consolidator_loop` 先睡后跑）、`llm/prompts/loader.py`（`load_prompt` 自 plan_engine 提升，plan_engine/review_merger 改 import）、`llm/prompts/consolidator.md`、`MemoryService` +5 方法（search_vector/merge_into/list_unconsolidated/mark_consolidated/decay_importance）、`POST /api/memories/consolidate`（503/502×2 映射）、`main.py` lifespan（启动先跑一轮消化积压 + 循环 + shutdown cancel）、迁移 0003 `consolidated_at`（已应用 VM DB，0002→0003）。
- **三绿**：pytest 356 passed / 4 skipped / 1 deselected（26.17s，服务测试全真 VM DB）；ruff "All checks passed!"；mypy 54 源码文件零错。
- **VM DB 全链路验证**（192.168.1.147，真实 qwen3-max + 真实 Bailian embedding，一次性脚本跑完删除）：seed 3 条真实 episodic → `_run_consolidator_cycle()` 一轮 → 1 次 LLM（889 in / 198 out tokens）→ 3 条 procedural 提炼落库；`decayed=3 distilled=3 inserted=3 merged=0 processed=3`；3 条 episodic 全部打上 consolidated_at；提炼行 importance=0.81（0.9 提炼值 × 0.9 衰减因子，与 C4 期望一致）；验证后全表清理归零。
- **验收对照**：①定时循环——循环体 `_run_consolidator_cycle` 真机跑通（含 commit），sleep 定时由 unit 测试注入 fake 覆盖；②多 episodic→一次提炼→落库+消费标记（llm_call 日志仅 1 次佐证）；③衰减 factor/floor（importance 0.81 实测）；④合并阈值路径（真 pgvector 余弦，C4）；⑤手动端点计数响应（API 测试）；⑥失败语义（C5 无 adapter 不标记、C6 LLM 失败批次保持未消费）。
- **实施中修复**：mypy 报 `Result[Any]` 无 `.rowcount` → `cast(CursorResult[Any], ...)`（沿用项目 cast 收窄约定）；ruff F401/I001（导入排序与未用导入）随修。
- **插曲**：验证中途 VM 的 pgvector 容器宕机（5432 不可达，25 个真 DB 测试 OSError），按约定提醒主人修复后重跑，三绿与全链路验证均基于修复后的 VM DB。
