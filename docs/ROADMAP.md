# PRAR-Agent 开发路线图

> 4 周 MVP，每个里程碑必须可演示。每个任务编号 `NN` 对应 `docs/design/NN-<topic>.md` 详细设计文件名。
> 任务总数 27，重排后分布：M1=10 / M2=4 / M3=7 / M4=6。

## M0 准备阶段（已完成 ✅）

- [x] 概要设计冻结（`docs/DESIGN.md`）
- [x] 工作流约定（`docs/WORKFLOW.md`）
- [x] 项目级 Claude 配置（`.claude/settings.json`）
- [x] git 仓库初始化

---

## M1 — Skeleton + Plan 生成（Week 1）

**目标**：UI 输入 init request → 流式看到完整可读 plan 文档（含决策题可点 + 自审过的内容）

| NN | 任务 | 产出 |
|----|------|------|
| 01 | 后端项目骨架（FastAPI + uv + pydantic v2 + 配置加载） | `backend/` 可启动，`/health` 通过 |
| 02 | PostgreSQL + pgvector 容器化 + `db/models.py` 初版（session/plan/memory/comment 表） | `docker compose up` + alembic up to head |
| 03 | 状态机 `core/state_machine.py` + 单元测试 | 6 phase + 转移合法性表 + 100% 覆盖 |
| 04 | LiteLLM 路由 `llm/router.py` + 多模型 schema 归一化层 | Claude / GPT 两家结构化输出走通 |
| **05** | **Logging / 链路追踪基建**（structlog + JSONL + `request_id` 串全链路） | LLM call / tool exec / state transition 全可追溯 |
| **06** | **shared/schema.json 自动生成管道**（pydantic `model_json_schema()` → 前端 `json-schema-to-ts`） | `make gen-schema` 一键同步前后端契约 |
| 07 | Plan 引擎 `core/plan_engine.py`（Planner prompt + **Critic 内嵌自审** + **LTM 接口存根 `ltm_recall=[]`**） | 给 init request 生成自审过的结构化 Plan JSON |
| **08** | **基础 WebSocket 流式管道**（SSE-over-WS，按 ProseMirror node 边界 chunk） | 前端能流式收到 plan 节点 |
| 09 | 前端项目骨架（Vite + React + TS + Tiptap 基础） | `pnpm dev` 起来，能渲染纯 doc |
| 10 | Plan 文档渲染（Decision/Glossary/Step 三个自定义节点） + **Decision 答题闭环**（前端答 → 后端持久化 → 解锁推进） | M1 demo：流式看到 plan，答完决策题能解锁 ACTING |

**M1 验收**：
- 在 UI 输入"实现一个 X 功能"
- 实时流式看到完整 plan（含名词解释、决策题、步骤）
- 答完所有 blocking 决策题后"进入 Action"按钮可点
- 全程日志可查（request_id 串起 LLM call + state transitions）

---

## M2 — Plan Review 循环（Week 2）

**目标**：用户评论 → 真正改 plan → 看到 v1 → v2 diff

| NN | 任务 | 产出 |
|----|------|------|
| 11 | `AnchorMark` + `CommentThread` extension（侧边栏 UI） | 用户可选中文本留 comment，评论持久化 |
| 12 | Review Merger `core/review_merger.py` + merger prompt | comments + plan v{N} → plan v{N+1}，每条评论附 accept/reject/partial 理由 |
| 13 | Plan 版本管理 + 前端 diff 视图（`.plan/v{N}.json` 落盘 + 版本切换） | 能看到 v1 → v2 节点级 diff |
| 14 | 评论锚定算法（anchor_id 直查 + fuzzy match `quote+context`）+ "悬空评论" UI 状态 | 改 plan 后评论位置不丢；命中率 < 0.7 提示重新指认 |

**M2 验收**：完整跑一轮 Plan v1 → 留 3 条评论 → 触发 Review Merger → Plan v2 真的改变、diff 视图可见、评论位置不丢。

---

## M3 — Action + Tool 框架（Week 3）

**目标**：plan 通过后能跑出结果，每步落 git

| NN | 任务 | 产出 |
|----|------|------|
| 15 | Tool ABC + registry (`tools/base.py`, `tools/registry.py`) | 注册/查询工具，function-calling schema |
| 16 | **本地 subprocess 沙箱** `tools/sandbox.py`（rlimit + 超时 + 工作目录隔离 + 默认禁网） | MVP 级隔离，不依赖 Docker |
| 17 | 内置工具：`shell` / `fs.read` / `fs.write` | 三个最小可用工具 |
| 18 | Action Dispatcher + ReAct loop (`core/action_dispatcher.py`) | step 能被执行，think→call_tool→observe |
| 19 | **工具输出流式**（复用 M1 的 SSE 管道，扩展 `tool.stdout` / `tool.exit` 事件类型） | 前端实时看到 stdout 流 |
| 20 | Git checkpoint (`core/checkpoint.py` + pygit2) | 每 step 一 commit，message 固定格式 |
| 21 | 前端 Action 输出面板（VSCode terminal/log 风格） | 输出可视、可滚动、可复制 |

**M3 验收**：批准后的 plan 让 agent 在 sandbox 里跑 shell 命令并把结果实时展示，每 step 自动 commit。

---

## M4 — 长期记忆 + 局部回滚（Week 4）

**目标**：第二次同类 session 看到记忆生效；失败可局部 rerun

> M1 已埋好 `ltm_recall` 接口存根，M4 只填实现，不动 M1 接口。

| NN | 任务 | 产出 |
|----|------|------|
| 22 | pgvector 接入 + Embedding 服务（默认 OpenAI / 可换本地 BGE） | 向量写入/检索 |
| 23 | 长期记忆三层 (`memory/long_term.py`)：episodic / semantic / procedural | session DONE 时 episodic 写入 |
| 24 | Consolidator 后台任务（APScheduler + 衰减/合并/提炼） | 定时跑通，semantic 从多个 episodic 提炼 |
| 25 | LTM_RECALL 实际注入 Planner（填实 M1 留好的接口存根） | 第二次相似 session 能看到记忆使用 |
| 26 | 局部 rerun（`git revert <step_commit>` + 重跑指定 step + state machine 回退） | 失败能修能重，git 历史干净 |
| 27 | Action Review UI + 用户评论 → 触发 rerun / 改 plan | 闭环 |

**M4 验收**：
- 第一次 session DONE → 第二次提相似需求 → plan 能引用历史决策（日志可证 LTM 命中）
- 某 step 失败后，UI 上点"重跑"能真重跑且 git 干净

---

## 调整说明（vs. 初版）

本 ROADMAP 在 2026-05-03 经议题 1-7 review 后调整。原初版 7+7+7+6 重排为 10+4+7+6，要点：

| 议题 | 改动 |
|------|------|
| 1. Critic 必跑 | 从 M2 (#11) 前移到 M1 (#07)，与 plan_engine 一体 |
| 2. Decision 答题闭环 | 从 M2 (#10) 合并入 M1 (#10)，与 plan 渲染一体 |
| 3. WebSocket 流式 | 从 M3 (#19) 拆出基础管道到 M1 (#08)，工具输出流式留 M3 (#19) |
| 4. LTM 接口存根 | M1 (#07) 即预留 `ltm_recall` 参数，M4 只填实现 |
| 5. Docker 沙箱 | 拆为本地 subprocess (M3 #16) + Docker 沙箱（M5+） |
| 6. Logging 基建 | 新增 M1 (#05) |
| 7. shared schema 自动生成 | 新增 M1 (#06) |

---

## M5+ 后续（不进 MVP）

- Tauri 桌面壳
- **Docker 沙箱**（议题 5 拆出的 16b，生产级隔离）
- 更多内置工具（git / npm / pytest / browser-use）
- Plan 模板库
- 多模型成本对比看板
- 团队协作（暂不做，需求边界已定单用户）
