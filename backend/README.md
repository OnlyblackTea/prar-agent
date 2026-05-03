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

详见项目根 [`../docs/`](../docs/) 与 [`../CLAUDE.md`](../CLAUDE.md)。
