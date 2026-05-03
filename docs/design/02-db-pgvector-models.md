# 02. PostgreSQL + pgvector 容器化 + 初版 ORM 模型

## 目标

- **一句话**：把 4 张核心表（sessions / plans / comments / memories）的 schema 落到 PostgreSQL + pgvector，alembic 第一份 migration 可一键 upgrade 到 head。
- **验收标准**（缺一不可）：
  1. `docker compose up -d postgres` 起容器，`psql` 可连
  2. `cd backend && make db-init` 跑 alembic upgrade head 成功，4 张表 + pgvector HNSW 索引落盘
  3. `cd backend && make test` 全绿（含本任务新增的模型 introspection 测试）
  4. `cd backend && make lint && make typecheck` 仍零警告/零错误
  5. `psql ... -c "\d+ sessions plans comments memories"` 输出符合契约

## 输入 / 输出

**前置任务**：
- Task 01（后端骨架）— ✅ 已完成

**交付物清单**：
- 项目根 `docker-compose.yml`（启 postgres+pgvector 服务）
- backend 端 `db/` 子包：`base.py` (declarative Base) + `models.py`（4 个 ORM）
- alembic 配置 + 第一份 auto-generated migration
- `pyproject.toml` 增 4 个依赖（sqlalchemy / asyncpg / alembic / pgvector）
- `Settings` 增 `database_url` 字段；`.env.example` 增 `DATABASE_URL`
- `Makefile` 增 5 个 db-* target
- 模型 introspection 测试

**不交付**（留给后续 task）：
- 业务路由消费 DB（→ 路由层在 Task 03 状态机集成时引入）
- `get_db` FastAPI dependency / async session factory（→ 同上，Task 03 起需要）
- 真实 CRUD 操作 → 各业务 task 自己加
- 测试容器化（testcontainers-python）→ 暂不做，整合测试用主开发 DB
- LTM 写入逻辑 → Task 22-25

## 接口设计

### 目录结构（增量）

```
prar-agent/
├── docker-compose.yml            # 新增（项目根）
└── backend/
    ├── alembic.ini               # 新增
    ├── alembic/
    │   ├── env.py                # 新增（async 模式）
    │   ├── script.py.mako        # 新增（默认模板）
    │   └── versions/
    │       └── <rev>_initial.py  # 新增（auto-gen 后入库）
    ├── pyproject.toml            # 修改（+4 依赖）
    ├── .env.example              # 修改（+ DATABASE_URL）
    ├── Makefile                  # 修改（+5 target）
    ├── src/app/
    │   ├── config.py             # 修改（+ database_url 字段）
    │   └── db/
    │       ├── __init__.py       # 新增（空）
    │       ├── base.py           # 新增（Base + naming convention）
    │       └── models.py         # 新增（4 个 ORM 类）
    └── tests/
        └── test_models.py        # 新增（introspection 测试）
```

### `docker-compose.yml`（项目根）

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    container_name: prar-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: prar
      POSTGRES_PASSWORD: prar
      POSTGRES_DB: prar_agent
    ports:
      - "15432:5432"          # 避开本机已有 postgres
    volumes:
      - prar_pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "prar", "-d", "prar_agent"]
      interval: 5s
      timeout: 3s
      retries: 5

volumes:
  prar_pgdata:
```

> 端口 15432 故意避开主机默认 5432。`pgvector/pgvector:pg16` 镜像已预装 pgvector 扩展，但需要在每个 DB 内 `CREATE EXTENSION` 启用——交给 alembic 第一份 migration 做。

### `Settings` 增量（`src/app/config.py`）

新增字段：

```python
database_url: str = "postgresql+asyncpg://prar:prar@localhost:15432/prar_agent"
```

通过 `.env` 的 `DATABASE_URL` 覆盖（pydantic-settings 自动 case-insensitive 映射）。

### `.env.example` 增量

```
# Database
DATABASE_URL=postgresql+asyncpg://prar:prar@localhost:15432/prar_agent
```

### Makefile 增量

| target | 命令 | 用途 |
|--------|------|------|
| `make db-up` | `docker compose -f ../docker-compose.yml up -d postgres` | 起容器 |
| `make db-down` | `docker compose -f ../docker-compose.yml down` | 停容器（数据卷保留） |
| `make db-shell` | `docker exec -it prar-postgres psql -U prar -d prar_agent` | 进 psql |
| `make db-init` | `uv run alembic upgrade head` | 应用所有 migration 到最新 |
| `make db-makemigration` | `uv run alembic revision --autogenerate -m "$(MSG)"` | 自动生成 migration（用法 `make db-makemigration MSG=desc`） |

### `db/base.py`

```python
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# 命名约定：让 alembic 自动生成可重命名的约束/索引名
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
```

> 命名约定是 alembic 与 SQLAlchemy 集成的最佳实践——避免 auto-gen 出 `ix_xxxx_<random>` 这种不可控名字，未来 rename 列时 migration 不爆炸。

### `db/models.py` — 4 个 ORM 类

#### Session（PRAR 工作流实例，**非** SQLAlchemy session）

```python
class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    init_request: Mapped[str] = mapped_column(Text, nullable=False)
    phase: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        # CHECK constraint enforced via __table_args__
    )
    current_plan_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    model_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "phase IN ('init','planning','plan_review','acting','action_review','done')",
            name="phase_valid",
        ),
    )
```

> 字段 `metadata_json` 而非 `metadata` 因为 SQLAlchemy declarative 的 `metadata` 是保留属性。
>
> Phase 用 `String(32)` + `CheckConstraint` 而不是 PostgreSQL ENUM，因为 ENUM 增删值要 ALTER TYPE 复杂；CHECK 后续改 enum 集合直接发新 migration `op.drop_constraint`+`op.create_check_constraint`。

#### Plan（每 session 多版本，ProseMirror tree 存 JSONB）

```python
class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    document: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("session_id", "version", name="session_version_unique"),
    )
```

#### Comment

```python
class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    anchor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    quote_context: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

> `anchor_id` 是 ProseMirror 树里的字符串 ID（如 `anc_xxx`），不是 FK——锚点本身嵌在 plan.document JSONB 里。

#### Memory（pgvector 长期记忆）

```python
class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[UUID | None] = mapped_column(nullable=True)  # 单用户期暂 NULL
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    importance: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.5")
    last_accessed: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    access_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    source_session: Mapped[UUID | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('episodic','semantic','procedural')", name="kind_valid"
        ),
        Index(
            "memories_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
```

> `embedding` 设 nullable：Task 22 才接入 embedding 服务，Task 23+ 写入 episodic 时可能临时只存 `content`。
>
> HNSW 索引在 nullable 列上工作正常（pgvector 自动跳过 NULL）。

### Alembic 配置

#### `alembic.ini`（关键差异于默认）

```ini
[alembic]
script_location = alembic
sqlalchemy.url = postgresql+asyncpg://prar:prar@localhost:15432/prar_agent
file_template = %%(rev)s_%%(slug)s
```

`sqlalchemy.url` 在 `env.py` 内会被 `Settings.database_url` 覆盖（避免硬编码）。

#### `alembic/env.py`（async 模式 + pgvector 类型识别）

关键片段：

```python
import asyncio
from logging.config import fileConfig

import pgvector.sqlalchemy  # noqa: F401  注册 Vector 类型给 alembic 看见
from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import get_settings
from app.db.base import Base
from app.db import models  # noqa: F401  触发模型注册到 Base.metadata

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)
if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations():
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as conn:
        await conn.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    raise RuntimeError("Offline mode unsupported in async setup")
else:
    asyncio.run(run_async_migrations())
```

#### 第一份 migration

由 `alembic revision --autogenerate -m "initial schema"` 生成。**手工补**第一行：

```python
op.execute("CREATE EXTENSION IF NOT EXISTS vector")
```

放到 `def upgrade()` 的最前面，确保 pgvector 扩展在 `CREATE TABLE memories` 前就绪。

`def downgrade()` 不需要 DROP EXTENSION（其他 schema 可能在用）。

### `pyproject.toml` 依赖增量

```toml
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "pydantic>=2.9.0",
    "pydantic-settings>=2.5.0",
    # === Task 02 新增 ===
    "sqlalchemy[asyncio]>=2.0.30",
    "asyncpg>=0.29.0",
    "alembic>=1.13.0",
    "pgvector>=0.3.0",
]
```

dev 组无需新增（pytest/httpx/mypy 已覆盖）。

## 文件清单

| 路径 | 类型 | 说明 |
|------|------|------|
| `docker-compose.yml` (项目根) | 新增 | postgres+pgvector 服务，端口 15432，named volume |
| `backend/alembic.ini` | 新增 | alembic 配置（url 由 env.py override） |
| `backend/alembic/env.py` | 新增 | async 模式 + pgvector 类型注册 |
| `backend/alembic/script.py.mako` | 新增 | 默认模板（alembic init 生成） |
| `backend/alembic/versions/<rev>_initial_schema.py` | 新增 | auto-gen 后手工补 CREATE EXTENSION |
| `backend/src/app/db/__init__.py` | 新增 | 空 |
| `backend/src/app/db/base.py` | 新增 | Base + 命名约定 |
| `backend/src/app/db/models.py` | 新增 | 4 个 ORM 类 |
| `backend/tests/test_models.py` | 新增 | introspection 测试（不连真 DB） |
| `backend/pyproject.toml` | 修改 | +4 deps |
| `backend/.env.example` | 修改 | +DATABASE_URL |
| `backend/Makefile` | 修改 | +5 个 db-* target |
| `backend/src/app/config.py` | 修改 | +database_url 字段 |
| `backend/README.md` | 修改 | +DB setup 一段（make db-up + make db-init） |

总计 9 新增 + 5 修改 = 14 文件改动。

## 实施步骤

1. **建目录**：`mkdir -p backend/src/app/db backend/alembic/versions`
2. **写 `docker-compose.yml`**（项目根）
3. **`docker compose up -d postgres` + 等 healthcheck 通过**：人工验证 `docker exec prar-postgres psql -U prar -c "SELECT version()"` 工作
4. **`pyproject.toml` 加 4 deps + `uv sync`**：验证依赖装得上
5. **写 `.env.example` 增量 + `config.py` 加 `database_url` 字段**
6. **写 `db/base.py`** 与 `db/__init__.py`
7. **写 `db/models.py` 4 个类**
8. **`uv run alembic init alembic`** 初始化 alembic 目录（生成 env.py / script.py.mako）
9. **重写 `alembic/env.py`** 为 async 模式 + 注入 Settings.database_url
10. **写 `alembic.ini`**（如果第 8 步没生成则手写）
11. **`uv run alembic revision --autogenerate -m "initial schema"`**
12. **手工补 migration**：在 `upgrade()` 顶部加 `op.execute("CREATE EXTENSION IF NOT EXISTS vector")`；检查 `Vector(1536)` 列、HNSW 索引、CheckConstraint、UniqueConstraint 都生成了
13. **`make db-init` 跑 migration**：验证 `psql -U prar -d prar_agent -c "\dt"` 看到 4 张表 + alembic_version
14. **写 `tests/test_models.py`**
15. **`make test` 验证全绿**（旧 3 个 + 新增）
16. **`make lint && make typecheck` 验证零警告**
17. **`make Makefile` 加 5 个 target**（db-up/down/shell/init/makemigration）
18. **更新 `backend/README.md`** DB setup 说明

## 测试清单

### 单元测试（无需真 DB，必须 cover）

| # | 测试 | 断言 |
|---|------|------|
| T1 | `test_models_register_with_metadata` | `Base.metadata.tables` 含 `sessions/plans/comments/memories` 四张表 |
| T2 | `test_session_columns` | `Session.__table__.columns` 含 `id/init_request/phase/.../updated_at` 11 列，phase 列有 CheckConstraint |
| T3 | `test_plan_columns_and_unique` | `Plan.__table__` 含 `(session_id, version)` 唯一约束 |
| T4 | `test_comment_columns` | `Comment.__table__.columns` 含 anchor_id/quote/quote_context/body/resolved |
| T5 | `test_memory_columns_and_index` | `Memory.__table__` 含 Vector(1536) 类型的 embedding 列 + HNSW 索引、kind CheckConstraint |

### 集成测试（需要真 DB，跳过 if 没有连接）

| # | 测试 | 断言 |
|---|------|------|
| I1 | `test_alembic_upgrade_head` | (skip if `pg_isready` false) `alembic upgrade head` 退出码 0；连 DB 验证 4 张表存在 |

I1 用 pytest `@pytest.mark.skipif` 标记跳过条件——本任务暂不强制 CI 跑，主人本地验证即可。

### 边缘情况

- 重复 `alembic upgrade head`：应幂等无副作用 → alembic 自带版本表机制保证
- pgvector 扩展未启用：第一份 migration 顶部 `CREATE EXTENSION IF NOT EXISTS vector` 兜底
- `metadata_json` vs `metadata`：列名 SQL 侧仍是 `metadata`（要不要 rename 列？）→ **决策**：列名 = 类属性名 = `metadata_json`，避免任何 SQLAlchemy 保留字误踩

### 集成测试入口

```bash
cd backend && make db-up && make db-init && make test
```

## 风险与未决

### 已识别风险

| 风险 | 缓解 |
|------|------|
| 主机 5432 已被本机 postgres 占用 | 默认映射 15432，避开 |
| pgvector 镜像版本与代码 driver 兼容 | `pgvector/pgvector:pg16` + Python `pgvector>=0.3.0` 对齐 PG16/asyncpg |
| asyncpg + alembic async env.py 配置坑 | env.py 用社区成熟模板（async_engine_from_config + run_sync） |
| auto-gen migration 漏掉 HNSW 索引 / 自定义类型 | 实施步骤 #12 强制人工 review migration 文件，缺什么补什么 |
| `Vector(1536)` 维度未来要换 | nullable + Index 在 nullable 上 OK；改维度需新 migration drop+create 列，文档化 |
| alembic_version 表名冲突 | alembic 默认 `alembic_version`，不与 4 张业务表冲突 |
| `gen_random_uuid()` 在 PG13- 不可用 | PG16 自带，无问题（pgcrypto 也已默认包含） |

### 已决策（默认值，主人不反对就这么走）

| # | 项目 | 决策 | 反对就告诉我 |
|---|------|------|-------------|
| Q1 | DB 端口 | **15432**（避主机冲突） | "用 5432" / 别的 |
| Q2 | 数据卷类型 | **named volume** `prar_pgdata` | "用 bind mount ./pgdata" |
| Q3 | Embedding 维度 | **1536**（OpenAI text-embedding-3-small） | "用 768" / "用 384" / "可配置" |
| Q4 | 测试 DB 策略 | **T1-T5 不连 DB；I1 用主开发 DB，pytest 标 skipif** | "上 testcontainers" / "完全不测 DB" |
| Q5 | Phase 字段类型 | **TEXT(32) + CheckConstraint** | "用 PostgreSQL ENUM 类型" |
| Q6 | UUID 生成位置 | **server_default=gen_random_uuid()** | "Python uuid4 默认" |
| Q7 | docker-compose.yml 位置 | **项目根**（全栈共享） | "放 backend/" |
| Q8 | DB user/pass | **prar/prar**（开发默认，生产 .env 覆盖） | "改用别的" |
| Q9 | DB 名 | **prar_agent** | "改名" |

如以上 9 项主人无异议，请直接回复 `APPROVED`，我立即按本设计落代码并 commit。

## 已做的设计决策（记录依据）

| 决策 | 理由 |
|------|------|
| `db/` 是 `app/` 的子包，不是顶级 sibling | 与 Task 01 的 `src/app/` 包结构一致，避免 import 路径割裂 |
| Base 用 `MetaData(naming_convention=...)` | alembic auto-gen 出可控约束/索引名，rename 列时 migration 不爆炸 |
| `metadata_json` 而非 `metadata` 字段名 | SQLAlchemy declarative 的 `metadata` 是保留属性 |
| Phase 用 TEXT+CHECK 而非 ENUM | enum 加值要 ALTER TYPE，CHECK 直接 drop+create 简单 |
| `gen_random_uuid()` server-side | PG 原子，比 Python uuid4 默认少一次 round-trip；client 不用关心 |
| FK ON DELETE：plan/comment CASCADE，memory SET NULL | 删 session 应连带 plan/comment（一致性）；memory 是跨 session 复用的，应保留只断引用 |
| HNSW 索引选 `vector_cosine_ops` | embedding similarity 默认 cosine（OpenAI / BGE 都常用） |
| alembic env.py async 模式 | SQLAlchemy 用 async，env.py 也用 async 才能复用 driver；offline mode 直接 raise（用不到） |
| 第一份 migration 顶部手工加 `CREATE EXTENSION` | auto-gen 不会主动加扩展；放在第一行确保后续 Vector 列创建可工作 |
| `make db-makemigration MSG=...` 用 Make 变量传参 | 比 `MSG=foo make db-makemigration` 略友好；统一在 Makefile 内 |
| 不在本 task 加 `get_db` dependency / async session factory | YAGNI——Task 03 起才有路由层消费 DB，那时再加更准确 |
| 不在本 task 加 testcontainers | T1-T5 introspection 不连 DB 已够；真集成测试本机 docker 起 DB 也够；CI 化留 M5+ |

---

**Q1-Q9 已锁定默认，主人审阅整体方案，回 `APPROVED` 即开始落代码 + 跑测试 + commit（设计与代码同 commit）。**
