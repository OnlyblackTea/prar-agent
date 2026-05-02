# PRAR-Agent 开发路线图

> 4 周 MVP，每个里程碑必须可演示。每个任务编号 `NN` 即对应 `docs/design/NN-<topic>.md` 详细设计文件名。

## M0 准备阶段（已完成 ✅）

- [x] 概要设计冻结（`docs/DESIGN.md`）
- [x] 工作流约定（`docs/WORKFLOW.md`）
- [x] git 仓库初始化

---

## M1 — Skeleton + Plan 生成（Week 1）

**目标**：能输入 init request → 看到生成的 plan 文档

| NN | 任务 | 产出 |
|----|------|------|
| 01 | 后端项目骨架（FastAPI + uv + pydantic v2 + 配置加载） | `backend/` 可启动，`/health` 通过 |
| 02 | PostgreSQL + pgvector 容器化 + `db/models.py` 初版（session/plan/memory 表） | `docker compose up` + alembic up to head |
| 03 | 状态机 `core/state_machine.py` + 单元测试 | 6 个 phase + 转移合法性表 |
| 04 | LiteLLM 路由 `llm/router.py` + 多模型 schema 归一化层 | Claude/GPT 两家结构化输出走通 |
| 05 | Plan 引擎 `core/plan_engine.py` + Planner prompt | 给 init request 能生成结构化 Plan JSON |
| 06 | 前端项目骨架（Vite + React + TS + Tiptap 基础） | `pnpm dev` 起来，能渲染纯 doc |
| 07 | Plan 文档展示（只读，不含交互节点） | 后端返回 plan → 前端渲染 |

**M1 验收**：在 UI 输入"实现一个 X 功能"，看到完整 plan 文档（含解释/决策题/步骤），但不能互动。

---

## M2 — Plan Review 循环（Week 2）

**目标**：能用评论真正改 plan

| NN | 任务 | 产出 |
|----|------|------|
| 08 | Tiptap 自定义节点：`DecisionNode` / `GlossaryNode` / `StepNode` | 三种节点可渲染 |
| 09 | `AnchorMark` + `CommentThread` extension（侧边栏） | 用户可选中文本留 comment |
| 10 | Decision 答题闭环（前端答 → 后端持久化 → 解锁推进） | 决策题阻塞机制工作 |
| 11 | Critic pass `core/critic.py` + critic prompt | Plan 生成后自动跑一轮 self-critique |
| 12 | Review Merger `core/review_merger.py` + merger prompt | comments + plan v{N} → plan v{N+1} |
| 13 | Plan 版本管理 + diff 视图 (`.plan/v{N}.json`) | 前端能看到 v1 → v2 的变化 |
| 14 | 评论锚定算法 + "悬空评论" UI 状态 | 改 plan 后评论位置不丢 |

**M2 验收**：完整跑一轮 Plan → Comment → Plan v2，用户的评论真的改变了下一版 plan。

---

## M3 — Action + Tool 框架（Week 3）

**目标**：plan 通过后能跑出结果

| NN | 任务 | 产出 |
|----|------|------|
| 15 | Tool ABC + registry (`tools/base.py`, `tools/registry.py`) | 注册/查询工具 |
| 16 | Docker 沙箱执行器 (`tools/sandbox.py`) | 容器化运行，禁网/限额 |
| 17 | 内置工具：`shell` / `fs.read` / `fs.write` | 三个最小可用工具 |
| 18 | Action Dispatcher + ReAct loop (`core/action_dispatcher.py`) | step 能被执行 |
| 19 | WebSocket 流式输出 (`api/ws.py`) | 前端实时看到工具输出 |
| 20 | Git checkpoint (`core/checkpoint.py` + pygit2) | 每 step 一 commit |
| 21 | 前端 Action 输出面板（VSCode 风格 terminal/log） | 输出可视 |

**M3 验收**：批准后的 plan 能让 agent 在沙箱里跑 shell 命令并把结果展示出来，每步落 git。

---

## M4 — 长期记忆 + 局部回滚（Week 4）

**目标**：第二次同类 session 看到记忆生效；失败可局部 rerun

| NN | 任务 | 产出 |
|----|------|------|
| 22 | pgvector 接入 + Embedding 服务（默认 OpenAI / 可换本地） | 向量写入/检索 |
| 23 | 长期记忆三层 (`memory/long_term.py`) | episodic/semantic/procedural 写入 |
| 24 | Consolidator 后台任务（衰减/合并/提炼） | cron 跑通 |
| 25 | LTM_RECALL 注入 Planner prompt | 第二次 session 能看到记忆使用 |
| 26 | 局部 rerun（`git revert` + 重跑指定 step） | 失败能修能重 |
| 27 | Action Review UI + 用户评论 → 触发 rerun / 改 plan | 闭环 |

**M4 验收**：
- 第一次 session 完成后，第二次提相似需求，plan 能引用历史决策
- 某 step 失败后，UI 上点"重跑"能真重跑且 git 干净

---

## M5+ 后续（不进 MVP）

- Tauri 桌面壳
- 更多内置工具（git / npm / pytest / browser-use）
- Plan 模板库
- 多模型成本对比看板
- 团队协作（暂不做，需求边界已定单用户）
