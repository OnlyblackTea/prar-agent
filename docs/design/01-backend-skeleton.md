# 01. 后端项目骨架

## 目标

- **一句话**：让 `backend/` 可启动 + `/health` 通过 pytest，奠定后续 Task 02-08 的代码挂靠点。
- **验收标准**（缺一不可）：
  1. `cd backend && uv sync` 成功（不报依赖冲突）
  2. `cd backend && make dev` 启动后 `curl localhost:8000/health` 返回 200 + 预期 JSON
  3. `cd backend && make test` 全绿，至少 2 个测试用例
  4. `cd backend && make lint` 无 ruff 警告
  5. `cd backend && make typecheck` 无 mypy strict 错误

## 输入 / 输出

**前置任务**：M0（已完成）。本任务不依赖任何代码前置。

**交付物清单**：

- `backend/` 顶层目录（src 布局 + tests）
- `pyproject.toml` 依赖固化（含 dev 组）
- 可启动的 FastAPI app 暴露 `/health`
- ≥2 个 pytest 用例验证 `/health` 行为与 schema
- 一键命令汇总（Makefile）

**不交付**（留给后续 task）：

- 任何数据库连接、SQLAlchemy 模型 → Task 02
- 状态机、core 业务逻辑 → Task 03
- LLM 调用、router → Task 04
- structlog / 链路追踪 → Task 05
- shared/schema 自动生成 → Task 06
- 任何业务路由（`api/session.py` 等）

## 接口设计

### 目录结构（src/ 布局）

```
backend/
├── src/
│   └── app/                      # 主包（被 uv 装为 editable）
│       ├── __init__.py           # 空，标记包
│       ├── main.py               # FastAPI() 实例 + 装载路由
│       ├── config.py             # pydantic-settings Settings
│       └── health.py             # /health 路由 (APIRouter)
├── tests/
│   ├── __init__.py
│   ├── conftest.py               # 共享 TestClient fixture
│   └── test_health.py
├── pyproject.toml
├── .python-version               # uv 选解释器用，内容: 3.12
├── .env.example                  # 环境变量模板
├── Makefile
└── README.md                     # 启动 3 行说明
```

> `app/health.py` 单独成模块（不是塞 main.py 里）是为了立下"路由按职责拆模块"的范式，后续 `api/` 子包不会破坏一致性。
>
> src/ 布局：`uv sync` 会自动 editable-install `src/app`，所以代码里仍然 `from app.main import app`，src/ 对调用方透明。

### `pyproject.toml` 关键片段

```toml
[project]
name = "prar-agent-backend"
version = "0.1.0"
description = "PRAR Agent backend - Plan/Review/Action/Review loop service"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "pydantic>=2.9.0",
    "pydantic-settings>=2.5.0",
]

[dependency-groups]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.24.0",
    "httpx>=0.27.0",
    "ruff>=0.7.0",
    "mypy>=1.13.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/app"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py312"
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "N", "UP", "B", "A", "RUF"]

[tool.mypy]
python_version = "3.12"
strict = true
mypy_path = "src"
```

### 核心代码 schema（落地时严格按此写）

#### `src/app/config.py` — Settings

```python
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "prar-agent-backend"
    app_version: str = "0.1.0"
    environment: str = "development"  # development | production
    log_level: str = "INFO"

    host: str = "0.0.0.0"
    port: int = 8000


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```

#### `src/app/health.py` — 路由

```python
from fastapi import APIRouter
from pydantic import BaseModel

from app.config import get_settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    environment: str


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    s = get_settings()
    return HealthResponse(
        status="ok",
        service=s.app_name,
        version=s.app_version,
        environment=s.environment,
    )
```

#### `src/app/main.py` — 装配

```python
from fastapi import FastAPI

from app.config import get_settings
from app.health import router as health_router


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title=s.app_name, version=s.app_version)
    app.include_router(health_router)
    return app


app = create_app()
```

> 用 `create_app()` 工厂模式而不是模块顶层直接 `app = FastAPI(...)` 是为了 pytest 时可以用不同 settings 创建独立实例（M2 后会用到）。

### `/health` API 契约

| 方法 | 路径 | 响应码 | 响应体 schema |
|------|------|--------|---------------|
| GET | `/health` | 200 | `{status: str, service: str, version: str, environment: str}` |

样例响应：

```json
{
  "status": "ok",
  "service": "prar-agent-backend",
  "version": "0.1.0",
  "environment": "development"
}
```

### Makefile 目标

| target | 命令 | 用途 |
|--------|------|------|
| `make sync` | `uv sync --all-groups` | 装依赖（含 dev 组），自动 editable-install `src/app` |
| `make dev` | `uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000` | 启动开发服务器 |
| `make test` | `uv run pytest -v` | 跑测试 |
| `make lint` | `uv run ruff check src tests` | lint |
| `make fmt` | `uv run ruff format src tests` | 格式化 |
| `make typecheck` | `uv run mypy src tests` | mypy strict |
| `make clean` | `rm -rf .venv .pytest_cache .mypy_cache .ruff_cache` | 清缓存 |

## 文件清单

| 路径 | 类型 | 说明 |
|------|------|------|
| `backend/pyproject.toml` | 新增 | 依赖 + ruff/mypy/pytest 配置 + hatch packages 指向 src/app |
| `backend/.python-version` | 新增 | `3.12` 单行 |
| `backend/.env.example` | 新增 | 5 个 env var 模板 |
| `backend/Makefile` | 新增 | 7 个 target |
| `backend/README.md` | 新增 | 3-5 行启动说明 |
| `backend/src/app/__init__.py` | 新增 | 空 |
| `backend/src/app/main.py` | 新增 | FastAPI 工厂 |
| `backend/src/app/config.py` | 新增 | Settings + get_settings() |
| `backend/src/app/health.py` | 新增 | /health APIRouter |
| `backend/tests/__init__.py` | 新增 | 空 |
| `backend/tests/conftest.py` | 新增 | client fixture |
| `backend/tests/test_health.py` | 新增 | 3 个测试用例 |

总计 12 个新文件，均在 `backend/` 下。

## 实施步骤

每步独立可验证。

1. **建目录**：`mkdir -p backend/{src/app,tests}`，touch `__init__.py` 占位
2. **写 `pyproject.toml`**：固化 `fastapi==0.115+ / uvicorn[standard] / pydantic==2.9+ / pydantic-settings==2.5+`，dev 组加 `pytest / pytest-asyncio / httpx / ruff / mypy`，hatch packages 指向 `src/app`
3. **写 `.python-version` / `.env.example` / `Makefile` / `README.md`**
4. **写 `src/app/config.py`**：Settings + get_settings
5. **写 `src/app/health.py`**：APIRouter + HealthResponse
6. **写 `src/app/main.py`**：create_app 工厂 + include_router
7. **写 `tests/conftest.py`**：`@pytest.fixture client` 用 `TestClient(app)`
8. **写 `tests/test_health.py`**：见下文测试清单
9. **`cd backend && uv sync --all-groups` 验证依赖装得上 + editable-install `src/app` 成功**
10. **`make test` 验证全绿**
11. **`make lint && make typecheck` 验证零警告**
12. **`make dev` 启动 + `curl localhost:8000/health` 人眼确认 JSON 正确**

每完成一步 → 在 commit message 之前快速 sanity check。出问题立刻停下问主人。

## 测试清单

### 单元测试（必须 cover）

| # | 测试 | 断言 |
|---|------|------|
| T1 | `test_health_returns_200_and_status_ok` | status_code == 200 + body["status"] == "ok" |
| T2 | `test_health_response_schema_complete` | body keys 严格 == {status, service, version, environment} |
| T3 | `test_health_reflects_settings` | 修改 env `APP_NAME` 后清 `get_settings` 缓存重启 client，body["service"] 跟随变化 |

T3 实现：`monkeypatch.setenv("APP_NAME", "test-app")` + `get_settings.cache_clear()` + 重新构造 `TestClient(create_app())`。

### 边缘情况

- `/health` 不需要鉴权（即使未来有 auth middleware 也要豁免）→ 本 task 暂无 auth，留作 M5+ 注意事项
- 启动时缺 `.env` 文件应使用默认值（不抛错）→ T1 实际就在测这个

### 集成测试入口

```bash
cd backend && make test
```

## 风险与未决

### 已识别风险

| 风险 | 缓解 |
|------|------|
| uv 在主人当前机器未安装 | README 加 `curl -LsSf https://astral.sh/uv/install.sh \| sh` 安装提示 |
| Python 3.12 在某些生产镜像不可用 | 本 task 仅本机开发；生产部署 Task 留 M5+，到时再决定 base image |
| FastAPI 0.115 + pydantic 2.9 兼容（一般稳定，但锁版本） | pyproject 用 `>=` 而不是 `==`，uv.lock 锁实际版本 |
| pytest-asyncio 配置坑 | `asyncio_mode = "auto"` 简化；本 task 实际没异步 endpoint，但提前配置避免 Task 04 起接 LLM 时再补 |
| src/ 布局首次跑 pytest 找不到 `app` 包 | uv sync 自动 editable-install；如果离开 uv 直接 `pip install -e .` 也能复现 |

### 已决策（主人 2026-05-03 给定）

| # | 项目 | 决策 |
|---|------|------|
| Q1 | Python 版本 | **3.12** |
| Q2 | 包名 | **`app`** |
| Q3 | 端口 | **8000** |
| Q4 | 任务运行器 | **Makefile** |
| Q5 | 项目布局 | **src/ 布局** |
| Q6 | 包管理 | **uv** |

无剩余开放问题，主人 `APPROVED` 即可落代码。

## 已做的设计决策（记录依据）

| 决策 | 理由 |
|------|------|
| `create_app()` 工厂模式 | 测试时可注入不同 settings；后续 lifespan、middleware 安装也更干净 |
| `app/health.py` 独立模块 | 立下"按职责拆路由模块"的范式，后续 `api/` 子包不破坏一致性 |
| `@lru_cache` 包装 `get_settings()` | 避免每个 request 重读 .env；测试用 `cache_clear()` 重置 |
| `Settings.model_config.extra="ignore"` | 容忍 .env 中无关 var（如其他 task 加的 DATABASE_URL 不会让 Task 01 测试挂掉） |
| **采纳 src/ 布局**（主人决定） | 三个收益：① 测试不会无意中 import 仓库根的脏文件；② 强制 editable-install，import 路径与生产一致；③ 与 PyPA 现代推荐对齐 |
| **Python 3.12**（主人决定） | type 系统改进 + per-interpreter GIL 路线 + 新 generic syntax；本机 / 容器都广泛可用 |
| 不在本 task 加 CORS | CORS 只在前端联调时需要；Task 09 起前端骨架时一起加 |
| 不在本 task 加 logging middleware | 等 Task 05 做 structlog 基建时统一处理 |
| 选 `hatchling` build backend | 比 setuptools 轻；uv 推荐；pyproject 配置最少；与 src 布局原生兼容 |
| ruff 启用 `select = ["E", "F", "W", "I", "N", "UP", "B", "A", "RUF"]` | 主流严格集合，不会抓鸡毛 |
| ruff `src = ["src", "tests"]` | 让 isort 正确识别 first-party 包，避免 `app.config` 被排到第三方区 |
| mypy 启用 strict 模式 + `mypy_path = "src"` | 全局 type hints 要求（CLAUDE.md 硬约束）需要 strict 才有意义；mypy_path 让 strict 找得到 src 下的 app 包 |

---

**Q1-Q6 已锁定，主人审阅整体方案，回 `APPROVED` 即开始落代码 + 跑测试 + commit（设计与代码同 commit）。**
