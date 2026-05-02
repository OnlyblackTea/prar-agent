# PRAR-Agent 协作工作流

> 主人 ↔ Claude 的协作纪律。**违反此文件 = 直接打断重来**。

## 1. 单任务循环（Design → Approve → Code → Commit）

每个开发任务必须严格走以下 4 步，**不允许跳过、不允许并行多任务**：

```
┌────────────────────────────────────────────────────────────────┐
│ Step 1. 详细设计                                                │
│   → Claude 在 docs/design/NN-<topic>.md 写详细设计              │
│   → 包含：目标、接口、数据流、文件清单、测试清单、风险         │
│                                                                 │
│ Step 2. 审批                                                    │
│   → 主人阅读后，回复 "APPROVED" 或具体修改意见                  │
│   → 未收到 "APPROVED" 字样，禁止开写任何 .py/.ts/.sql 等代码    │
│                                                                 │
│ Step 3. 实施编码                                                │
│   → 严格按详细设计写代码                                        │
│   → 偏离设计要先停下，更新设计文档，重走 Step 2                 │
│                                                                 │
│ Step 4. git 提交                                                │
│   → 编码完成立即 git add + git commit                           │
│   → commit 必须同时包含：详细设计文件 + 代码文件                │
│   → message 格式见 §3                                           │
└────────────────────────────────────────────────────────────────┘
```

## 2. 详细设计文件规范

### 2.1 路径与命名

`docs/design/NN-<topic>.md`，其中：

- `NN` = 与 `docs/ROADMAP.md` 的任务编号一致（两位数字，前补零）
- `<topic>` = kebab-case 简短主题，例：`01-backend-skeleton.md`、`16-docker-sandbox.md`

### 2.2 必须包含的章节

```markdown
# NN. <任务标题>

## 目标
- 一句话目标
- 验收标准（如何判断完成）

## 输入 / 输出
- 上游产物 (前置任务编号)
- 本任务交付物清单

## 接口设计
- 函数签名 / API endpoint / DB schema / Schema 字段
- 关键数据流 (mermaid / 文字皆可)

## 文件清单
- 列出本任务**新增/修改**的所有文件路径与作用

## 实施步骤
- 编号步骤，每步可独立验证

## 测试清单
- 单元测试要 cover 哪些 case
- 边缘情况列表
- 集成测试入口（命令）

## 风险与未决
- 已知不确定项
- 需要主人决策的开放问题（如有则**必须在 Step 2 之前问清**）
```

## 3. Git 提交规范

### 3.1 Commit message 格式

```
<type>(<scope>): <短描述>

<可选正文：动机、影响、注意事项>

Refs: docs/design/NN-<topic>.md
```

- **type**：`feat` / `fix` / `refactor` / `docs` / `test` / `chore` / `build`
- **scope**：`backend` / `frontend` / `db` / `tools` / `llm` / `memory` / `ci` / `docs`
- **正文**：超过 1 文件改动时建议带，单纯 typo 可省

### 3.2 范例

```
feat(backend): 实现状态机与单元测试 (M1-03)

- core/state_machine.py: 6-phase Phase enum + TRANSITIONS 表
- tests/test_state_machine.py: 覆盖合法/非法转移
- 详细设计见 docs/design/03-state-machine.md

Refs: docs/design/03-state-machine.md
```

### 3.3 提交粒度

- **一个详细设计 = 一个 commit**（设计 + 代码 + 测试一起提交）
- 任务过大时（>1 天工作量），先拆设计文档（`03a-*.md` / `03b-*.md`），每子任务一 commit
- **绝不允许**：积压未提交改动跨任务、混合多个无关任务、纯实验性 WIP 进 main

### 3.4 分支策略

- MVP 阶段单 `main` 分支，每 commit 必须可跑通已存在的测试
- M5+ 引入 `dev` + feature branch（暂不预留）

## 4. 编码动手前的强制确认

在动手写代码前，Claude 必须**显式确认**以下三件事：

1. **设计文件已存在且最新**：路径、章节齐全
2. **主人已 APPROVED**：在对话中找到批准信号
3. **环境就绪**：依赖已安装、DB 已起、相关上游任务已合入 main

任何一项未满足 → 停下问主人，**禁止猜测推进**。

## 5. 偏离设计的处理

如果实施过程中发现设计有问题：

1. **立刻停下**，不继续写代码
2. 在原设计文件追加 `## 设计变更 (YYYY-MM-DD)` 章节，说明：发现什么/原方案为什么不行/新方案
3. 把变更提给主人 review
4. 收到 `APPROVED` 后才能继续

## 6. 测试纪律

- 后端：每个 `core/` 模块函数都要有 pytest，覆盖率不强求但**关键状态机/锚定算法必须 100%**
- 前端：组件靠 Vitest + React Testing Library；Tiptap 节点至少有 render snapshot
- **修 bug 强制循环**：先写复现测试（红）→ 改代码（绿）→ 跑全套（无回归）

## 7. 禁止事项清单

- ❌ 跳过详细设计直接写代码
- ❌ 未收到 APPROVED 就开写
- ❌ 一次 commit 跨任务
- ❌ commit 不带 `Refs: docs/design/NN-*.md`（除 docs/chore 类）
- ❌ 偏离设计但不更新设计文件
- ❌ 写"将来可能用到"的扩展点
- ❌ 给已有代码做无关重构
- ❌ 用 `--no-verify` 绕过 hook
- ❌ 静默 catch 异常 / 加 fallback 让错误消失
