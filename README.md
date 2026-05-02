# PRAR-Agent

**P**lan → **R**eview → **A**ction → **R**eview 循环驱动的 Agent 框架。

模拟"经理 ↔ 程序员"协作模式：大模型先生成可评论的计划文档，用户通过决策题/留 comment 完善方案，批准后执行可插拔工具，结果再次进入用户 review 循环。

## 状态

🚧 **早期设计阶段**。尚无可运行代码。

## 文档入口

- [`docs/DESIGN.md`](docs/DESIGN.md) — 完整概要设计（架构、数据模型、模块拆分、Schema、Prompt）
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — 4 周 MVP 开发计划与里程碑
- [`docs/WORKFLOW.md`](docs/WORKFLOW.md) — 编码前详细设计 → 审批 → 实施 → 提交 的工作流约定
- [`CLAUDE.md`](CLAUDE.md) — 本项目对 Claude Code 的特异指令

## 核心理念

| 角色 | 职责 |
|------|------|
| **Manager**（Planner LLM） | 拆解需求、生成可读计划、提决策题、不动手 |
| **Programmer**（Executor LLM） | 收到批准后的 step，调用工具，汇报结果 |
| **用户** | 通过批注/答题/批准按钮控制全流程 |

不依赖任何模型微调，纯框架代码 + 结构化输出实现。
