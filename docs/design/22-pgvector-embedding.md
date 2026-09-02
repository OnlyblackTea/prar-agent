# 22. pgvector 接入 + Embedding 服务（默认 OpenAI / 可换本地 BGE）

> 对应 ROADMAP M4 #22：`pgvector 接入 + Embedding 服务（默认 OpenAI / 可换本地 BGE）`，产出**向量写入/检索**。
> 本任务只做底座（Embedding 服务 + Memory 向量写入/检索 + 最小 API）；长期记忆三层（23）、Consolidator（24）、LTM_RECALL 注入（25）等后续任务在本底座之上构建。

## 目标

- 一句话目标：实现 `EmbeddingService`（OpenAI 兼容端点，零新增依赖）+ `MemoryService`（pgvector 向量写入/余弦检索），交付 `/api/memories` 最小 REST 面，使"任何文本 → 向量 → 写入 → 语义检索"整条链路可运行、可验证。
- 验收标准：
  1. 零新增 Python 依赖（复用既有 `openai>=1.55.0` SDK；本地 BGE 走 OpenAI 兼容 HTTP 端点 + `base_url` 覆盖，同一 SDK）；
  2. `EmbeddingService.embed` 返回与 schema `Vector(1536)` 匹配的向量，维度不匹配时抛 `EmbeddingDimensionError`（带清晰修复指引，绝不静默落库）；
  3. `MemoryService.store`（嵌入 + 写入）与 `MemoryService.search`（`<=>` 余弦距离、kind 过滤、score 降序、命中即 bump `access_count`/`last_accessed`）；
  4. API：`POST /api/memories`（写入）与 `POST /api/memories/search`（检索）；api 层不直连 LLM（经 core/services 层，架构红线）；
  5. Windows 开发机：全套测试全绿，`ruff check` / `mypy strict` / `make check-schema` 零警告零错误；单元测试 100% mock（不真调 API、不真连 DB）；
  6. 真实验证：VM DB（192.168.1.147）真实写入 + 余弦检索（含 kind 过滤、access 统计、排序正确性）；`pytest -m smoke` 真调阿里云百炼 embedding（text-embedding-v1，已验证可用，1536 维）。

## 现状调研结论（设计依据）

- **Schema 已预建**（alembic 0001）：`CREATE EXTENSION vector`、`memories.embedding Vector(1536)`、HNSW 索引 `memories_embedding_hnsw`（`vector_cosine_ops`）。实测 VM DB：pgvector **0.8.6**，表与索引俱在，`memories` 行数 0（此刻迁移成本最低）。
- **SDK 已就绪**：`openai>=1.55.0` 是既有依赖；`.env` 的 `OPENAI_API_KEY` + `OPENAI_BASE_URL` 指向阿里云百炼兼容端点。实测 `text-embedding-v1`：**可用，1536 维**（与 schema 完全匹配；v3/v4 为 1024 维，不可用）。
- **LLM 调用惯例**（`llm/router.py` + `llm/providers/`）：异常层级（Transport 系 + 业务系）、`ResolvedAdapter` 凭据解析、客户端缓存。Embedding 不走 adapter 体系（M4 记忆是系统级能力，不属用户会话 LLM），配置直达 `Settings`。
- **服务与 API 惯例**：`services/*_service.py` 持 `AsyncSession`（构造注入）；`api/*.py` 定义 pydantic 契约 + `Depends(get_db)`；API 测试用 `dependency_overrides` 注入 mock service（`test_comments_api.py` 范式）。
- **共享 schema 机制**：新响应模型须注册进 `app/shared/schemas.py` 的 `SHARED_SCHEMAS`，再 `make gen-schema` 重新生成 `shared/schema.json`（`make check-schema` 门禁）。
- **LTM 存根已预留**：`plan_engine.generate(..., ltm_recall)`、WS 客户端字段 `ltm_recall`、`prompts/planner.md` 的 `{ltm_recall}` 占位符——任务 25 的注入点已存在，本任务不动它。

## 设计决策

### D1. 零新增依赖：OpenAI 兼容端点一个 SDK 通吃

`AsyncOpenAI(**{api_key, base_url})` 覆盖三种部署：

| 场景 | `embedding_model` | `embedding_base_url` | `embedding_api_key` |
|---|---|---|---|
| 默认（本环境阿里云百炼） | `text-embedding-v1` | 不设 → SDK 读 `OPENAI_BASE_URL` 环境变量 | 不设 → SDK 读 `OPENAI_API_KEY` |
| OpenAI 官方 | `text-embedding-3-small` | 不设（默认 api.openai.com） | 同上 |
| 本地 BGE（TEI / Ollama 等 OpenAI 兼容服务） | 如 `bge-large-zh-v1.5` | `http://127.0.0.1:8080/v1` | 本地服务任意占位 key |

Embedding 不接入 `PROVIDER_REGISTRY`/adapter 体系——M4 记忆是系统级基础设施，不做 per-session LLM 路由。

### D2. 维度策略：固定 1536，运行时强校验，BGE 换维留迁移路径

- `memories.embedding` 为 `Vector(1536)` + HNSW（0001 已建，pgvector HNSW 要求全列同维，**不动 schema**）。
- `Settings.embedding_dim`（默认 1536）是运行时契约：每次 embed 校验返回维度，不匹配抛 `EmbeddingDimensionError`，错误信息含"改 `EMBEDDING_DIM` 配置 / 换 1536 维模型 / 执行换维迁移"三条指引。
- 本地 BGE（768/1024 维）换用需换维迁移（附录 A 给出 SQL 路径）。当前不做：数据量为 0 时迁移虽便宜，但"换维灵活性"在第二个真实实现出现前属于投机抽象（编码纪律：抽象等第二次实证）。ROADMAP "可换本地 BGE"的承诺落在**服务可换**（任意 OpenAI 兼容端点），维度契约显式化、失败大声化。

### D3. Embedding 服务位置与形态：`core/embedding.py`

- `core/` 层（与 `llm/router.py` 同级能力；api 禁直连 LLM，必经 core/services）。
- 类 `EmbeddingService`：懒建 `AsyncOpenAI` 客户端（单实例复用连接池），模块级 `@lru_cache get_embedding_service()` 单例（跟随 `config.py` 模式）。
- 异常层级：`EmbeddingError` 基类 → `EmbeddingTransportError`（SDK 异常/认证/限流包装，含 `model_id` 与 `cause`）→ `EmbeddingDimensionError`（维度违约）。
- 批量接口 `embed(texts) -> list[list[float]]`，空列表/空串校验在服务层（`ValueError`）。

### D4. 检索语义：余弦距离 + score 换算 + access 统计

```sql
-- 等价 SQL（HNSW 命中）
SELECT *, 1 - (embedding <=> :query_vec) AS score
FROM memories
WHERE embedding IS NOT NULL [AND kind = ANY(:kinds)]
ORDER BY embedding <=> :query_vec
LIMIT :limit
```

- `score = 1 - cosine_distance` ∈ [-1, 1]（1 = 完全相同方向）；`ORDER BY` 按距离升序即 score 降序。
- 检索命中后对命中 id 批量 `UPDATE access_count = access_count + 1, last_accessed = now()`——M4 记忆衰减/重要性演进的原始信号，本任务只负责记账。
- `embedding IS NULL` 的行（历史脏数据/未来手工行）被自动排除，不参与排序。
- `limit` 上限 50（API 层 `ge=1, le=50`），默认 5。

### D5. MemoryService 写入：嵌入失败不落库

`store()` 顺序：`embed_one(content)` 成功 → 构造 `Memory` 行（含 embedding）→ `flush/refresh`。嵌入调用失败（Transport/Dimension）时**没有任何部分写入**——embedding 缺失的行对检索无用，宁失败不产生半成品。

### D6. API 面（最小交付）

`api/memories.py`，`APIRouter(prefix="/api/memories")`：

```
POST /api/memories
  body: { kind: "episodic"|"semantic"|"procedural", content: str(1..), importance: 0..1 = 0.5,
          user_id?: UUID, source_session?: UUID }
  → 201 MemoryResponse（不含 embedding 字段——向量是内部数据，永不出 API）

POST /api/memories/search
  body: { query: str(1..), limit: 1..50 = 5, kinds?: [kind] }
  → 200 MemorySearchResponse { hits: [{ id, kind, content, importance, score,
         last_accessed, access_count, source_session, created_at }] }
```

- kind 契约 API 层 `Literal["episodic","semantic","procedural"]`；服务层对直调方再校验一次（`ValueError`）。
- 无 GET/list——任务 23/27 按需再扩，不过度建设。

### D7. 配置新增（`config.py` Settings + `.env` 注释）

```python
embedding_model: str = "text-embedding-v1"   # 实测可用、1536 维、最便宜
embedding_dim: int = 1536                     # 运行时契约，与 Vector(1536) 对齐
embedding_base_url: str | None = None         # None → SDK 环境变量默认（OPENAI_BASE_URL）
embedding_api_key: str | None = None          # None → SDK 环境变量默认（OPENAI_API_KEY）
```

## 交付物清单

| 文件 | 动作 |
|---|---|
| `backend/src/app/core/embedding.py` | 新增：`EmbeddingService` + 异常层级 + `get_embedding_service()` |
| `backend/src/app/services/memory_service.py` | 新增：`MemoryService`（store/search）+ `MemoryHit` dataclass |
| `backend/src/app/api/memories.py` | 新增：pydantic 契约 + 两端点 + `get_memory_service` 依赖 |
| `backend/src/app/config.py` | 修改：`embedding_*` 四个字段 |
| `backend/src/app/main.py` | 修改：注册 memories router |
| `backend/src/app/shared/schemas.py` | 修改：注册 4 个新模型 |
| `shared/schema.json` | 重新生成（gen-schema） |
| `backend/.env` | 修改：追加 EMBEDDING_* 注释段（值保持默认，不写敏感新增） |
| `backend/tests/test_embedding.py` | 新增：单元测试（mock AsyncOpenAI） |
| `backend/tests/test_memory_service.py` | 新增：单元测试（AsyncMock db + mock embedding） |
| `backend/tests/test_memories_api.py` | 新增：API 测试（dependency_overrides） |
| `backend/tests/test_embedding_smoke.py` | 新增：`@pytest.mark.smoke` 真 API 冒烟 |

## 测试策略（成本纪律）

- **单元测试 100% mock**：`test_embedding.py` 用 `AsyncMock` 替换 `EmbeddingService._client` 的 `embeddings.create`；`test_memory_service.py` 用 `AsyncMock` db（`test_comment_service.py` 范式）+ 注入 mock embedding；`test_memories_api.py` 用 `dependency_overrides`（`test_comments_api.py` 范式）。零真实网络/DB。
- **smoke（默认跳过）**：`test_embedding_smoke.py` 单用例真调 `text-embedding-v1`，断言 1536 维。成本：1 次 embedding 调用（文本个位数 token，几乎为零）。
- **真实验证（任务步骤，非测试）**：对 VM DB 跑 写入→检索 全链路脚本，断言余弦排序、kind 过滤、access 统计，验证后清理测试行。

## 风险与对策

| 风险 | 对策 |
|---|---|
| 维度不匹配静默落库 → HNSW 插入炸 | D2 运行时强校验，`EmbeddingDimensionError` 前置失败 |
| embedding 端点欠费/挂 → 写入链路全挂 | `EmbeddingTransportError` 包装 + 结构化日志（`get_logger("embedding")`），api 层 502 |
| 本地 BGE 换维需求 | 附录 A 迁移路径已文档化；服务层已支持 base_url 切换 |
| `shared/schema.json` 失同步 | `make check-schema` 门禁（CI/验收必跑） |

## 附录 A：换维迁移路径（切本地 BGE 时执行，本任务不执行）

```sql
-- 例：1536 → 1024（bge-large-zh-v1.5）。pgvector 换维需全表重写 + 索引重建。
ALTER TABLE memories ALTER COLUMN embedding TYPE vector(1024) USING embedding::vector(1024);
DROP INDEX memories_embedding_hnsw;
CREATE INDEX memories_embedding_hnsw ON memories
  USING hnsw (embedding vector_cosine_ops);
-- 同步修改 Settings.embedding_dim；旧 1536 数据需重新嵌入（任务 23 的 Consolidator 场景）。
```

## 实施记录

### 2026-09-03 实施完成

- **TDD 红**：3 个测试文件（test_embedding / test_memory_service / test_memories_api）先写后绿，收集期报 3 个 ModuleNotFoundError 确认红灯。
- **实现**：`core/embedding.py`（EmbeddingService + Transport/Dimension 异常层级 + `lru_cache` 单例）、`services/memory_service.py`（store 嵌入成功后整行落库 / search 余弦排序 + access 记账）、`api/memories.py`（POST "" + POST "/search"，embedding 永不出 API）、config 4 字段、main 注册、shared 注册 4 模型。
- **三绿**：pytest 317 passed / 4 skipped / 1 deselected（smoke）；ruff 全绿；mypy 81 文件零错误。
- **类型设计变更**：`MemoryService.search` 的 `kinds` 参数定为 `Sequence[str]`（原计划 `list[str]`）——API 层 `list[Literal[...]]` 因 list 不变性无法赋给 `list[str]`，改用协变 Sequence 后零 cast 通过。
- **mock 陷阱**：`AsyncMock` 会把同步的 `Session.add` 误生成协程导致 RuntimeWarning——测试里 `db.add = MagicMock()`，与 test_comment_service.py 既有范式一致（该文件本身仍有 1 个同类 warning，非本次引入）。
- **真实验证**：
  - smoke：`pytest -m smoke` 真调阿里云 Bailian `text-embedding-v1`，1536 维与 `embedding_dim` 一致（0.8s）。
  - VM DB（192.168.1.147）临时脚本全链路：写入 3 行（2 episodic + 1 procedural）→ 余弦检索语义排序正确（0.6846 > 0.6376 > 0.2093，无关内容垫底）→ kind 过滤（episodic → 2 条）→ access 记账（命中 2 次 count=2 / 1 次 count=1）→ 清理后行数 0 → 0。脚本按"任务步骤非交付物"约定跑完即删。
- **schema 门禁**：gen_schema 重新生成 `shared/schema.json`（22 schemas，+209 行），重复运行幂等无漂移。
- **零新增依赖**：复用 openai SDK（>=1.55.0），`AsyncOpenAI(api_key/base_url)` 显式覆盖，None 时回退 SDK 环境变量默认。
