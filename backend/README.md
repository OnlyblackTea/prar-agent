# prar-agent-backend

PRAR Agent backend service.

## 启动

```bash
# 首次需要 uv：
curl -LsSf https://astral.sh/uv/install.sh | sh

uv sync --all-groups
make dev          # → http://localhost:8000/health
make test
```

## 数据库

PostgreSQL 16 + pgvector，docker-compose.yml 在项目根：

```bash
make db-up                    # 起 postgres 容器（默认端口 15432）
make db-init                  # 跑 alembic upgrade head
make db-shell                 # 进 psql
make db-makemigration MSG=xxx # 生成新 migration
make db-down                  # 停容器（数据卷保留）
```

详见项目根 [`../docs/`](../docs/) 与 [`../CLAUDE.md`](../CLAUDE.md)。
