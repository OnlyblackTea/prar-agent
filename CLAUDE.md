# CLAUDE.md — prar-agent

全局规范见 `~/.claude/CLAUDE.md`；本文件只放本项目特异约定。

## 项目定位

PRAR (Plan-Review-Action-Review) Agent 框架。Manager-Programmer 协作范式，纯框架实现，不微调模型。

详见 [`docs/DESIGN.md`](docs/DESIGN.md)。

## 强制工作流（不可破坏）

详细规则见 [`docs/WORKFLOW.md`](docs/WORKFLOW.md)，关键约束：

1. **任何编码前**必须先在 `docs/design/<NN>-<topic>.md` 写出详细设计，**等待主人 `APPROVED` 字样回复**才能开写代码
2. **每次编码完成后**立即 `git add` + `git commit`，不允许积压未提交改动跨任务
3. **commit message 格式**（见 WORKFLOW.md）：`<type>(<scope>): <短描述>`
4. **设计文档 = 第一公民**：先有 `docs/design/NN-foo.md` → 再有代码 → 提交时一起进 commit

## 项目级硬约束

- **后端 = Python 3.11+ + FastAPI**；**前端 = React + TS + Tiptap + Vite**
- **包管理**：后端 `uv` 优先（fallback `pip --break-system-packages`）；前端 `pnpm`
- **数据库**：本机开发用 PostgreSQL 16 + pgvector（容器化）
- **LLM**：默认 LiteLLM；至少接入 Claude Sonnet 4.6 + GPT 系列两家做 schema 兼容性测试
- **沙箱**：所有 Action 工具默认在 Docker 容器中执行，禁网，资源限额（CPU/MEM/超时）
- **不允许**：跨 frontend/backend 直接 import；后端禁止任何 LLM call 出现在 API 路由层（必须经 `core/` 路由）

## 路径速查

| 路径 | 角色 |
|------|------|
| `docs/DESIGN.md` | 概要架构（不轻易改） |
| `docs/ROADMAP.md` | 阶段路线图 |
| `docs/WORKFLOW.md` | 协作工作流 |
| `docs/design/NN-*.md` | 单次编码任务的详细设计（编码前撰写） |
| `backend/` | FastAPI 后端 |
| `frontend/` | React 前端 |
| `shared/` | 前后端共享 schema/类型 |

## 写代码风格

继承全局 `~/.claude/rules/coding-style.md`；本项目额外要求：

- 后端 Python：所有公开函数必须有 type hints；用 `pydantic v2` 而不是 dataclass 表达 API 契约
- 前端 TS：禁用 `any`；优先 `discriminated union` 表达状态机
- **绝不写"将来可能有用"的扩展点**——抽象等到第 2 个真实实现再做
