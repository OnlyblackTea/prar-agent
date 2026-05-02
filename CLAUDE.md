# CLAUDE.md — prar-agent

> 全局规范见 `~/.claude/CLAUDE.md`；本文件是 prar-agent 项目的所有特异约定。
> **本文件目的**：让任意时刻冷启动的 Claude 会话立即知道项目状态、纪律与下一步动作。

## TL;DR — 进项目必知 5 条

1. **PRAR 4 阶段循环驱动的 Agent 框架，不微调模型**。架构定义见 [`docs/DESIGN.md`](docs/DESIGN.md)。
2. **强制工作流**：写代码前必先在 `docs/design/NN-*.md` 写详细设计 → 等主人 `APPROVED` → 写代码 → git commit（设计+代码同 commit）。详见 [`docs/WORKFLOW.md`](docs/WORKFLOW.md)。
3. **绝对禁止**：跳过详细设计、未 APPROVED 就动代码、跨任务的 commit、`--no-verify` 绕 hook、静默 catch 异常、写"将来用得上"的扩展点。
4. **当前阶段**：M0 完成（仓库初始化 + 概要冻结）；下一任务 = ROADMAP M1-01（后端骨架）。进度看 [`docs/ROADMAP.md`](docs/ROADMAP.md)。
5. **技术栈硬约束（不可换）**：FastAPI(backend) + React/Vite/Tiptap(frontend) + PostgreSQL/pgvector + LiteLLM + Docker 沙箱。

## 进项目 30 秒诊断

```bash
git -C /data/claude/li-quan-zhou/prar-agent log --oneline -10
ls /data/claude/li-quan-zhou/prar-agent/docs/design/ 2>/dev/null || echo "尚无详细设计"
```

- commit 流水 → 已完成什么任务（commit 标题里带 `(M1-NN)` 编号）
- `docs/design/` → 哪些设计已落地（在 commit 里 = 已 APPROVED 已实施）

## 文档地图

| 文件 | 作用 | 何时读 |
|------|------|--------|
| `docs/DESIGN.md` | 概要架构（冻结） | 第一次进项目必读 |
| `docs/ROADMAP.md` | 4 周 27 任务路线图 | 决定下一步做什么时 |
| `docs/WORKFLOW.md` | 协作纪律 | 每次开始新任务前过一遍 |
| `docs/design/NN-*.md` | 单次任务详细设计 | 准备实施编码时 |
| `.claude/settings.json` | 项目级 Claude 配置 | 一般不动；想加放行/屏蔽时改 |

## 项目级硬约束

- **后端**：Python 3.11+ + FastAPI + pydantic v2，包管理用 `uv`（fallback `pip --break-system-packages`）
- **前端**：React + TS + Vite + Tiptap + Monaco，包管理用 `pnpm`
- **数据库**：PostgreSQL 16 + pgvector，本机 `docker compose` 起
- **LLM**：默认 LiteLLM；至少接入 Claude Sonnet 4.6 + GPT 系列做 schema 兼容性 smoke test
- **沙箱**：所有 Action 工具默认在 Docker 容器执行，禁网，CPU/MEM/超时限额
- **跨包禁止**：frontend ↔ backend 不直接 import；backend `api/` 路由不直接调 LLM（必须经 `core/`）
- **prompt 必须存 `backend/llm/prompts/*.md`**，不许写死在 .py 里

## 写代码风格

继承全局 `~/.claude/rules/coding-style.md`；本项目额外要求：

- Python：所有公开函数 type hints；用 `pydantic v2` 表达 API 契约（不是 dataclass）
- TS：禁 `any`；状态机优先 `discriminated union`
- 抽象等到第 2 个真实实现才做（"抽象要求两次实证"）
- prompt 模板存 `backend/llm/prompts/*.md`，代码里只 import 渲染

## 常见踩坑预警

1. **不同 LLM 的 structured output 差异巨大** — schema 归一化必须在 `llm/router.py` 处理，业务层不许碰这层差异
2. **流式渲染富文本不要逐 token** — 按 ProseMirror node 边界 chunk（10-50 tokens/帧），否则前端 reflow 卡顿
3. **Plan 节点 ID 由框架后处理分配**（`dec_001/step_001/anc_xxx`），不让 LLM 自由生成
4. **测试要 mock LLM call**，用 `respx` / `pytest-asyncio`，禁止 CI 真调 API
5. **Comment 锚定永远有 edge case**，准备"悬空评论" UI 状态，别奢望 100% 自动匹配
6. **决策题 blocking 性硬编码**：第一轮全 blocking，二轮起用户手动降级，**不交给模型判定**
7. **状态机转移由框架触发**，LLM 无权决定 phase 切换（参见 `docs/DESIGN.md §5`）

## LLM 成本意识

本项目本身就要烧 token。开发期纪律：

- 单元测试：100% mock，禁止真调
- 集成 smoke test：用最便宜模型（Haiku / GPT-4o-mini），跑前确认必要性
- 端到端调试：主人指定模型；默认 Sonnet 4.6
- prompt 改动后：先在本地 fixture 上跑 diff 看输出变化，确认稳定再上真模型

## 优先调用的 skill / 主动避开的 skill

**优先**：

- `superpowers:writing-plans` / `:executing-plans` / `:test-driven-development` / `:systematic-debugging`
- `git-commit` / `git-workflow`
- `everything-claude-code:claude-api`（项目本身集成 Claude API，写 LLM 代码时翻一下）
- `everything-claude-code:python-review` / `:python-patterns` / `:python-testing`
- `everything-claude-code:frontend-design` / `:frontend-patterns`
- `everything-claude-code:postgres-patterns` / `:database-migrations` / `:docker-patterns`
- `everything-claude-code:mcp-server-patterns`（如果某天我们要发布 MCP）
- `everything-claude-code:security-review`（任何动 auth/输入解析的代码后跑一次）
- `everything-claude-code:silent-failure-hunter`（review 时跑）
- `claude-mem:mem-search`（找历史决策）

**主动避开**（已在 `.claude/settings.json` 屏蔽，不会被自动唤起，仅供查阅）：

- 所有 `pua:*`、`auto-harness:*`
- 其他语言栈相关：laravel/django/springboot/kotlin/swift/cpp/rust/golang/csharp/dotnet/perl/dart/flutter/android
- 行业特化：healthcare/customs/finance-billing/inventory/logistics/visa
- 商业/营销/媒体生成：投资人/邮件/IM/视频/social
- ECC 元工作流：claude-devfleet/santa-method/ralphinho/instinct/PRP（我们有自己的 docs/design/ 工作流）
- **gateguard**（事实强制门）— 已关，避免每次 Edit/Write 都要喂 facts

## .claude/ 目录速查

| 文件 | 作用 | 是否 commit |
|------|------|------------|
| `.claude/settings.json` | 项目级 Claude 配置（git 放行 + skill 屏蔽） | ✅ commit |
| `.claude/settings.local.json` | 个人本地覆盖（如本机 API key 路径） | ❌ gitignore |

## 常用命令（待项目骨架建立后填充）

```bash
# 后端 (TBD - Task 01 完成后补全)
cd backend && uv sync && uv run uvicorn app.main:app --reload

# 前端 (TBD - Task 06 完成后补全)
cd frontend && pnpm install && pnpm dev

# DB (TBD - Task 02 完成后补全)
docker compose up -d postgres

# 测试 (TBD)
cd backend && uv run pytest
cd frontend && pnpm test
```
